import csv
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import signal_bot_core as core


def cfg(**overrides):
    values = dict(
        proximity_tolerance_pct=0.002,
        atr_period=14,
        atr_sl_multiplier=1.5,
        sl_buffer_points_fallback=5.0,
        rsi_period=14,
        rsi_dynamic_enabled=False,
        rsi_oversold=35.0,
        rsi_overbought=65.0,
        rsi_require_turn_confirmation=True,
        touch_lookback_candles=5,
        entry_interval="15",
        tp_points=15.0,
        min_rr_target=1.0,
        max_tp_extension_multiplier=1.5,
        reversion_range_filter_enabled=True,
        reversion_range_min_atr=2.0,
        reversion_range_max_atr=8.0,
        reversion_require_closed_candle_rejection=True,
        log=logging.getLogger("test-reversion-quality"),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class ReversionQualityTests(unittest.TestCase):
    def test_range_requires_two_levels_and_reasonable_atr_width(self):
        config = cfg()
        htf = pd.DataFrame({"high": [110.0] * 30, "low": [100.0] * 30, "close": [105.0] * 30})
        with patch.object(core, "compute_atr", return_value=pd.Series([2.0] * 30)):
            valid, width, _ = core.evaluate_reversion_range(config, htf, 105.0, 100.0, 110.0)
            too_wide, _, _ = core.evaluate_reversion_range(config, htf, 105.0, 80.0, 110.0)
            missing, _, _ = core.evaluate_reversion_range(config, htf, 105.0, None, 110.0)
        self.assertTrue(valid)
        self.assertEqual(width, 5.0)
        self.assertFalse(too_wide)
        self.assertFalse(missing)

    def test_rejection_must_be_on_latest_closed_candle(self):
        config = cfg()
        old_touch = {"open": 101.0, "high": 103.0, "low": 99.9, "close": 101.0}
        no_touch = {"open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0}
        side, level = core.detect_closed_candle_rejection(config, pd.Series(old_touch), 100.0, 110.0)
        stale_side, _ = core.detect_closed_candle_rejection(config, pd.Series(no_touch), 100.0, 110.0)
        self.assertEqual((side, level), ("LONG", 100.0))
        self.assertIsNone(stale_side)

    def test_old_touch_does_not_create_signal_without_current_rejection(self):
        config = cfg()
        rows = [
            {"open": 102.0, "high": 103.0, "low": 99.9, "close": 101.0, "volume": 10.0},
            {"open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0, "volume": 10.0},
        ] * 10
        rows[-2] = {"open": 103.0, "high": 104.0, "low": 102.0, "close": 103.0, "volume": 10.0}
        rows[-1] = {"open": 103.0, "high": 105.0, "low": 102.0, "close": 104.0, "volume": 10.0}
        entry = pd.DataFrame(rows)
        htf = pd.DataFrame({"high": [110.0] * 30, "low": [100.0] * 30, "close": [105.0] * 30})
        rsi = pd.Series([30.0] * 19 + [32.0])
        atr = pd.Series([2.0] * 30)
        with patch.object(core, "compute_rsi", return_value=rsi), patch.object(core, "compute_atr", return_value=atr):
            signal = core.evaluate_reversion_signal(
                config, entry, 104.0, 100.0, 110.0, df_htf=htf, current_adx=16.3,
            )
        self.assertIsNone(signal)

    def test_confirmed_rejection_records_entry_factors(self):
        config = cfg()
        entry = pd.DataFrame([
            {"open": 102.0, "high": 103.0, "low": 101.0, "close": 102.0, "volume": 10.0}
            for _ in range(19)
        ] + [{"open": 101.0, "high": 102.0, "low": 99.9, "close": 100.8, "volume": 10.0}])
        htf = pd.DataFrame({"high": [110.0] * 30, "low": [100.0] * 30, "close": [105.0] * 30})
        rsi = pd.Series([30.0] * 19 + [32.0])
        atr = pd.Series([2.0] * 30)
        with patch.object(core, "compute_rsi", return_value=rsi), patch.object(core, "compute_atr", return_value=atr):
            signal = core.evaluate_reversion_signal(
                config, entry, 100.8, 100.0, 110.0, df_htf=htf, current_adx=16.3,
            )
        self.assertEqual(signal["side"], "LONG")
        self.assertEqual(signal["adx_4h"], 16.3)
        self.assertEqual(signal["rango_4h_atr"], 5.0)
        self.assertEqual(signal["rechazo_nivel"], 100.0)
        self.assertIn("vela 15m cerrada", signal["razon_entrada"])

    def test_old_history_schema_is_migrated_without_losing_rows(self):
        old_fields = core.HISTORY_FIELDS[:14]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "history.csv"
            original = {field: "" for field in old_fields}
            original.update({"resultado": "GANADORA", "puntos": "10", "estrategia": "reversion_sr_rsi"})
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=old_fields)
                writer.writeheader()
                writer.writerow(original)
            core.ensure_history_schema(str(path))
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
            self.assertEqual(reader.fieldnames, core.HISTORY_FIELDS)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["puntos"], "10")
            self.assertEqual(rows[0]["adx_4h"], "")


if __name__ == "__main__":
    unittest.main()
