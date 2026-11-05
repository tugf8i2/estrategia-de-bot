import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import bot_nas100_diario as daily


class DailyBotTests(unittest.TestCase):
    def test_schedule_stays_at_1710_colombia_across_new_york_dst_change(self):
        colombia = ZoneInfo("America/Bogota")
        new_york = ZoneInfo("America/New_York")

        october = datetime(2026, 10, 31, 17, 10, tzinfo=colombia)
        november = datetime(2026, 11, 2, 17, 10, tzinfo=colombia)

        self.assertEqual(daily.proxima_ejecucion(october).isoformat(), "2026-11-01T17:10:00-05:00")
        self.assertEqual(october.astimezone(new_york).strftime("%H:%M"), "18:10")
        self.assertEqual(november.astimezone(new_york).strftime("%H:%M"), "17:10")
        friday_before_dst_change = datetime(2026, 10, 30, 17, 10, tzinfo=colombia)
        self.assertEqual(daily.hora_apertura_colombia(friday_before_dst_change).strftime("%H:%M"), "09:30")
        self.assertEqual(daily.hora_apertura_colombia(october).strftime("%H:%M"), "09:30")
        self.assertEqual(
            daily.hora_apertura_colombia(datetime(2026, 10, 29, 17, 10, tzinfo=colombia)).strftime("%H:%M"),
            "08:30",
        )
        self.assertEqual(daily.hora_apertura_colombia(november).strftime("%H:%M"), "09:30")

    def test_report_compares_current_allocation_to_next_open_target(self):
        res = {
            "NAS100": {"w_manana": 0.5, "texto": ["NAS100: lectura"]},
            "ORO": {"w_manana": 0.0, "texto": ["ORO: lectura"]},
        }
        actual = {"NAS100": 50.0, "ORO": 0.0, "EFECTIVO": 50.0}
        report = daily.construir_informe(
            res,
            ahora=datetime(2026, 10, 20, 17, 10, tzinfo=ZoneInfo("America/Bogota")),
            asignacion_actual=actual,
        )

        self.assertIn("NAS100 25% | ORO 0% | Efectivo 75%", report)
        self.assertIn("Horario de referencia de apertura: 8:30 a. m.", report)
        self.assertIn("NAS100 -25 puntos porcentuales", report)
        self.assertIn("EFECTIVO +25 puntos porcentuales", report)

    def test_weekend_rerun_for_same_friday_close_does_not_send_duplicate_alert(self):
        result = {
            "fecha": "2026-10-23",
            "w_hoy": 0.0,
            "w_manana": 0.5,
            "texto": ["NAS100: lectura de viernes"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "estado.json"
            with patch.object(daily, "ESTADO", str(state_file)), patch.object(
                daily, "analizar", return_value=result
            ), patch("signal_bot_core.send_alert") as send_alert:
                self.assertTrue(daily.main(argv=[], asignacion_actual=daily.ASIGNACION_INICIAL))
                self.assertTrue(daily.main(argv=[], asignacion_actual=daily.ASIGNACION_INICIAL))
                self.assertTrue(state_file.exists())

        self.assertEqual(send_alert.call_count, 1)


if __name__ == "__main__":
    unittest.main()
