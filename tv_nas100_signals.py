#!/usr/bin/env python3
"""
Bot de señales — VANTAGE:NAS100FT (Nasdaq 100)

Estrategia ÚNICA: zonas de soporte/resistencia en 4H + gatillo RSI(14) en 15m.

  VENTA:  precio dentro de zona de RESISTENCIA y RSI 15m > 70
  COMPRA: precio dentro de zona de SOPORTE   y RSI 15m < 30

  - Si el RSI 4H < 40 se cancelan las COMPRAS.
  - Si el precio ya tocó o penetró la zona pero el RSI aún no coincide, la zona
    queda armada y el bot espera a que el RSI coincida para entrar.
  - TP = 110 y SL = 100 puntos, fijos. Protección: al llegar al 90% del camino
    hacia el TP, el SL sube al 50% de ese recorrido.

Uso:
    python tv_nas100_signals.py           -> corre el bot en vivo
    python tv_nas100_signals.py test      -> prueba la conexión y las alertas
    python tv_nas100_signals.py stats     -> muestra el resumen de la bitácora
"""

from signal_bot_core import BotConfig, run_cli

cfg = BotConfig(
    tv_symbol="VANTAGE:NAS100FT",   # fuente de las velas
    label="NAS100",

    sr_interval="240",      # 4H: zonas S/R y RSI de filtro
    entry_interval="15",    # 15m: gatillo RSI
    rsi_period=14,          # RSI 14 en 15m y 4H
    rsi_oversold=30.0,
    rsi_overbought=70.0,

    zone_rsi_mode_enabled=True,
    htf_rsi_buy_block_below=40.0,

    # TP/SL fijos en puntos desde la entrada
    fixed_tp_sl_enabled=True,
    tp_points=110.0,
    sl_points=100.0,

    # Protección: a 90% del camino al TP, el SL sube al 50% del recorrido
    breakeven_enabled=True,
    breakeven_trigger_pct=0.9,
    breakeven_lock_pct=0.5,

    # Reglas anteriores apagadas
    trend_mode_enabled=False,
    reversion_mode_enabled=False,
    supertrend_entry_enabled=False,
    macd_bb_entry_enabled=False,
    engulfing_mode_enabled=False,
    adx_filter_enabled=False,
    min_candle_body_filter_enabled=False,
    liquidity_filter_enabled=False,

    # Auto-trading MT5 (APAGADO: el bot solo avisa)
    mt5_trading_enabled=False,
    mt5_symbol="NAS100+",
    mt5_lot_size=0.10,
)

if __name__ == "__main__":
    run_cli(cfg)
