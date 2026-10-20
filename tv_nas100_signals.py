#!/usr/bin/env python3
"""
Bot de señales — VANTAGE:NAS100 (Nasdaq 100), SOLO estrategia de REVERSIÓN

Usa el mismo motor que el bot de oro (signal_bot_core.py), sin cambios en el
núcleo. Aquí se apaga el modo tendencia (trend_mode_enabled=False), así que el
bot siempre evalúa la reversión: soporte/resistencia de 4H + RSI de 15m con
confirmación de giro + rechazo en vela cerrada.

Nota: el bot lee el símbolo (VANTAGE:NAS100), no tu layout guardado de
TradingView. Lo que pongas en el gráfico no cambia las señales; cambian los
parámetros de abajo.

⚠️ tp_points y sl_buffer_points_fallback son los valores base del núcleo para
NAS100 (~28,000 puntos). Son un punto de partida, NO un valor probado.
Ajústalos con la bitácora (`python tv_nas100_signals.py stats`).

⚠️ Esta reversión no está validada con un backtest. Úsala primero solo para
avisos y revisa los resultados antes de activar cualquier auto-trading.

Uso:
    python tv_nas100_signals.py           -> corre el bot en vivo
    python tv_nas100_signals.py test      -> prueba la conexión y las alertas
    python tv_nas100_signals.py stats     -> muestra el resumen de la bitácora
"""

from signal_bot_core import BotConfig, run_cli

cfg = BotConfig(
    tv_symbol="VANTAGE:NAS100",
    label="NAS100",

    # TP/SL en puntos de NAS100. El SL real lo define el ATR (atr_sl_multiplier);
    # sl_buffer_points_fallback solo se usa si el ATR no se puede calcular.
    tp_points=110.0,
    sl_buffer_points_fallback=40.0,

    # --- Solo reversión ---
    # Con trend_mode_enabled=False el bot nunca entra en modo tendencia:
    # siempre busca reversión en soporte/resistencia, aunque haya tendencia.
    trend_mode_enabled=False,
    reversion_mode_enabled=True,
    reversion_range_filter_enabled=False,
    reversion_range_min_atr=2.0,
    reversion_range_max_atr=8.0,
    reversion_require_closed_candle_rejection=True,

    # Apagados porque pertenecen al modo tendencia (sin efecto con
    # trend_mode_enabled=False, se dejan explícitos para que se vea).
    supertrend_entry_enabled=False,
    macd_bb_entry_enabled=False,
    engulfing_mode_enabled=False,

    # --- Auto-trading en Vantage vía MT5 (APAGADO por defecto — el bot solo avisa) ---
    mt5_trading_enabled=False,
    # Verifica el nombre exacto en el Market Watch de tu MT5 (puede ser "NAS100",
    # "NAS100+", etc.). Mientras mt5_trading_enabled=False no se usa.
    mt5_symbol="NAS100+",
    mt5_lot_size=0.10,  # ajústalo a tu cuenta (0.01=micro, 0.10=mini, 1.0=estándar)
)

if __name__ == "__main__":
    run_cli(cfg)
