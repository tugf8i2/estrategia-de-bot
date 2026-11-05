import unittest
from unittest.mock import patch

import cloud_app


class CloudAppTests(unittest.TestCase):
    def test_starts_bots_and_daily_scheduler(self):
        with patch("cloud_app.threading.Thread") as thread:
            threads = cloud_app.start_bot_workers()
            scheduler = cloud_app.start_daily_scheduler()

        calls = thread.call_args_list
        configs = [call.kwargs["args"][0] for call in calls[:2]]
        self.assertEqual([config.tv_symbol for config in configs], ["VANTAGE:XAUUSD", "VANTAGE:NAS100"])
        self.assertEqual(len(threads), 2)
        self.assertEqual(thread.call_count, 3)
        self.assertEqual(thread.return_value.start.call_count, 3)
        self.assertEqual(scheduler, thread.return_value)
        self.assertEqual(calls[2].kwargs["target"], cloud_app.ejecutar_programacion_diaria)


if __name__ == "__main__":
    unittest.main()
