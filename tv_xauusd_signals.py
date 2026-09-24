#!/usr/bin/env python3
"""
Bot de señales — VANTAGE:XAUUSD (oro)

Mismo motor que el bot de NAS100 (signal_bot_core.py), pero con los puntos de
TP/SL recalibrados a la escala de precio del oro — en NAS100 (~28,000 puntos)
110 puntos es razonable, pero en oro (~2,000-3,000 USD) esos mismos "110
puntos" serían un movimiento gigante. El resto de los parámetros (RSI,
Supertrend, tolerancias en %, ATR) ya son relativos y no necesitan ajuste.

⚠️ tp_points y sl_buffer_points_fallback de abajo son un punto de partida
razonable, NO un valor definitivo — ajústalos con los primeros resultados
reales de la bitácora (`python tv_xauusd_signals.py stats`).

Uso:
    python tv_xauusd_signals.py           -> corre el bot en vivo
    python tv_xauusd_signals.py test       -> prueba la conexión y las alertas
    python tv_xauusd_signals.py stats      -> muestra el resumen de la bitácora
"""

from signal_bot_core import BotConfig, run_cli

cfg = BotConfig(
    tv_symbol="VANTAGE:XAUUSD",
    label="XAUUSD",

    # Punto de partida para la escala de precio del oro (USD por onza).
    # El SL real casi siempre lo define el ATR (auto-adaptado); esto es solo
    # el objetivo de TP y el respaldo si el ATR no se puede calcular.
    tp_points=15.0,
    sl_buffer_points_fallback=5.0,

    # --- Interruptores de estrategia (activá/desactivá cuando quieras) ---
    # reversion: se usa cuando el mercado está LATERAL.
    # supertrend / engulfing: se usan cuando hay TENDENCIA confirmada en 4H
    # (supertrend primero, engulfing como relleno si supertrend no dispara).
    # Las 3 son independientes entre sí — podés apagar cualquier combinación.
    trend_indicator="macd",          # dirección de tendencia via MACD (antes Supertrend, iba mucho en contra)
    reversion_mode_enabled=True,     # entrada por soporte/resistencia + RSI en lateral
    supertrend_entry_enabled=False,   # sin efecto mientras trend_indicator="macd" (queda por si volvés a Supertrend)
    macd_bb_entry_enabled=False,     # apagado a propósito: NO usar la entrada MACD-BB, solo el MACD para la dirección
    engulfing_mode_enabled=True,     # única entrada de tendencia activa ahora

    # --- Filtros de calidad adicionales (no quitan nada, solo agregan exigencia extra) ---
    # Afectan tanto a la entrada de Supertrend como a la de envolvente por igual.
    adx_filter_enabled=True,
    adx_threshold=23.0,              # subido un poco (era 20) — moderado, no tan estricto como 25
    min_candle_body_filter_enabled=True,
    min_candle_body_atr_ratio=0.4,   # subido un poco (era 0.3) — la vela debe tener cuerpo >= 40% del ATR

    # --- Auto-trading en Vantage vía MT5 (APAGADO por defecto — el bot solo avisa) ---
    mt5_trading_enabled=False,
    # Símbolo tal como aparece en el Market Watch de tu MT5 de Vantage (revisalo
    # ahí, puede tener sufijo, ej "XAUUSD" o "XAUUSD.a").
    mt5_symbol="XAUUSD+",
    mt5_lot_size=0.01,  # 0.01=micro, 0.10=mini, 1.0=estándar — ajustalo a tu cuenta
)

if __name__ == "__main__":
    run_cli(cfg)
