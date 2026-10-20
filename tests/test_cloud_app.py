import unittest
from unittest.mock import patch

import cloud_app


class CloudAppTests(unittest.TestCase):
    def test_starts_workers_for_both_instruments(self):
        with patch("cloud_app.threading.Thread") as thread:
            threads = cloud_app.start_bot_workers()

        configs = [call.kwargs["args"][0] for call in thread.call_args_list]
        self.assertEqual([config.tv_symbol for config in configs], ["VANTAGE:XAUUSD", "VANTAGE:NAS100"])
        self.assertEqual(len(threads), 2)
        self.assertEqual(thread.call_count, 2)
        self.assertEqual(thread.return_value.start.call_count, 2)


if __name__ == "__main__":
    unittest.main()
