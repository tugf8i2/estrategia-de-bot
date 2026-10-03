import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import signal_bot_core as core


class FakeMt5:
    def place_market_order(self, **kwargs):
        return {"order": 42, "price": 4310.25, "request": kwargs}


class CoreUpdateTests(unittest.TestCase):
    def test_volume_profile_ignores_empty_bins_when_calculating_threshold(self):
        candles = pd.DataFrame(
            [
                {"low": 100.0, "high": 101.0, "volume": 100.0},
                {"low": 100.5, "high": 101.5, "volume": 120.0},
                {"low": 109.0, "high": 110.0, "volume": 10.0},
            ]
        )
        levels = core.compute_volume_profile_levels(candles, bins=20, top_pct=0.7)
        self.assertEqual(len(levels), 1)
        self.assertLess(levels[0], 102.0)

    def test_liquidity_rejection_long_and_short(self):
        cfg = SimpleNamespace(
            sr_fractal_wing=2,
            liquidity_volume_bins=20,
            liquidity_volume_top_pct=0.7,
            liquidity_zone_tolerance_pct=0.002,
            liquidity_lookback_candles=3,
        )
        candles = pd.DataFrame(
            [
                {"low": 101.0, "high": 109.0, "close": 105.0},
                {"low": 99.0, "high": 108.0, "close": 101.0},
                {"low": 102.0, "high": 111.0, "close": 109.0},
            ]
        )
        with patch.object(core, "find_liquidity_zones", return_value=([100.0], [110.0])):
            self.assertEqual(core.detect_liquidity_rejection(cfg, candles, "LONG"), (True, 100.0))
            self.assertEqual(core.detect_liquidity_rejection(cfg, candles, "SHORT"), (True, 110.0))

    def test_percentage_profit_protection_closes_at_halfway_level(self):
        opened = pd.Timestamp("2026-10-02T10:00:00Z")
        candles = pd.DataFrame(
            [
                {"start": opened, "open": 100.0, "high": 104.0, "low": 99.0, "close": 103.0},
                {"start": opened + pd.Timedelta(minutes=1), "open": 103.0, "high": 109.0, "low": 102.0, "close": 108.0},
                {"start": opened + pd.Timedelta(minutes=2), "open": 108.0, "high": 108.5, "low": 105.0, "close": 106.0},
            ]
        )
        cfg = SimpleNamespace(
            monitor_interval="1",
            breakeven_enabled=True,
            breakeven_trigger_pct=0.9,
            breakeven_lock_pct=0.5,
            log=logging.getLogger("test-profit-protection"),
        )
        state = {
            "side": "LONG", "entry": 100.0, "tp": 110.0, "sl": 95.0,
            "opened_at": opened.isoformat(),
        }
        with patch.object(core, "get_klines", return_value=candles), patch.object(core, "save_state"):
            result, exit_price = core.check_active_signal(cfg, "VANTAGE:XAUUSD", state)

        self.assertIn("SL asegurado al 50%", result)
        self.assertEqual(exit_price, 105.0)
        self.assertTrue(state["breakeven_triggered"])

    def test_mt5_real_fill_replaces_theoretical_entry(self):
        cfg = SimpleNamespace(
            mt5_symbol="XAUUSD+", mt5_lot_size=0.01, mt5=FakeMt5(),
            log=logging.getLogger("test-mt5-fill"),
        )
        signal = {"side": "LONG", "entry": 4309.80, "sl": 4299.0, "tp": 4320.0}
        message = core.execute_trade_mt5(cfg, signal)

        self.assertEqual(signal["entry_teorica"], 4309.80)
        self.assertEqual(signal["entry"], 4310.25)
        self.assertIn("4310.25", message)

    def test_hostless_persistence_and_secret_safety_are_preserved(self):
        source = Path(core.__file__).read_text(encoding="utf-8")
        self.assertIn('os.environ.get("SIGNAL_DATA_DIR"', source)
        self.assertIn("restore_file", source)
        self.assertIn("persist_file", source)
        self.assertIn('os.environ.get("TELEGRAM_BOT_TOKEN", "")', source)
        self.assertNotIn("api/webhooks/1462986508352098378", source)

    def test_xauusd_explicitly_enables_new_liquidity_switch(self):
        source = (Path(core.__file__).parent / "tv_xauusd_signals.py").read_text(encoding="utf-8")
        self.assertIn("liquidity_filter_enabled=True", source)


if __name__ == "__main__":
    unittest.main()
