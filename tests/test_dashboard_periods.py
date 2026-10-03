import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dashboard


class DashboardPeriodTests(unittest.TestCase):
    def test_monthly_and_yearly_breakdown_uses_persisted_history(self):
        rows = [
            {"cerrada_en": "2026-09-25T13:00:00+00:00", "resultado": "GANADORA", "puntos": "10", "estrategia": "reversion"},
            {"cerrada_en": "2026-09-26T13:00:00+00:00", "resultado": "PERDEDORA", "puntos": "-4", "estrategia": "reversion"},
            {"cerrada_en": "2026-10-01T13:00:00+00:00", "resultado": "GANADORA", "puntos": "7.5", "estrategia": "engulfing"},
            {"cerrada_en": "2027-01-02T13:00:00+00:00", "resultado": "GANADORA", "puntos": "3", "estrategia": "reversion"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            history = Path(temp_dir) / "history.csv"
            with history.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            with patch.object(dashboard, "HISTORY_FILE", history), patch.object(
                dashboard, "STATE_FILE", Path(temp_dir) / "missing-state.json"
            ):
                data = dashboard.load_data()

        months = {item["period"]: item for item in data["months"]}
        days = {item["period"]: item for item in data["days"]}
        self.assertEqual(days["2026-09-25"]["net"], 10.0)
        self.assertEqual(days["2026-09-26"]["net"], -4.0)
        self.assertEqual(set(months), {"2026-09", "2026-10", "2027-01"})
        self.assertEqual(months["2026-09"]["wins"], 1)
        self.assertEqual(months["2026-09"]["losses"], 1)
        self.assertEqual(months["2026-09"]["net"], 6.0)
        self.assertEqual(months["2026-10"]["net"], 7.5)

        years = {item["period"]: item for item in data["years"]}
        self.assertEqual(years["2026"]["trades"], 3)
        self.assertEqual(years["2026"]["net"], 13.5)
        self.assertEqual([month["period"] for month in years["2026"]["months"]], ["2026-10", "2026-09"])
        self.assertEqual(years["2027"]["net"], 3.0)

    def test_invalid_close_date_is_kept_out_of_period_totals(self):
        row = {"cerrada_en": "fecha-invalida", "resultado": "GANADORA", "puntos": "5", "estrategia": "x"}
        with tempfile.TemporaryDirectory() as temp_dir:
            history = Path(temp_dir) / "history.csv"
            with history.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=row.keys())
                writer.writeheader()
                writer.writerow(row)
            with patch.object(dashboard, "HISTORY_FILE", history), patch.object(
                dashboard, "STATE_FILE", Path(temp_dir) / "missing-state.json"
            ), patch.object(dashboard, "STATS_FROM_DATE", ""):
                data = dashboard.load_data()

        self.assertEqual(data["total"], 1)
        self.assertEqual(data["days"], [])
        self.assertEqual(data["months"], [])
        self.assertEqual(data["years"], [])


if __name__ == "__main__":
    unittest.main()
