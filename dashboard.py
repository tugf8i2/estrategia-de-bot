#!/usr/bin/env python3
"""Panel local, sin dependencias extra, para la contabilidad del bot."""

import csv
import json
import os
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA_DIR = Path(os.environ.get("SIGNAL_DATA_DIR", "."))
HISTORY_FILE = Path(
    os.environ.get("HISTORY_FILE", str(DATA_DIR / "historial_senales_vantage_xauusd.csv"))
)
STATE_FILE = Path(
    os.environ.get("STATE_FILE", str(DATA_DIR / "signal_state_vantage_xauusd.json"))
)
HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))
USD_PER_POINT = float(os.environ.get("USD_PER_POINT", "0") or 0)
STATS_FROM_DATE = os.environ.get("STATS_FROM_DATE", "2026-09-25").strip()
STATS_TIMEZONE = timezone(timedelta(hours=-5))


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def visible_from_baseline(row):
    if not STATS_FROM_DATE:
        return True
    try:
        baseline = date.fromisoformat(STATS_FROM_DATE)
        closed_at = datetime.fromisoformat((row.get("cerrada_en") or "").replace("Z", "+00:00"))
        if closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=STATS_TIMEZONE)
        return closed_at.astimezone(STATS_TIMEZONE).date() >= baseline
    except (TypeError, ValueError):
        return False


def load_data():
    rows = []
    if HISTORY_FILE.exists():
        with HISTORY_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    rows = [row for row in rows if visible_from_baseline(row)]

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    wins = losses = breakeven = 0
    gross_profit = gross_loss = 0.0
    strategies = {}
    curve = []

    for row in rows:
        points = number(row.get("puntos"))
        result = row.get("resultado", "")
        if result == "GANADORA":
            wins += 1
        elif result == "PERDEDORA":
            losses += 1
        else:
            breakeven += 1
        if points > 0:
            gross_profit += points
        elif points < 0:
            gross_loss += abs(points)
        equity += points
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        curve.append(round(equity, 4))
        name = row.get("estrategia") or "sin_dato"
        bucket = strategies.setdefault(name, {"trades": 0, "wins": 0, "losses": 0, "points": 0.0})
        bucket["trades"] += 1
        bucket["wins"] += int(result == "GANADORA")
        bucket["losses"] += int(result == "PERDEDORA")
        bucket["points"] += points

    decided = wins + losses
    state = {"active": False}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    return {
        "total": len(rows), "wins": wins, "losses": losses, "breakeven": breakeven,
        "win_rate": 100 * wins / decided if decided else 0,
        "gross_profit": gross_profit, "gross_loss": gross_loss,
        "net": gross_profit - gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "avg_win": gross_profit / wins if wins else 0,
        "avg_loss": gross_loss / losses if losses else 0,
        "max_drawdown": max_drawdown, "curve": curve,
        "strategies": strategies, "rows": rows[-100:][::-1], "state": state,
        "usd_per_point": USD_PER_POINT,
    }


HTML = r'''<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Contabilidad Coffee Fly</title><style>
:root{color-scheme:dark;--bg:#0b1020;--card:#151c30;--muted:#92a0bb;--good:#35d07f;--bad:#ff6577;--line:#27314c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#eef2ff;font:15px system-ui;padding:24px}
main{max-width:1200px;margin:auto}h1{margin:0 0 4px}.sub{color:var(--muted);margin-bottom:22px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
.label{color:var(--muted);font-size:12px;text-transform:uppercase}.value{font-size:25px;font-weight:750;margin-top:5px}.good{color:var(--good)}.bad{color:var(--bad)}
.wide{margin-top:14px}canvas{width:100%;height:230px}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:9px;border-bottom:1px solid var(--line);white-space:nowrap}.scroll{overflow:auto}
.pill{display:inline-block;padding:4px 9px;border-radius:99px;background:#25304c}.active{background:#174d37;color:#8ff0bb}@media(max-width:600px){body{padding:12px}.value{font-size:21px}}
</style></head><body><main><h1>☕ Coffee Fly</h1><div class="sub">Contabilidad del bot · actualización automática cada 60 segundos</div>
<section class="grid" id="cards"></section><section class="card wide"><div class="label">Curva acumulada (puntos)</div><canvas id="curve"></canvas></section>
<section class="card wide"><div class="label">Rendimiento por estrategia</div><div class="scroll"><table id="strategies"></table></div></section>
<section class="card wide"><div class="label">Últimas operaciones</div><div class="scroll"><table id="trades"></table></div></section></main>
<script>
const n=(x,d=2)=>Number(x||0).toLocaleString('es-CO',{minimumFractionDigits:d,maximumFractionDigits:d});
function card(label,value,cls=''){return `<div class="card"><div class="label">${label}</div><div class="value ${cls}">${value}</div></div>`}
function draw(values){const c=document.querySelector('#curve'),x=c.getContext('2d'),d=devicePixelRatio||1,w=c.clientWidth,h=c.clientHeight;c.width=w*d;c.height=h*d;x.scale(d,d);x.clearRect(0,0,w,h);if(!values.length){x.fillStyle='#92a0bb';x.fillText('Todavía no hay operaciones',15,30);return}let min=Math.min(0,...values),max=Math.max(0,...values);if(max===min)max=min+1;x.strokeStyle='#27314c';x.beginPath();x.moveTo(0,h-(0-min)/(max-min)*h);x.lineTo(w,h-(0-min)/(max-min)*h);x.stroke();x.strokeStyle='#35d07f';x.lineWidth=2;x.beginPath();values.forEach((v,i)=>{let px=values.length===1?w/2:i*w/(values.length-1),py=h-10-(v-min)/(max-min)*(h-20);i?x.lineTo(px,py):x.moveTo(px,py)});x.stroke()}
async function load(){let d=await fetch('/api/stats').then(r=>r.json()),money=d.usd_per_point?` · $${n(d.net*d.usd_per_point)}`:'';document.querySelector('#cards').innerHTML=card('Estado',d.state.active?'Operación activa':'Esperando señal',d.state.active?'good':'')+card('Operaciones',d.total, '')+card('Ganadas',d.wins,'good')+card('Perdidas',d.losses,'bad')+card('Break-even',d.breakeven)+card('Win rate',n(d.win_rate,1)+'%')+card('Ganancia bruta',n(d.gross_profit)+' pts','good')+card('Pérdida bruta','-'+n(d.gross_loss)+' pts','bad')+card('Neto',n(d.net)+' pts'+money,d.net>=0?'good':'bad')+card('Profit factor',d.profit_factor===null?'∞':n(d.profit_factor))+card('Promedio ganada',n(d.avg_win)+' pts')+card('Promedio perdida','-'+n(d.avg_loss)+' pts')+card('Máx. drawdown','-'+n(d.max_drawdown)+' pts','bad');draw(d.curve);
let ss=Object.entries(d.strategies).map(([k,v])=>`<tr><td>${k}</td><td>${v.trades}</td><td class="good">${v.wins}</td><td class="bad">${v.losses}</td><td>${n(v.points)}</td></tr>`).join('');document.querySelector('#strategies').innerHTML='<tr><th>Estrategia</th><th>Total</th><th>Ganadas</th><th>Perdidas</th><th>Puntos</th></tr>'+ss;
let ts=d.rows.map(r=>`<tr><td>${r.cerrada_en||''}</td><td>${r.side||''}</td><td>${r.resultado||''}</td><td>${r.entrada||''}</td><td>${r.salida||''}</td><td>${r.puntos||''}</td><td>${r.estrategia||''}</td></tr>`).join('');document.querySelector('#trades').innerHTML='<tr><th>Cierre</th><th>Lado</th><th>Resultado</th><th>Entrada</th><th>Salida</th><th>Puntos</th><th>Estrategia</th></tr>'+ts}load();setInterval(load,60000);addEventListener('resize',()=>load())
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = b'{"status":"ok"}'
            content_type = "application/json; charset=utf-8"
        elif self.path == "/api/stats":
            body = json.dumps(load_data(), ensure_ascii=False, default=str).encode()
            content_type = "application/json; charset=utf-8"
        elif self.path in ("/", "/index.html"):
            body = HTML.encode()
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    print(f"Panel disponible en http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
