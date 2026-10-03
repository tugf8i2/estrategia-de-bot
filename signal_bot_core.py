#!/usr/bin/env python3
"""
Núcleo compartido del bot de señales (S/R+RSI en lateral, Supertrend en tendencia,
envolvente 2m como relleno) sobre el feed no oficial de TradingView.

Este módulo es AGNÓSTICO al instrumento — no tiene ningún símbolo ni parámetro
"quemado". Cada bot específico (NAS100, XAUUSD, etc.) es un script chico que
importa este núcleo, arma su propio BotConfig con los valores que le hacen
sentido a ESE instrumento, y llama a run_main / run_test / run_stats.

⚠️ IMPORTANTE sobre la fuente de datos:
TradingView NO ofrece una API pública oficial para leer datos de gráficos por
símbolo. Este módulo usa el mismo protocolo websocket interno que usa la propia
página web de TradingView para dibujar el gráfico (la técnica que usan
proyectos open-source conocidos como "tvDatafeed"). Esto significa que no es
una API oficial ni soportada, y puede dejar de funcionar si TradingView cambia
su protocolo interno sin previo aviso. Es para uso personal/informativo.

⚠️ Esto tampoco es asesoría financiera. Las señales dependen 100% de la lógica
definida abajo. Opera bajo tu propio riesgo.
"""

import os
import re
import csv
import json
import time
import random
import string
import logging
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from typing import Optional

import requests
import websocket  # pip install websocket-client
import pandas as pd
import numpy as np


# ============================================================
# CONFIGURACIÓN POR INSTRUMENTO
# ============================================================

@dataclass
class BotConfig:
    # --- Obligatorio: símbolo tal como aparece en la URL de TradingView ---
    tv_symbol: str  # ej "VANTAGE:NAS100" o "VANTAGE:XAUUSD"
    label: str = ""  # nombre corto para logs/archivos; si se deja vacío, usa tv_symbol

    # --- Alertas (comparten canal entre instrumentos, salvo que las sobreescribas) ---
    telegram_bot_token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.environ.get("TELEGRAM_CHAT_ID", ""))
    discord_webhook_url: str = field(default_factory=lambda: os.environ.get("DISCORD_WEBHOOK_URL", ""))
 
    # --- Temporalidades (formato TradingView: '1','3','5','15','30','60','120','240'...'D','W','M') ---
    sr_interval: str = "240"       # 4H para soportes/resistencias
    entry_interval: str = "15"      # temporalidad del gatillo RSI / Supertrend de entrada
    monitor_interval: str = "1"     # temporalidad fina para vigilar TP/SL (mechas)

    # --- RSI (modo reversión) ---
    reversion_mode_enabled: bool = True  # apagalo para que en mercado LATERAL no mande ninguna señal
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    rsi_dynamic_enabled: bool = True
    rsi_percentile_window: int = 200
    rsi_oversold_percentile: float = 20.0
    rsi_overbought_percentile: float = 80.0
    rsi_oversold_max_cap: float = 35.0
    rsi_overbought_min_cap: float = 65.0
    rsi_require_turn_confirmation: bool = True

    # --- Soporte/Resistencia (modo reversión) ---
    sr_lookback_candles: int = 150
    sr_fractal_wing: int = 2
    sr_cluster_tolerance_pct: float = 0.0015   # % -> ya es relativo, no necesita recalibrarse por instrumento
    proximity_tolerance_pct: float = 0.0020    # % -> idem
    touch_lookback_candles: int = 5

    # --- TP/SL ---
    tp_points: float = 110.0                  # ¡ESTE SÍ hay que recalibrar por instrumento! (ver notas del bot específico)
    atr_period: int = 14
    atr_sl_multiplier: float = 1.5
    sl_buffer_points_fallback: float = 40.0   # solo se usa si el ATR no se puede calcular (poco historial)

    min_rr_target: float = 1.0
    max_tp_extension_multiplier: float = 1.5

    poll_seconds: int = 90

    # --- Modo tendencia: qué indicador define el régimen y la entrada principal ---
    trend_mode_enabled: bool = True  # apagalo del todo y siempre usa reversión, aunque haya tendencia
    trend_indicator: str = "macd"  # "macd" (nuevo, más reactivo) o "supertrend" (el original, más lento)
    trend_confirm_candles: int = 3

    # --- Filtros adicionales de calidad para el modo tendencia (afectan Supertrend Y envolvente) ---
    # No bloquean nada por sí solos si los dejás en False/0 — son capas extra, opcionales.
    adx_filter_enabled: bool = False
    adx_period: int = 14
    adx_threshold: float = 20.0  # permisivo a propósito, para no cortar demasiadas señales
    min_candle_body_filter_enabled: bool = False
    min_candle_body_atr_ratio: float = 0.3  # el cuerpo de la vela de entrada debe ser >= 30% del ATR

    # --- Filtro opcional de zonas de liquidez (Supertrend y envolvente) ---
    # Exige un barrido y rechazo de una zona donde coincidan un swing reciente
    # y un nodo de alto volumen. Permanece desactivado hasta validarlo.
    liquidity_filter_enabled: bool = False
    liquidity_volume_bins: int = 20
    liquidity_volume_top_pct: float = 0.7
    liquidity_zone_tolerance_pct: float = 0.002
    liquidity_lookback_candles: int = 5

    # --- Break-even automático: no evita señales perdedoras, cambia el costo de un
    # barrido de stop DESPUÉS de ir ganando. Al avanzar breakeven_trigger_pct del
    # camino hacia el TP, el SL se mueve a un porcentaje configurado de ese
    # mismo recorrido. No afecta señales que van directo al SL.
    breakeven_enabled: bool = True
    breakeven_trigger_pct: float = 0.9  # a qué % del camino hacia el TP se activa
    breakeven_lock_pct: float = 0.5     # a qué % del camino hacia el TP se mueve el SL

    # --- Supertrend (se deja disponible por si querés volver a esto con trend_indicator="supertrend") ---
    supertrend_entry_enabled: bool = True
    supertrend_atr_period: int = 10
    supertrend_multiplier: float = 3.0

    # --- MACD (régimen de 4H) + Bollinger Bands sobre el MACD (entrada) ---
    macd_bb_entry_enabled: bool = True
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    macd_bb_period: int = 20      # ventana para la media/desviación de las bandas sobre el MACD
    macd_bb_std_mult: float = 2.0  # ancho de las bandas, en desviaciones estándar
    macd_bb_sl_atr_multiplier: float = 1.5  # SL basado en ATR (no hay una "línea" natural como en Supertrend)

    # --- Modo tendencia, relleno: envolvente ---
    engulfing_mode_enabled: bool = True  # apaga SOLO la entrada por envolvente
    engulfing_interval: str = "5"   # 5 min: menos ruido y volumen mas representativo que 1m. OJO: TradingView solo acepta 1,3,5,15,30,45,60,120,180,240... "2" NO es válido
    engulfing_volume_lookback: int = 20
    engulfing_volume_multiplier: float = 1.1
    engulfing_atr_period: int = 14
    engulfing_sl_atr_multiplier: float = 0.5

    # --- Ejecución automática en Tradovate ---
    # Interruptor GENERAL: si está en False (por defecto), el bot SOLO manda
    # señales por Telegram/Discord como siempre, sin tocar tu cuenta. Si lo
    # pones en True, cada señal nueva se ejecuta automáticamente en Tradovate
    # con TP/SL puestos de una vez (bracket order). Empezá siempre en False.
    tradovate_trading_enabled: bool = False
    tradovate_environment: str = "demo"  # "demo" o "live" -- empezá siempre en demo
    tradovate_username: str = field(default_factory=lambda: os.environ.get("TRADOVATE_USERNAME", ""))
    tradovate_password: str = field(default_factory=lambda: os.environ.get("TRADOVATE_PASSWORD", ""))
    tradovate_app_id: str = field(default_factory=lambda: os.environ.get("TRADOVATE_APP_ID", "SignalBot"))
    tradovate_cid: str = field(default_factory=lambda: os.environ.get("TRADOVATE_CID", ""))
    tradovate_sec: str = field(default_factory=lambda: os.environ.get("TRADOVATE_SEC", ""))
    # Símbolo EXACTO del contrato vigente en Tradovate (con mes/año, ej "MNQZ5" para
    # el micro Nasdaq de diciembre. Buscalo en la plataforma de Tradovate: contratos
    # nuevos salen cada 3 meses (mar/jun/sep/dic), así que hay que actualizarlo).
    tradovate_contract_symbol: str = ""
    tradovate_order_qty: int = 1

    # --- Ejecución automática en Vantage vía MetaTrader 5 ---
    # Interruptor GENERAL, igual filosofía que el de Tradovate: apagado por
    # defecto, el bot solo avisa. Solo funciona en Windows con MT5 instalado.
    mt5_trading_enabled: bool = False
    mt5_login: str = field(default_factory=lambda: os.environ.get("MT5_LOGIN", ""))
    mt5_password: str = field(default_factory=lambda: os.environ.get("MT5_PASSWORD", ""))
    mt5_server: str = field(default_factory=lambda: os.environ.get("MT5_SERVER", ""))
    mt5_path: str = field(default_factory=lambda: os.environ.get("MT5_PATH", ""))  # opcional: ruta al terminal64.exe
    # Símbolo tal como aparece en Market Watch de tu MT5 de Vantage (puede no
    # ser idéntico al de TradingView, ej "NAS100" o "XAUUSD" con o sin sufijo).
    mt5_symbol: str = ""
    mt5_lot_size: float = 0.10  # volumen del lote (0.01=micro, 0.10=mini, 1.0=estándar)

    # --- Archivos (por defecto, uno distinto por símbolo para poder correr varios bots a la vez) ---
    log_max_bytes: int = 2 * 1024 * 1024
    log_backup_count: int = 2
    state_file: str = ""
    log_file: str = ""
    history_file: str = ""

    tv_ws_url: str = "wss://data.tradingview.com/socket.io/websocket"
    tv_origin: str = "https://www.tradingview.com"

    def __post_init__(self):
        if not self.label:
            self.label = self.tv_symbol
        slug = re.sub(r"[^A-Za-z0-9]+", "_", self.tv_symbol).strip("_").lower()
        base_dir = os.environ.get("SIGNAL_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
        os.makedirs(base_dir, exist_ok=True)
        if not self.state_file:
            self.state_file = os.path.join(base_dir, f"signal_state_{slug}.json")
        if not self.log_file:
            self.log_file = os.path.join(base_dir, f"bot_{slug}.log")
        if not self.history_file:
            self.history_file = os.path.join(base_dir, f"historial_senales_{slug}.csv")
        if os.environ.get("DATABASE_URL"):
            try:
                from cloud_storage import restore_file
                source_dir = os.path.dirname(os.path.abspath(__file__))
                restore_file(f"state:{slug}", self.state_file)
                restore_file(
                    f"history:{slug}", self.history_file,
                    os.path.join(source_dir, f"seed_historial_{slug}.csv"),
                )
            except Exception as exc:
                print(f"AVISO: no se pudo restaurar persistencia cloud: {exc}")
        self._log = None
        self._tradovate_client = None
        self._mt5_client = None

    @property
    def log(self):
        if self._log is None:
            logger = logging.getLogger(f"signal_bot.{self.label}")
            logger.setLevel(logging.INFO)
            if not logger.handlers:
                fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
                sh = logging.StreamHandler()
                sh.setFormatter(fmt)
                fh = RotatingFileHandler(
                    self.log_file, maxBytes=self.log_max_bytes,
                    backupCount=self.log_backup_count, encoding="utf-8",
                )
                fh.setFormatter(fmt)
                logger.addHandler(sh)
                logger.addHandler(fh)
            self._log = logger
        return self._log

    @property
    def tradovate(self):
        """Cliente de Tradovate, creado solo la primera vez que se necesita
        (si tradovate_trading_enabled está en False, nunca se llega a crear)."""
        if self._tradovate_client is None:
            from tradovate_client import TradovateClient
            if not (self.tradovate_username and self.tradovate_password
                    and self.tradovate_cid and self.tradovate_sec):
                raise RuntimeError(
                    "Faltan credenciales de Tradovate (usuario/contraseña/cid/sec). "
                    "Configuralas como variables de entorno: TRADOVATE_USERNAME, "
                    "TRADOVATE_PASSWORD, TRADOVATE_CID, TRADOVATE_SEC."
                )
            self._tradovate_client = TradovateClient(
                username=self.tradovate_username,
                password=self.tradovate_password,
                app_id=self.tradovate_app_id,
                cid=self.tradovate_cid,
                sec=self.tradovate_sec,
                environment=self.tradovate_environment,
                log=self.log,
            )
        return self._tradovate_client

    @property
    def mt5(self):
        """Cliente de MetaTrader5 (Vantage), creado solo la primera vez que se
        necesita. Solo funciona en Windows con MT5 instalado."""
        if self._mt5_client is None:
            from mt5_client import MT5Client
            self._mt5_client = MT5Client(
                login=self.mt5_login,
                password=self.mt5_password,
                server=self.mt5_server,
                path=self.mt5_path,
                log=self.log,
            )
        return self._mt5_client


# ============================================================
# ALERTAS: TELEGRAM + DISCORD
# ============================================================

def send_telegram(cfg: BotConfig, message: str):
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": cfg.telegram_chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.status_code != 200:
            cfg.log.error("Error enviando Telegram: %s", r.text)
    except Exception as e:
        cfg.log.error("Excepción enviando Telegram: %s", e)


def send_discord(cfg: BotConfig, message: str):
    if not cfg.discord_webhook_url:
        return
    try:
        r = requests.post(cfg.discord_webhook_url, json={"content": message}, timeout=10)
        if r.status_code not in (200, 204):
            cfg.log.error("Error enviando Discord: %s %s", r.status_code, r.text)
    except Exception as e:
        cfg.log.error("Excepción enviando Discord: %s", e)


def send_alert(cfg: BotConfig, message: str):
    sent_somewhere = False
    if cfg.telegram_bot_token and cfg.telegram_chat_id:
        send_telegram(cfg, message)
        sent_somewhere = True
    if cfg.discord_webhook_url:
        send_discord(cfg, message)
        sent_somewhere = True
    if not sent_somewhere:
        cfg.log.warning("Ningún canal de alertas configurado (Telegram/Discord). Mensaje no enviado: %s", message)


# ============================================================
# PROTOCOLO WEBSOCKET DE TRADINGVIEW (no oficial / reverse-engineered)
# ============================================================

def _random_session_id(prefix: str) -> str:
    letters = string.ascii_lowercase
    return prefix + "".join(random.choice(letters) for _ in range(12))


def _prepend_header(msg: str) -> str:
    return f"~m~{len(msg)}~m~{msg}"


def _construct_message(func: str, params: list) -> str:
    return json.dumps({"m": func, "p": params}, separators=(",", ":"))


def _create_message(func: str, params: list) -> str:
    return _prepend_header(_construct_message(func, params))


def _send(ws, func: str, params: list):
    ws.send(_create_message(func, params))


def _split_frames(raw: str):
    """TradingView empaqueta varios mensajes JSON en un solo frame de texto,
    separados por el patrón ~m~<len>~m~. Esta función los separa."""
    frames = []
    for chunk in re.split(r"~m~\d+~m~", raw):
        chunk = chunk.strip()
        if chunk:
            frames.append(chunk)
    return frames


def _fetch_bars_via_tv(cfg: BotConfig, symbol_full: str, interval: str, n_bars: int = 300, timeout: int = 20) -> pd.DataFrame:
    """Abre una sesión websocket temporal a TradingView, pide n_bars velas para
    symbol_full en la temporalidad interval, y devuelve un DataFrame OHLCV."""
    chart_session = _random_session_id("cs_")
    quote_session = _random_session_id("qs_")

    ws = websocket.create_connection(
        cfg.tv_ws_url,
        header=[f"Origin: {cfg.tv_origin}"],
        timeout=timeout,
    )

    try:
        _send(ws, "set_auth_token", ["unauthorized_user_token"])
        _send(ws, "chart_create_session", [chart_session, ""])
        _send(ws, "quote_create_session", [quote_session])
        _send(ws, "quote_set_fields", [
            quote_session, "lp", "lp_time", "volume", "current_session",
            "description", "exchange", "original_name", "pro_name", "short_name",
        ])
        _send(ws, "quote_add_symbols", [quote_session, symbol_full])
        symbol_init = json.dumps({"symbol": symbol_full, "adjustment": "splits"}, separators=(",", ":"))
        _send(ws, "resolve_symbol", [chart_session, "symbol_1", "=" + symbol_init])
        _send(ws, "create_series", [chart_session, "s1", "s1", "symbol_1", interval, n_bars])

        bars = {}
        deadline = time.time() + timeout
        got_series = False

        while time.time() < deadline:
            try:
                raw = ws.recv()
            except Exception:
                break
            if not raw:
                continue

            if "~h~" in raw:
                try:
                    ws.send(raw)
                except Exception:
                    pass
                continue

            for frame in _split_frames(raw):
                try:
                    msg = json.loads(frame)
                except json.JSONDecodeError:
                    continue

                m = msg.get("m")
                if m in ("timescale_update", "du"):
                    payload = msg.get("p", [])
                    for part in payload:
                        if isinstance(part, dict) and "s1" in part:
                            series = part["s1"]
                            points = series.get("s", [])
                            for point in points:
                                v = point.get("v")
                                if v and len(v) >= 5:
                                    t, o, h, l, c = v[0], v[1], v[2], v[3], v[4]
                                    vol = v[5] if len(v) > 5 else 0.0
                                    bars[t] = (o, h, l, c, vol)
                            got_series = True

                if msg.get("m") == "series_completed":
                    got_series = True

            if got_series and len(bars) >= min(n_bars, 20):
                time.sleep(0.5)
                try:
                    ws.settimeout(1.5)
                    while True:
                        raw2 = ws.recv()
                        if not raw2:
                            break
                        if "~h~" in raw2:
                            ws.send(raw2)
                            continue
                        for frame in _split_frames(raw2):
                            try:
                                msg2 = json.loads(frame)
                            except json.JSONDecodeError:
                                continue
                            if msg2.get("m") in ("timescale_update", "du"):
                                for part in msg2.get("p", []):
                                    if isinstance(part, dict) and "s1" in part:
                                        for point in part["s1"].get("s", []):
                                            v = point.get("v")
                                            if v and len(v) >= 5:
                                                t, o, h, l, c = v[0], v[1], v[2], v[3], v[4]
                                                vol = v[5] if len(v) > 5 else 0.0
                                                bars[t] = (o, h, l, c, vol)
                except Exception:
                    pass
                break
    finally:
        try:
            ws.close()
        except Exception:
            pass

    if not bars:
        raise RuntimeError(
            f"No se recibieron velas de TradingView para '{symbol_full}' en interval='{interval}'. "
            "Esto puede pasar si TradingView cambió el protocolo interno, si el símbolo no existe "
            "tal cual, o por bloqueo temporal. Prueba de nuevo o revisa el símbolo en la URL del gráfico."
        )

    rows = []
    for t, (o, h, l, c, vol) in sorted(bars.items()):
        rows.append({"start": t, "open": o, "high": h, "low": l, "close": c, "volume": vol})
    df = pd.DataFrame(rows)
    df["start"] = pd.to_datetime(df["start"], unit="s", utc=True)
    return df


def get_klines(cfg: BotConfig, symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
    last_err = None
    for attempt in range(3):
        try:
            return _fetch_bars_via_tv(cfg, symbol, interval, n_bars=limit)
        except Exception as e:
            last_err = e
            cfg.log.warning("Intento %d/3 fallido pidiendo velas TV (%s, %s): %s", attempt + 1, symbol, interval, e)
            time.sleep(2)
    raise last_err


def get_last_price(cfg: BotConfig, symbol: str) -> float:
    df = get_klines(cfg, symbol, cfg.entry_interval, limit=5)
    return float(df["close"].iloc[-1])


# ============================================================
# INDICADORES (agnósticos al instrumento)
# ============================================================

def compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def compute_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    """Indicador Supertrend clásico (basado en ATR). Devuelve (línea, dirección),
    dirección: 1 = alcista, -1 = bajista."""
    atr = compute_atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    close = df["close"].values
    basic_upper_v = basic_upper.values
    basic_lower_v = basic_lower.values

    n = len(df)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    supertrend = np.full(n, np.nan)
    direction = np.full(n, np.nan)

    for i in range(n):
        if np.isnan(basic_upper_v[i]) or np.isnan(basic_lower_v[i]):
            continue

        prev_valid = (
            i > 0
            and not np.isnan(final_upper[i - 1])
            and not np.isnan(final_lower[i - 1])
            and not np.isnan(supertrend[i - 1])
        )

        if not prev_valid:
            final_upper[i] = basic_upper_v[i]
            final_lower[i] = basic_lower_v[i]
            direction[i] = 1
            supertrend[i] = final_lower[i]
            continue

        final_upper[i] = (
            basic_upper_v[i]
            if (basic_upper_v[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1])
            else final_upper[i - 1]
        )
        final_lower[i] = (
            basic_lower_v[i]
            if (basic_lower_v[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1])
            else final_lower[i - 1]
        )

        if supertrend[i - 1] == final_upper[i - 1]:
            if close[i] <= final_upper[i]:
                supertrend[i] = final_upper[i]
                direction[i] = -1
            else:
                supertrend[i] = final_lower[i]
                direction[i] = 1
        else:
            if close[i] >= final_lower[i]:
                supertrend[i] = final_lower[i]
                direction[i] = 1
            else:
                supertrend[i] = final_upper[i]
                direction[i] = -1

    return pd.Series(supertrend, index=df.index), pd.Series(direction, index=df.index)


def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD clásico: línea MACD (EMA rápida - EMA lenta), línea de señal
    (EMA de la línea MACD), e histograma (la diferencia entre ambas)."""
    close = df["close"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_macd_direction(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    """Dirección de tendencia según MACD: 1 = alcista (MACD > señal), -1 = bajista.
    A diferencia de Supertrend, reacciona en cuanto las líneas se cruzan, sin
    ningún colchón de ATR que retrase el cambio. Los primeros `slow + signal`
    puntos quedan en NaN (no hay suficiente historial para que la EMA lenta y
    la señal sean confiables todavía)."""
    macd_line, signal_line, _ = compute_macd(df, fast, slow, signal)
    direction = pd.Series(np.where(macd_line > signal_line, 1.0, -1.0), index=df.index)
    warmup = slow + signal
    if len(direction) > warmup:
        direction.iloc[:warmup] = np.nan
    else:
        direction[:] = np.nan
    return direction


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ADX de Wilder: mide la FUERZA de la tendencia (no la dirección). Se usa
    como filtro adicional — la dirección ya la da Supertrend o MACD, esto solo
    confirma que hay músculo real detrás antes de tomar la señal.
    Referencia: <20 sin tendencia clara/lateral, 20-25 zona gris, >25 tendencia fuerte."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index,
    )

    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr_smooth = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    plus_dm_smooth = plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    minus_dm_smooth = minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    plus_di = 100 * (plus_dm_smooth / atr_smooth.replace(0, np.nan))
    minus_di = 100 * (minus_dm_smooth / atr_smooth.replace(0, np.nan))

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return adx.fillna(0)


def candle_body_ok(candle, atr_value: float, ratio: float) -> bool:
    """Filtro de convicción: el cuerpo de la vela de entrada (no la mecha) debe
    ser al menos `ratio` veces el ATR, para descartar velas indecisas/chicas
    que técnicamente cumplen el patrón pero sin fuerza real detrás."""
    if atr_value is None or atr_value <= 0:
        return True  # sin ATR confiable, no bloqueamos por este filtro
    body = abs(float(candle["close"]) - float(candle["open"]))
    return body >= atr_value * ratio


def find_swing_points(df: pd.DataFrame, wing: int = 2):
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    swing_highs, swing_lows = [], []
    for i in range(wing, n - wing):
        window_high = highs[i - wing: i + wing + 1]
        window_low = lows[i - wing: i + wing + 1]
        if highs[i] == window_high.max():
            swing_highs.append(highs[i])
        if lows[i] == window_low.min():
            swing_lows.append(lows[i])
    return swing_highs, swing_lows


def compute_volume_profile_levels(df: pd.DataFrame, bins: int = 20, top_pct: float = 0.7):
    """Devuelve centros de tramos de precio con volumen agregado alto."""
    if df.empty or "volume" not in df.columns or bins <= 0 or not 0 <= top_pct <= 1:
        return []
    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    if not np.isfinite(price_min) or not np.isfinite(price_max) or price_max <= price_min:
        return []

    bin_edges = np.linspace(price_min, price_max, bins + 1)
    volume_per_bin = np.zeros(bins)
    mids = (df["high"].to_numpy(dtype=float) + df["low"].to_numpy(dtype=float)) / 2
    volumes = df["volume"].to_numpy(dtype=float)
    for mid, volume in zip(mids, volumes):
        if not np.isfinite(mid) or not np.isfinite(volume) or volume <= 0:
            continue
        idx = int((mid - price_min) / (price_max - price_min) * bins)
        volume_per_bin[min(max(idx, 0), bins - 1)] += volume

    populated = volume_per_bin[volume_per_bin > 0]
    if populated.size == 0:
        return []
    threshold = np.quantile(populated, top_pct)
    return [
        float((bin_edges[i] + bin_edges[i + 1]) / 2)
        for i, volume in enumerate(volume_per_bin)
        if volume >= threshold and volume > 0
    ]


def find_liquidity_zones(df: pd.DataFrame, wing: int = 2, bins: int = 20,
                         top_pct: float = 0.7, tolerance_pct: float = 0.002):
    """Cruza swings con nodos de volumen y devuelve soportes y resistencias."""
    swing_highs, swing_lows = find_swing_points(df, wing=wing)
    high_volume_levels = compute_volume_profile_levels(df, bins=bins, top_pct=top_pct)
    if not high_volume_levels:
        return [], []

    def confirmed(levels):
        return [
            float(level)
            for level in levels
            if any(hv and abs(level - hv) / abs(hv) <= tolerance_pct for hv in high_volume_levels)
        ]

    return confirmed(swing_lows), confirmed(swing_highs)


def detect_liquidity_rejection(cfg: BotConfig, df: pd.DataFrame, side: str):
    """Detecta un barrido y cierre de regreso sobre una zona reciente."""
    support_zones, resistance_zones = find_liquidity_zones(
        df,
        wing=cfg.sr_fractal_wing,
        bins=cfg.liquidity_volume_bins,
        top_pct=cfg.liquidity_volume_top_pct,
        tolerance_pct=cfg.liquidity_zone_tolerance_pct,
    )
    recent = df.tail(max(1, cfg.liquidity_lookback_candles))
    zones = support_zones if side == "LONG" else resistance_zones
    for zone in zones:
        if side == "LONG":
            swept = recent[(recent["low"] < zone) & (recent["close"] > zone)]
        else:
            swept = recent[(recent["high"] > zone) & (recent["close"] < zone)]
        if not swept.empty:
            return True, zone
    return False, None


def cluster_levels(levels, tolerance_pct: float):
    if not levels:
        return []
    levels = sorted(levels)
    clusters = [[levels[0]]]
    for lvl in levels[1:]:
        last_cluster_mean = np.mean(clusters[-1])
        if abs(lvl - last_cluster_mean) / last_cluster_mean <= tolerance_pct:
            clusters[-1].append(lvl)
        else:
            clusters.append([lvl])
    return [(float(np.mean(c)), len(c)) for c in clusters]


def get_nearest_support_resistance(cfg: BotConfig, df_htf: pd.DataFrame, current_price: float):
    swing_highs, swing_lows = find_swing_points(df_htf, wing=cfg.sr_fractal_wing)
    resistance_clusters = cluster_levels(swing_highs, cfg.sr_cluster_tolerance_pct)
    support_clusters = cluster_levels(swing_lows, cfg.sr_cluster_tolerance_pct)

    resistances_above = [lvl for lvl, touches in resistance_clusters if lvl > current_price]
    supports_below = [lvl for lvl, touches in support_clusters if lvl < current_price]

    nearest_resistance = min(resistances_above) if resistances_above else None
    nearest_support = max(supports_below) if supports_below else None
    return nearest_support, nearest_resistance


def detect_engulfing(df: pd.DataFrame):
    if len(df) < 2:
        return None
    prev = df.iloc[-2]
    curr = df.iloc[-1]
    prev_bearish = prev["close"] < prev["open"]
    prev_bullish = prev["close"] > prev["open"]
    curr_bullish = curr["close"] > curr["open"]
    curr_bearish = curr["close"] < curr["open"]

    if prev_bearish and curr_bullish and curr["open"] <= prev["close"] and curr["close"] >= prev["open"]:
        return "BULLISH"
    if prev_bullish and curr_bearish and curr["open"] >= prev["close"] and curr["close"] <= prev["open"]:
        return "BEARISH"
    return None


# ============================================================
# ESTADO (persistencia de la señal activa)
# ============================================================

def load_state(cfg: BotConfig) -> dict:
    if os.path.exists(cfg.state_file):
        with open(cfg.state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"active": False}


def save_state(cfg: BotConfig, state: dict):
    with open(cfg.state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)
    if os.environ.get("DATABASE_URL"):
        from cloud_storage import persist_file
        persist_file(f"state:{re.sub(r'[^A-Za-z0-9]+', '_', cfg.tv_symbol).strip('_').lower()}", cfg.state_file)


# ============================================================
# BITÁCORA / HISTORIAL DE SEÑALES (CSV)
# ============================================================

HISTORY_FIELDS = [
    "abierta_en", "cerrada_en", "side", "entrada", "tp", "sl",
    "salida", "resultado", "puntos", "rsi", "soporte_ref", "resistencia_ref", "rr", "estrategia",
]


def log_signal_result(cfg: BotConfig, state: dict, resultado: str, exit_price: float):
    is_new_file = not os.path.exists(cfg.history_file)

    puntos = exit_price - state["entry"] if state["side"] == "LONG" else state["entry"] - exit_price
    rsi_val = state.get("rsi")

    row = {
        "abierta_en": state.get("opened_at", ""),
        "cerrada_en": datetime.now(timezone.utc).isoformat(),
        "side": state["side"],
        "entrada": round(state["entry"], 2),
        "tp": round(state["tp"], 2),
        "sl": round(state["sl"], 2),
        "salida": round(exit_price, 2),
        "resultado": "GANADORA" if "GANADORA" in resultado else ("BREAK-EVEN" if "BREAK-EVEN" in resultado else "PERDEDORA"),
        "puntos": round(puntos, 2),
        "rsi": round(rsi_val, 1) if rsi_val is not None else "",
        "soporte_ref": round(state.get("support_ref"), 2) if state.get("support_ref") is not None else "",
        "resistencia_ref": round(state.get("resistance_ref"), 2) if state.get("resistance_ref") is not None else "",
        "rr": state.get("rr") if state.get("rr") is not None else "",
        "estrategia": state.get("estrategia", ""),
    }

    with open(cfg.history_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        if is_new_file:
            writer.writeheader()
        writer.writerow(row)
    if os.environ.get("DATABASE_URL"):
        from cloud_storage import persist_file
        slug = re.sub(r"[^A-Za-z0-9]+", "_", cfg.tv_symbol).strip("_").lower()
        persist_file(f"history:{slug}", cfg.history_file)


def print_stats(cfg: BotConfig):
    if not os.path.exists(cfg.history_file):
        print(f"Todavía no hay señales cerradas registradas para {cfg.label}.")
        return

    df = pd.read_csv(cfg.history_file)
    if df.empty:
        print("La bitácora está vacía.")
        return

    total = len(df)
    ganadas = (df["resultado"] == "GANADORA").sum()
    perdidas = (df["resultado"] == "PERDEDORA").sum()
    breakeven = (df["resultado"] == "BREAK-EVEN").sum()
    decididas = ganadas + perdidas  # el win rate se calcula solo sobre estas, el break-even no cuenta como ninguna
    win_rate = 100 * ganadas / decididas if decididas else 0
    puntos_netos = df["puntos"].sum()

    print("=" * 50)
    print(f"RESUMEN DE LA BITÁCORA — {cfg.label}")
    print("=" * 50)
    print(f"Total de señales cerradas: {total}")
    print(f"Ganadoras: {ganadas} | Perdedoras: {perdidas} | Break-even: {breakeven}")
    print(f"Win rate (sobre señales decididas, sin contar break-even): {win_rate:.1f}%")
    print(f"Puntos netos acumulados: {puntos_netos:+.2f}")

    if "estrategia" in df.columns and df["estrategia"].notna().any():
        print("-" * 50)
        print("Por estrategia:")
        for estrategia, grupo in df.groupby(df["estrategia"].fillna("(sin dato)")):
            g_total = len(grupo)
            g_ganadas = (grupo["resultado"] == "GANADORA").sum()
            g_perdidas = (grupo["resultado"] == "PERDEDORA").sum()
            g_be = (grupo["resultado"] == "BREAK-EVEN").sum()
            g_decididas = g_ganadas + g_perdidas
            g_wr = 100 * g_ganadas / g_decididas if g_decididas else 0
            g_puntos = grupo["puntos"].sum()
            print(f"  {estrategia}: {g_total} señales ({g_be} break-even) | win rate {g_wr:.1f}% | puntos netos {g_puntos:+.2f}")

    print("-" * 50)
    print("Últimas 10 señales:")
    print(df.tail(10).to_string(index=False))


# ============================================================
# LÓGICA DE SEÑALES: LAS 3 ESTRATEGIAS
# ============================================================

def compute_dynamic_rsi_thresholds(cfg: BotConfig, rsi_series: pd.Series):
    recent_rsi = rsi_series.tail(cfg.rsi_percentile_window).dropna()
    if len(recent_rsi) < 20:
        return cfg.rsi_oversold, cfg.rsi_overbought

    oversold_dyn = float(np.percentile(recent_rsi, cfg.rsi_oversold_percentile))
    overbought_dyn = float(np.percentile(recent_rsi, cfg.rsi_overbought_percentile))

    oversold_dyn = min(oversold_dyn, cfg.rsi_oversold_max_cap)
    overbought_dyn = max(overbought_dyn, cfg.rsi_overbought_min_cap)

    oversold_dyn = min(oversold_dyn, 50.0)
    overbought_dyn = max(overbought_dyn, 50.0)
    return oversold_dyn, overbought_dyn


def apply_soft_rr_adjustment(cfg: BotConfig, entry: float, tp: float, sl: float, side: str):
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0:
        return tp, reward, (reward / risk if risk else None), False

    current_rr = reward / risk
    if current_rr >= cfg.min_rr_target:
        return tp, reward, current_rr, False

    desired_reward = risk * cfg.min_rr_target
    max_allowed_reward = cfg.tp_points * cfg.max_tp_extension_multiplier
    new_reward = min(desired_reward, max_allowed_reward)

    if new_reward <= reward:
        return tp, reward, current_rr, False

    new_tp = entry + new_reward if side == "LONG" else entry - new_reward
    new_rr = new_reward / risk
    return new_tp, new_reward, new_rr, True


def evaluate_reversion_signal(cfg: BotConfig, df_entry, price, support, resistance):
    """Reversión: soporte/resistencia de 4H + RSI + confirmación de giro. Se usa
    cuando el mercado está LATERAL (sin tendencia 4H clara)."""
    rsi_series = compute_rsi(df_entry["close"], cfg.rsi_period)
    rsi_value = float(rsi_series.iloc[-1])
    rsi_prev = float(rsi_series.iloc[-2]) if len(rsi_series) >= 2 else rsi_value
    rsi_turning_up = rsi_value > rsi_prev
    rsi_turning_down = rsi_value < rsi_prev

    atr_series = compute_atr(df_entry, cfg.atr_period)
    atr_value = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else None
    sl_distance = (atr_value * cfg.atr_sl_multiplier) if atr_value else cfg.sl_buffer_points_fallback

    if cfg.rsi_dynamic_enabled:
        oversold_threshold, overbought_threshold = compute_dynamic_rsi_thresholds(cfg, rsi_series)
    else:
        oversold_threshold, overbought_threshold = cfg.rsi_oversold, cfg.rsi_overbought

    recent = df_entry.tail(cfg.touch_lookback_candles)

    def touched(level, series):
        if level is None:
            return False
        return bool((abs(series - level) / level <= cfg.proximity_tolerance_pct).any())

    touched_support = touched(support, recent["low"])
    touched_resistance = touched(resistance, recent["high"])

    cfg.log.info(
        "[REVERSIÓN] Precio=%.2f | Soporte=%s (tocado: %s) | Resistencia=%s (tocada: %s) | "
        "RSI(%s)=%.2f (prev=%.2f, subiendo=%s) | umbrales: sobreventa<=%.1f sobrecompra>=%.1f | "
        "ATR(%d)=%s -> SL dist=%.2f",
        price,
        f"{support:.2f}" if support else "N/A", touched_support,
        f"{resistance:.2f}" if resistance else "N/A", touched_resistance,
        cfg.entry_interval, rsi_value, rsi_prev, rsi_turning_up, oversold_threshold, overbought_threshold,
        cfg.atr_period, f"{atr_value:.2f}" if atr_value else "N/A", sl_distance,
    )

    long_rsi_ok = rsi_value <= oversold_threshold and (not cfg.rsi_require_turn_confirmation or rsi_turning_up)
    short_rsi_ok = rsi_value >= overbought_threshold and (not cfg.rsi_require_turn_confirmation or rsi_turning_down)

    signal = None
    if touched_support and long_rsi_ok:
        tp = min(price + cfg.tp_points, resistance) if resistance else price + cfg.tp_points
        sl = support - sl_distance
        tp, reward, rr, adjusted = apply_soft_rr_adjustment(cfg, price, tp, sl, "LONG")
        signal = {
            "side": "LONG", "entry": price, "support_ref": support, "resistance_ref": resistance,
            "tp": tp, "sl": sl, "rsi": rsi_value, "rr": round(rr, 2) if rr is not None else None,
            "rr_ajustado": adjusted, "estrategia": "reversion_sr_rsi",
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
    elif touched_resistance and short_rsi_ok:
        tp = max(price - cfg.tp_points, support) if support else price - cfg.tp_points
        sl = resistance + sl_distance
        tp, reward, rr, adjusted = apply_soft_rr_adjustment(cfg, price, tp, sl, "SHORT")
        signal = {
            "side": "SHORT", "entry": price, "support_ref": support, "resistance_ref": resistance,
            "tp": tp, "sl": sl, "rsi": rsi_value, "rr": round(rr, 2) if rr is not None else None,
            "rr_ajustado": adjusted, "estrategia": "reversion_sr_rsi",
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
    return signal


def evaluate_trend_signal(cfg: BotConfig, df_entry, price, support, resistance, trend_direction):
    """Tendencia: entra A FAVOR de la tendencia de 4H usando un giro fresco del
    Supertrend en la temporalidad de entrada. SL = la propia línea de Supertrend."""
    st_entry, dir_entry = compute_supertrend(df_entry, cfg.supertrend_atr_period, cfg.supertrend_multiplier)
    if len(dir_entry) < 2:
        return None

    current_dir = dir_entry.iloc[-1]
    prev_dir = dir_entry.iloc[-2]

    if pd.isna(current_dir) or pd.isna(prev_dir):
        cfg.log.info("[TENDENCIA] Supertrend de entrada sin suficiente historial todavía (ATR calentando). Se espera.")
        return None

    fresh_flip = current_dir != prev_dir
    aligned = current_dir == trend_direction

    cfg.log.info(
        "[TENDENCIA] Precio=%.2f | Supertrend entrada dir=%s (prev=%s, giro fresco=%s) | "
        "alineado con tendencia 4H=%s | línea Supertrend=%.2f",
        price, current_dir, prev_dir, fresh_flip, aligned, float(st_entry.iloc[-1]),
    )

    if not (fresh_flip and aligned):
        return None

    if cfg.min_candle_body_filter_enabled:
        atr_series = compute_atr(df_entry, cfg.atr_period)
        atr_value = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else None
        if not candle_body_ok(df_entry.iloc[-1], atr_value, cfg.min_candle_body_atr_ratio):
            cfg.log.info(
                "[TENDENCIA] Giro de Supertrend detectado pero la vela es demasiado chica "
                "(cuerpo < %.0f%% del ATR) — se descarta por falta de convicción.",
                cfg.min_candle_body_atr_ratio * 100,
            )
            return None

    side = "LONG" if current_dir == 1 else "SHORT"

    if cfg.liquidity_filter_enabled:
        rejection_ok, zone = detect_liquidity_rejection(cfg, df_entry, side)
        if not rejection_ok:
            cfg.log.info(
                "[TENDENCIA] Giro detectado sin rechazo confirmado en zona de liquidez (15m); se descarta."
            )
            return None
        cfg.log.info("[TENDENCIA] Rechazo de liquidez confirmado en %.2f.", zone)

    sl = float(st_entry.iloc[-1])
    tp = price + cfg.tp_points if side == "LONG" else price - cfg.tp_points
    tp, reward, rr, adjusted = apply_soft_rr_adjustment(cfg, price, tp, sl, side)

    return {
        "side": side, "entry": price, "support_ref": support, "resistance_ref": resistance,
        "tp": tp, "sl": sl, "rsi": None, "rr": round(rr, 2) if rr is not None else None,
        "rr_ajustado": adjusted, "estrategia": "tendencia_supertrend",
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }


def evaluate_macd_bb_signal(cfg: BotConfig, df_entry, price, support, resistance, trend_direction):
    """Tendencia: entra A FAVOR de la tendencia de 4H (definida por MACD) usando
    Bollinger Bands calculadas SOBRE la línea de MACD (no sobre el precio) en
    la temporalidad de entrada. Un cruce de la línea MACD hacia afuera de su
    propia banda es una señal de momentum extremo/ruptura — más reactivo que
    esperar un giro de Supertrend, porque no tiene ningún colchón de ATR que
    retrase la detección del cambio."""
    macd_line, signal_line, _ = compute_macd(df_entry, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    bb_mid = macd_line.rolling(cfg.macd_bb_period).mean()
    bb_std = macd_line.rolling(cfg.macd_bb_period).std()
    upper = bb_mid + cfg.macd_bb_std_mult * bb_std
    lower = bb_mid - cfg.macd_bb_std_mult * bb_std

    if (len(macd_line) < 2 or pd.isna(upper.iloc[-1]) or pd.isna(lower.iloc[-1])
            or pd.isna(upper.iloc[-2]) or pd.isna(lower.iloc[-2])):
        cfg.log.info("[MACD-BB] Sin suficiente historial todavía para las bandas. Se espera.")
        return None

    macd_now, macd_prev = float(macd_line.iloc[-1]), float(macd_line.iloc[-2])
    upper_now, upper_prev = float(upper.iloc[-1]), float(upper.iloc[-2])
    lower_now, lower_prev = float(lower.iloc[-1]), float(lower.iloc[-2])

    crossed_up = macd_prev <= upper_prev and macd_now > upper_now
    crossed_down = macd_prev >= lower_prev and macd_now < lower_now

    cfg.log.info(
        "[MACD-BB] Precio=%.2f | MACD=%.4f (prev=%.4f) | Banda sup=%.4f | Banda inf=%.4f | "
        "cruce arriba=%s | cruce abajo=%s | tendencia 4H=%s",
        price, macd_now, macd_prev, upper_now, lower_now, crossed_up, crossed_down, trend_direction,
    )

    if crossed_up and trend_direction == 1:
        side = "LONG"
    elif crossed_down and trend_direction == -1:
        side = "SHORT"
    else:
        return None

    atr_series = compute_atr(df_entry, cfg.atr_period)
    atr_value = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else None
    sl_distance = (atr_value * cfg.macd_bb_sl_atr_multiplier) if atr_value else cfg.sl_buffer_points_fallback

    sl = price - sl_distance if side == "LONG" else price + sl_distance
    tp = price + cfg.tp_points if side == "LONG" else price - cfg.tp_points
    tp, reward, rr, adjusted = apply_soft_rr_adjustment(cfg, price, tp, sl, side)

    return {
        "side": side, "entry": price, "support_ref": support, "resistance_ref": resistance,
        "tp": tp, "sl": sl, "rsi": None, "rr": round(rr, 2) if rr is not None else None,
        "rr_ajustado": adjusted, "estrategia": "tendencia_macd_bb",
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }


def evaluate_engulfing_signal(cfg: BotConfig, symbol: str, support, resistance, trend_direction: int,
                              df_entry_15m=None):
    """Relleno para tendencia: envolvente en temporalidad chica, a favor de la
    tendencia de 4H, confirmada con volumen y, opcionalmente, liquidez en 15m."""
    df = get_klines(cfg, symbol, cfg.engulfing_interval, limit=60)
    price = float(df["close"].iloc[-1])

    pattern = detect_engulfing(df)

    lookback_slice = df["volume"].iloc[-(cfg.engulfing_volume_lookback + 1):-1]
    avg_volume = float(lookback_slice.mean()) if len(lookback_slice) > 0 else 0.0
    last_volume = float(df["volume"].iloc[-1])
    volume_confirmed = avg_volume > 0 and last_volume >= avg_volume * cfg.engulfing_volume_multiplier

    cfg.log.info(
        "[ENGULFING %sm] patrón=%s | volumen actual=%.0f vs promedio=%.0f (mínimo x%.1f) -> confirmado=%s",
        cfg.engulfing_interval, pattern, last_volume, avg_volume, cfg.engulfing_volume_multiplier, volume_confirmed,
    )

    if pattern is None or not volume_confirmed:
        return None

    matches_trend = (pattern == "BULLISH" and trend_direction == 1) or (pattern == "BEARISH" and trend_direction == -1)
    if not matches_trend:
        cfg.log.info("[ENGULFING] Patrón %s detectado pero NO alineado con la tendencia 4H -> se ignora.", pattern)
        return None

    engulfing_candle = df.iloc[-1]
    atr_period_safe = min(cfg.engulfing_atr_period, max(2, len(df) - 1))
    atr_series = compute_atr(df, atr_period_safe)
    atr_value = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else None
    cushion = (atr_value * cfg.engulfing_sl_atr_multiplier) if atr_value else 10.0

    if cfg.min_candle_body_filter_enabled and not candle_body_ok(engulfing_candle, atr_value, cfg.min_candle_body_atr_ratio):
        cfg.log.info(
            "[ENGULFING] Patrón %s detectado pero la vela envolvente es demasiado chica "
            "(cuerpo < %.0f%% del ATR) — se descarta por falta de convicción.",
            pattern, cfg.min_candle_body_atr_ratio * 100,
        )
        return None

    side = "LONG" if pattern == "BULLISH" else "SHORT"

    if cfg.liquidity_filter_enabled:
        if df_entry_15m is None or df_entry_15m.empty:
            cfg.log.info("[ENGULFING] Filtro de liquidez activo sin datos de 15m; se descarta.")
            return None
        rejection_ok, zone = detect_liquidity_rejection(cfg, df_entry_15m, side)
        if not rejection_ok:
            cfg.log.info(
                "[ENGULFING] Patrón %s sin rechazo confirmado en zona de liquidez (15m); se descarta.",
                pattern,
            )
            return None
        cfg.log.info("[ENGULFING] Rechazo de liquidez confirmado en %.2f.", zone)

    if pattern == "BULLISH":
        sl = float(engulfing_candle["low"]) - cushion
        tp = price + cfg.tp_points
    else:
        sl = float(engulfing_candle["high"]) + cushion
        tp = price - cfg.tp_points

    tp, reward, rr, adjusted = apply_soft_rr_adjustment(cfg, price, tp, sl, side)

    return {
        "side": side, "entry": price, "support_ref": support, "resistance_ref": resistance,
        "tp": tp, "sl": sl, "rsi": None, "rr": round(rr, 2) if rr is not None else None,
        "rr_ajustado": adjusted, "estrategia": "tendencia_engulfing_2m",
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }


def evaluate_new_signal(cfg: BotConfig, symbol: str):
    df_entry = get_klines(cfg, symbol, cfg.entry_interval, limit=200)
    price = float(df_entry["close"].iloc[-1])

    df_htf = get_klines(cfg, symbol, cfg.sr_interval, limit=cfg.sr_lookback_candles)
    support, resistance = get_nearest_support_resistance(cfg, df_htf, price)

    regime_direction = None
    current_adx = None
    if cfg.trend_mode_enabled:
        if cfg.trend_indicator == "macd":
            dir_htf = compute_macd_direction(df_htf, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
        else:
            _, dir_htf = compute_supertrend(df_htf, cfg.supertrend_atr_period, cfg.supertrend_multiplier)
        recent_dir = dir_htf.tail(cfg.trend_confirm_candles)
        direction_confirmed = (
            len(recent_dir) == cfg.trend_confirm_candles
            and not recent_dir.isna().any()
            and recent_dir.nunique() == 1
        )

        strength_confirmed = True
        if cfg.adx_filter_enabled:
            adx_series = compute_adx(df_htf, cfg.adx_period)
            current_adx = float(adx_series.iloc[-1]) if not pd.isna(adx_series.iloc[-1]) else 0.0
            strength_confirmed = current_adx >= cfg.adx_threshold

        if direction_confirmed and strength_confirmed:
            regime_direction = int(recent_dir.iloc[-1])
        elif direction_confirmed and not strength_confirmed:
            cfg.log.info(
                "Dirección de tendencia confirmada pero ADX=%.1f < %.1f (umbral) — se trata como LATERAL.",
                current_adx, cfg.adx_threshold,
            )

    regime_txt = {1: "TENDENCIA ALCISTA", -1: "TENDENCIA BAJISTA", None: "LATERAL"}[regime_direction]
    adx_txt = f" (ADX={current_adx:.1f})" if current_adx is not None else ""
    cfg.log.info("Régimen de mercado (4H, %s): %s%s", cfg.trend_indicator.upper(), regime_txt, adx_txt)

    if regime_direction is not None:
        signal = None
        if cfg.trend_indicator == "macd" and cfg.macd_bb_entry_enabled:
            signal = evaluate_macd_bb_signal(cfg, df_entry, price, support, resistance, regime_direction)
        elif cfg.trend_indicator == "supertrend" and cfg.supertrend_entry_enabled:
            signal = evaluate_trend_signal(cfg, df_entry, price, support, resistance, regime_direction)
        if signal is None and cfg.engulfing_mode_enabled:
            signal = evaluate_engulfing_signal(
                cfg, symbol, support, resistance, regime_direction, df_entry_15m=df_entry,
            )
        return signal
    else:
        if not cfg.reversion_mode_enabled:
            cfg.log.info("Mercado LATERAL pero reversion_mode_enabled=False — se omite, no se manda señal.")
            return None
        return evaluate_reversion_signal(cfg, df_entry, price, support, resistance)


def check_active_signal(cfg: BotConfig, symbol: str, state: dict):
    """Revisa si TP o SL se tocaron EN ALGÚN MOMENTO desde que se abrió la señal
    (no solo el precio actual), usando el high/low de las velas finas."""
    side = state["side"]
    tp = state["tp"]
    sl = state["sl"]
    opened_at = pd.Timestamp(state["opened_at"])
    if opened_at.tzinfo is None:
        opened_at = opened_at.tz_localize("UTC")

    now = pd.Timestamp.now(tz="UTC")
    elapsed_minutes = max(1, int((now - opened_at).total_seconds() / 60))
    interval_minutes = {"1": 1, "3": 3, "5": 5, "15": 15, "30": 30, "60": 60}.get(cfg.monitor_interval, 1)
    bars_needed = min(3000, elapsed_minutes // interval_minutes + 20)

    df = get_klines(cfg, symbol, cfg.monitor_interval, limit=bars_needed)
    df_since_open = df[df["start"] >= opened_at]

    if not df.empty and df["start"].iloc[0] > opened_at:
        cfg.log.warning(
            "La señal lleva abierta más tiempo del que cubren las %d velas traídas; "
            "puede haber quedado un tramo inicial sin revisar (mecha no detectada).",
            bars_needed,
        )

    if df_since_open.empty:
        price = get_last_price(cfg, symbol)
        df_since_open = pd.DataFrame([{"start": now, "open": price, "high": price, "low": price, "close": price}])

    result = None
    trigger_time = None
    exit_price = float(df_since_open["close"].iloc[-1])
    entry = state["entry"]

    # Al avanzar hasta el umbral configurado, asegura un porcentaje del
    # recorrido entrada->TP. El cálculo relativo funciona igual en LONG/SHORT.
    reward_total = tp - entry
    locked_sl = entry + cfg.breakeven_lock_pct * reward_total

    breakeven_time = None
    if state.get("breakeven_time"):
        breakeven_time = pd.Timestamp(state["breakeven_time"])
        if breakeven_time.tzinfo is None:
            breakeven_time = breakeven_time.tz_localize("UTC")
    elif cfg.breakeven_enabled and not state.get("breakeven_triggered", False):
        trigger_price = entry + cfg.breakeven_trigger_pct * reward_total
        if side == "LONG":
            be_hits = df_since_open[df_since_open["high"] >= trigger_price]
        else:
            be_hits = df_since_open[df_since_open["low"] <= trigger_price]
        if not be_hits.empty:
            breakeven_time = be_hits["start"].iloc[0]
            state["breakeven_triggered"] = True
            state["breakeven_time"] = breakeven_time.isoformat()
            save_state(cfg, state)
            cfg.log.info(
                "Protección activada: SL movido a %.2f (%.0f%% del camino; entrada=%.2f, tp=%.2f) desde %s",
                locked_sl, cfg.breakeven_lock_pct * 100, entry, tp, breakeven_time,
            )

    if breakeven_time is not None:
        before = df_since_open[df_since_open["start"] < breakeven_time]
        after = df_since_open[df_since_open["start"] >= breakeven_time]
        # El SL movido (=entrada + colchón) solo protege desde la vela
        # SIGUIENTE a la que activó el break-even — en la misma vela no
        # podemos saber si el mínimo ocurrió antes o después del máximo que
        # disparó el break-even.
        after_for_sl = df_since_open[df_since_open["start"] > breakeven_time]
    else:
        before = df_since_open
        after = df_since_open.iloc[0:0]
        after_for_sl = df_since_open.iloc[0:0]

    if side == "LONG":
        tp_hits = pd.concat([before[before["high"] >= tp], after[after["high"] >= tp]])
        sl_hits_before = before[before["low"] <= sl]
        sl_hits_after = after_for_sl[after_for_sl["low"] <= locked_sl]
    else:
        tp_hits = pd.concat([before[before["low"] <= tp], after[after["low"] <= tp]])
        sl_hits_before = before[before["high"] >= sl]
        sl_hits_after = after_for_sl[after_for_sl["high"] >= locked_sl]

    first_tp_time = tp_hits["start"].iloc[0] if not tp_hits.empty else None
    first_sl_before_time = sl_hits_before["start"].iloc[0] if not sl_hits_before.empty else None
    first_sl_after_time = sl_hits_after["start"].iloc[0] if not sl_hits_after.empty else None

    # Elegimos el evento que ocurrió PRIMERO en el tiempo entre los 3 posibles
    # (TP, SL original antes del break-even, SL=entrada+colchón después del
    # break-even). En empate exacto de horario, el TP tiene prioridad (igual
    # que antes).
    candidates = []
    if first_tp_time is not None:
        candidates.append(("GANADORA (TP)", first_tp_time, tp, 0))
    if first_sl_before_time is not None:
        candidates.append(("PERDEDORA (SL)", first_sl_before_time, sl, 1))
    if first_sl_after_time is not None:
        candidates.append((
            f"GANADORA (PROTECCIÓN, SL asegurado al {cfg.breakeven_lock_pct*100:.0f}% del camino)",
            first_sl_after_time, locked_sl, 1,
        ))

    if candidates:
        candidates.sort(key=lambda c: (c[1], c[3]))  # por tiempo, y TP gana empates
        result, trigger_time, exit_price, _ = candidates[0]

    if result:
        cfg.log.info("Señal %s tocó %s en la vela de %s (detectado por high/low, no solo el cierre)",
                      side, result, trigger_time)

    return result, exit_price


# ============================================================
# ENTRY POINTS (usados por cada script de instrumento)
# ============================================================

ESTRATEGIA_LABELS = {
    "reversion_sr_rsi": "📉 Reversión (S/R + RSI)",
    "tendencia_supertrend": "📈 Tendencia (Supertrend)",
    "tendencia_macd_bb": "📊 Tendencia (MACD + Bollinger)",
    "tendencia_engulfing_2m": "🕯️ Tendencia (Envolvente)",
}


def run_test(cfg: BotConfig):
    cfg.log.info("Probando conexión al feed de TradingView para %s ...", cfg.tv_symbol)
    df = get_klines(cfg, cfg.tv_symbol, cfg.entry_interval, limit=20)
    cfg.log.info("OK. Últimas %d velas recibidas. Último cierre: %.2f", len(df), df["close"].iloc[-1])
    print(df.tail(10).to_string(index=False))

    if cfg.telegram_bot_token or cfg.discord_webhook_url:
        cfg.log.info("Enviando mensaje de prueba a los canales configurados...")
        send_alert(cfg, f"✅ Prueba de conexión del bot {cfg.label} — si ves esto, la alerta llegó bien.")
    else:
        cfg.log.warning("No configuraste Telegram ni Discord. El bot correrá pero no te avisará nada.")

    if cfg.tradovate_trading_enabled:
        cfg.log.info("Auto-trading está activado — probando conexión a Tradovate (solo lectura, no manda órdenes)...")
        try:
            cfg.tradovate.authenticate()
            account_id, account_spec = cfg.tradovate.get_account()
            cfg.log.info("OK Tradovate. Entorno=%s | Cuenta=%s (id=%s)", cfg.tradovate_environment, account_spec, account_id)
            if cfg.tradovate_contract_symbol:
                contract = cfg.tradovate.find_contract(cfg.tradovate_contract_symbol)
                cfg.log.info("OK. Contrato '%s' encontrado: %s", cfg.tradovate_contract_symbol, contract)
            else:
                cfg.log.warning("No configuraste tradovate_contract_symbol todavía — hazlo antes de dejarlo corriendo.")
        except Exception as e:
            cfg.log.error("Fallo la conexión a Tradovate: %s", e)

    if cfg.mt5_trading_enabled:
        cfg.log.info("Auto-trading está activado — probando conexión a MT5/Vantage (solo lectura, no manda órdenes)...")
        try:
            cfg.mt5.connect()
            if cfg.mt5_symbol:
                resolved = cfg.mt5.resolve_symbol(cfg.mt5_symbol)
                cfg.log.info("OK. Símbolo '%s' resuelto como '%s' en tu terminal.", cfg.mt5_symbol, resolved)
            else:
                cfg.log.warning("No configuraste mt5_symbol todavía — hazlo antes de dejarlo corriendo.")
        except Exception as e:
            cfg.log.error("Fallo la conexión a MT5: %s", e)


def run_stats(cfg: BotConfig):
    print_stats(cfg)



def execute_trade_tradovate(cfg: BotConfig, signal: dict) -> str:
    """Ejecuta la señal como orden real (o demo) en Tradovate, con TP/SL puestos
    de una vez (bracket). Guarda el resultado en el propio dict de la señal
    (para que quede en el estado y en la bitácora) y devuelve un texto para
    agregar a la alerta de Telegram/Discord."""
    if not cfg.tradovate_contract_symbol:
        cfg.log.error("tradovate_trading_enabled=True pero no hay tradovate_contract_symbol configurado. No se ejecuta.")
        signal["tradovate_executed"] = False
        signal["tradovate_error"] = "Sin símbolo de contrato configurado"
        return "\n⚠️ Auto-trading activado pero falta el símbolo de contrato — NO se ejecutó la orden."

    try:
        result = cfg.tradovate.place_bracket_order(
            symbol=cfg.tradovate_contract_symbol,
            side=signal["side"],
            qty=cfg.tradovate_order_qty,
            tp_price=signal["tp"],
            sl_price=signal["sl"],
        )
        signal["tradovate_executed"] = True
        signal["tradovate_order_result"] = result
        cfg.log.info("Orden ejecutada en Tradovate (%s): %s", cfg.tradovate_environment, result)
        return (
            f"\n✅ Ejecutado en Tradovate ({cfg.tradovate_environment}): "
            f"{cfg.tradovate_order_qty}x {cfg.tradovate_contract_symbol}"
        )
    except Exception as e:
        signal["tradovate_executed"] = False
        signal["tradovate_error"] = str(e)
        cfg.log.error("Error ejecutando orden en Tradovate: %s", e)
        return f"\n❌ FALLÓ la ejecución en Tradovate: {e}"


def execute_trade_mt5(cfg: BotConfig, signal: dict) -> str:
    """Ejecuta la señal como orden real en tu cuenta de Vantage vía MT5, con
    SL/TP puestos de una vez y conserva el precio real de llenado."""
    if not cfg.mt5_symbol:
        cfg.log.error("mt5_trading_enabled=True pero no hay mt5_symbol configurado. No se ejecuta.")
        signal["mt5_executed"] = False
        signal["mt5_error"] = "Sin símbolo configurado"
        return "\n⚠️ Auto-trading activado pero falta el símbolo de MT5 — NO se ejecutó la orden."

    try:
        result = cfg.mt5.place_market_order(
            symbol_hint=cfg.mt5_symbol,
            side=signal["side"],
            volume=cfg.mt5_lot_size,
            sl=signal["sl"],
            tp=signal["tp"],
        )
        signal["mt5_executed"] = True
        signal["mt5_order_result"] = result
        cfg.log.info("Orden ejecutada en MT5 (Vantage): %s", result)

        real_price = result.get("price") if isinstance(result, dict) else None
        if real_price is not None:
            real_price = float(real_price)
            theoretical_entry = signal["entry"]
            signal["entry_teorica"] = theoretical_entry
            signal["entry"] = real_price
            cfg.log.info(
                "Precio real MT5: %.2f (teórico %.2f; deslizamiento %.2f).",
                real_price, theoretical_entry, real_price - theoretical_entry,
            )
        else:
            cfg.log.warning(
                "MT5 no devolvió precio real; se conserva la entrada teórica %.2f.", signal["entry"],
            )

        return (
            f"\n✅ Ejecutado en Vantage/MT5: {cfg.mt5_lot_size} lotes de "
            f"{cfg.mt5_symbol} a {signal['entry']:.2f}"
        )
    except Exception as e:
        signal["mt5_executed"] = False
        signal["mt5_error"] = str(e)
        cfg.log.error("Error ejecutando orden en MT5: %s", e)
        return f"\n❌ FALLÓ la ejecución en Vantage/MT5: {e}"


def execute_trade(cfg: BotConfig, signal: dict) -> str:
    """Despachador: usa el backend de ejecución que esté activo. Si ninguno
    está activo (caso normal), no debería llamarse — ver run_main."""
    if cfg.mt5_trading_enabled:
        return execute_trade_mt5(cfg, signal)
    if cfg.tradovate_trading_enabled:
        return execute_trade_tradovate(cfg, signal)
    return ""


def run_main(cfg: BotConfig):
    if cfg.mt5_trading_enabled:
        aviso = (
            f"⚠️ AUTO-TRADING ACTIVO en {cfg.label} vía Vantage/MT5 — "
            f"símbolo '{cfg.mt5_symbol}' x{cfg.mt5_lot_size} lotes. "
            f"Cada señal nueva se va a ejecutar SOLA en tu cuenta."
        )
        cfg.log.warning(aviso)
        send_alert(cfg, "🤖 " + aviso)
    elif cfg.tradovate_trading_enabled:
        aviso = (
            f"⚠️ AUTO-TRADING ACTIVO en {cfg.label} — entorno '{cfg.tradovate_environment}' — "
            f"contrato '{cfg.tradovate_contract_symbol}' x{cfg.tradovate_order_qty}. "
            f"Cada señal nueva se va a ejecutar SOLA en tu cuenta de Tradovate."
        )
        cfg.log.warning(aviso)
        if cfg.tradovate_environment == "live":
            cfg.log.warning("🔴 CUENTA REAL — esto opera con dinero real, no demo.")
        send_alert(cfg, "🤖 " + aviso)
    else:
        send_alert(cfg, f"🤖 Bot {cfg.label} iniciado. Buscando señales (reversión / tendencia / envolvente)...")

    while True:
        try:
            state = load_state(cfg)

            if state.get("active"):
                result, price = check_active_signal(cfg, cfg.tv_symbol, state)
                if result:
                    msg = (
                        f"📌 [{cfg.label}] Señal {state['side']} CERRADA: {result}\n"
                        f"Entrada: {state['entry']:.2f} | Salida: {price:.2f}\n"
                        f"TP: {state['tp']:.2f} | SL: {state['sl']:.2f}"
                    )
                    cfg.log.info(msg)
                    send_alert(cfg, msg)
                    log_signal_result(cfg, state, result, price)
                    save_state(cfg, {"active": False})
                else:
                    cfg.log.info("Señal %s activa. (esperando TP/SL)", state["side"])
            else:
                signal = evaluate_new_signal(cfg, cfg.tv_symbol)
                if signal:
                    signal["active"] = True

                    execution_txt = ""
                    if cfg.mt5_trading_enabled or cfg.tradovate_trading_enabled:
                        execution_txt = execute_trade(cfg, signal)

                    save_state(cfg, signal)

                    rr_txt = f"{signal['rr']:.2f}" if signal.get("rr") is not None else "N/A"
                    ajuste_txt = " (TP ajustado para mejorar R:R)" if signal.get("rr_ajustado") else ""
                    estrategia_txt = ESTRATEGIA_LABELS.get(signal.get("estrategia", ""), signal.get("estrategia", ""))

                    extra_lines = []
                    if signal.get("rsi") is not None:
                        extra_lines.append(f"RSI({cfg.rsi_period}): {signal['rsi']:.1f}")
                    if signal.get("support_ref") is not None and signal.get("resistance_ref") is not None:
                        extra_lines.append(
                            f"Soporte ref: {signal['support_ref']:.2f} | Resistencia ref: {signal['resistance_ref']:.2f}"
                        )
                    extra_txt = ("\n" + "\n".join(extra_lines)) if extra_lines else ""

                    msg = (
                        f"🚨 [{cfg.label}] NUEVA SEÑAL: {signal['side']} {cfg.tv_symbol}\n"
                        f"Estrategia: {estrategia_txt}\n"
                        f"Entrada: {signal['entry']:.2f}\n"
                        f"TP: {signal['tp']:.2f} | SL: {signal['sl']:.2f}\n"
                        f"R:R = 1:{rr_txt}{ajuste_txt}"
                        f"{extra_txt}"
                        f"{execution_txt}"
                    )
                    cfg.log.info(msg)
                    send_alert(cfg, msg)
                else:
                    cfg.log.info("Sin señal por ahora. Esperando condiciones de entrada.")

        except Exception as e:
            cfg.log.error("Error en el ciclo principal: %s", e)

        time.sleep(cfg.poll_seconds)


def run_cli(cfg: BotConfig):
    """Llamar desde el bloque `if __name__ == '__main__':` de cada script de instrumento."""
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        run_test(cfg)
    elif len(sys.argv) > 1 and sys.argv[1] == "stats":
        run_stats(cfg)
    else:
        run_main(cfg)
