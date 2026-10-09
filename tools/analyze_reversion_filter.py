#!/usr/bin/env python3
"""Compara candidatos de reversión antiguos/nuevos sobre velas históricas.

Es un diagnóstico de selectividad, no un backtest de P&L: no simula TP/SL.
"""

import argparse

import pandas as pd

from signal_bot_core import (
    compute_adx,
    compute_dynamic_rsi_thresholds,
    compute_macd_direction,
    compute_rsi,
    detect_closed_candle_rejection,
    evaluate_reversion_range,
    get_nearest_support_resistance,
)
from tv_xauusd_signals import cfg


def load(path):
    frame = pd.read_csv(path)
    frame["start"] = pd.to_datetime(frame["start"], utc=True)
    return frame.sort_values("start").reset_index(drop=True)


def analyze(entry, htf):
    old_candidates = new_candidates = stale_only = 0
    for i in range(max(200, cfg.rsi_period + 2), len(entry) - 1):
        closed = entry.iloc[: i + 1].tail(200)
        timestamp = closed["start"].iloc[-1]
        known_htf = htf[htf["start"] < timestamp].tail(cfg.sr_lookback_candles)
        if len(known_htf) < 50:
            continue
        price = float(closed["close"].iloc[-1])
        support, resistance = get_nearest_support_resistance(cfg, known_htf, price)
        direction = compute_macd_direction(known_htf, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
        recent_dir = direction.tail(cfg.trend_confirm_candles)
        direction_confirmed = (
            len(recent_dir) == cfg.trend_confirm_candles
            and not recent_dir.isna().any() and recent_dir.nunique() == 1
        )
        adx = float(compute_adx(known_htf, cfg.adx_period).iloc[-1])
        if direction_confirmed and adx >= cfg.adx_threshold:
            continue

        rsi = compute_rsi(closed["close"], cfg.rsi_period)
        now, prev = float(rsi.iloc[-1]), float(rsi.iloc[-2])
        low, high = compute_dynamic_rsi_thresholds(cfg, rsi)
        long_ok = now <= low and now > prev
        short_ok = now >= high and now < prev
        recent = closed.tail(cfg.touch_lookback_candles)
        old_long = support is not None and bool(
            (abs(recent["low"] - support) / support <= cfg.proximity_tolerance_pct).any()
        ) and long_ok
        old_short = resistance is not None and bool(
            (abs(recent["high"] - resistance) / resistance <= cfg.proximity_tolerance_pct).any()
        ) and short_ok
        old = old_long or old_short
        old_candidates += int(old)

        range_ok, _, _ = evaluate_reversion_range(cfg, known_htf, price, support, resistance)
        side, _ = detect_closed_candle_rejection(cfg, closed.iloc[-1], support, resistance)
        new = range_ok and ((side == "LONG" and long_ok) or (side == "SHORT" and short_ok))
        new_candidates += int(new)
        stale_only += int(old and not new)
    return old_candidates, new_candidates, stale_only


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("entry_csv")
    parser.add_argument("htf_csv")
    args = parser.parse_args()
    old, new, stale = analyze(load(args.entry_csv), load(args.htf_csv))
    print(f"candidatos_criterio_anterior={old}")
    print(f"candidatos_criterio_nuevo={new}")
    print(f"candidatos_descartados={stale}")
