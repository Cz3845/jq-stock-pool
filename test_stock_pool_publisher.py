import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import stock_pool_publisher


class StockPoolPublisherTests(unittest.TestCase):
    def test_load_stock_pool_file_returns_empty_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "stock_pool.json"

            self.assertEqual(stock_pool_publisher.load_stock_pool_file(path), {})

    def test_update_stock_pool_data_preserves_old_dates_and_applies_max_size(self):
        existing = {
            "2026-05-03": ["000001"],
        }

        result = stock_pool_publisher.update_stock_pool_data(
            existing,
            "2026-05-04",
            ["600000", "300750", "688981"],
            max_size=2,
        )

        self.assertEqual(result, {
            "2026-05-03": ["000001"],
            "2026-05-04": ["600000", "300750"],
        })
        self.assertEqual(existing, {"2026-05-03": ["000001"]})

    def test_write_stock_pool_file_writes_deterministic_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "stock_pool.json"

            stock_pool_publisher.write_stock_pool_file(path, {
                "2026-05-04": ["600000"],
                "2026-05-03": ["000001"],
            })

            self.assertEqual(path.read_text(encoding="utf-8"), '{\n  "2026-05-03": [\n    "000001"\n  ],\n  "2026-05-04": [\n    "600000"\n  ]\n}\n')

    def test_run_update_dry_run_does_not_write_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "stock_pool.json"
            with patch.object(stock_pool_publisher, "fetch_eastmoney_stock_codes", return_value=stock_pool_publisher.FetchResult(True, ["000001"], "")), \
                    redirect_stdout(io.StringIO()):
                success = stock_pool_publisher.run_update("2026-05-04", path, dry_run=True, max_size=15)

            self.assertTrue(success)
            self.assertFalse(path.exists())

    def test_run_update_does_not_modify_file_when_fetch_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "stock_pool.json"
            path.write_text('{\n  "2026-05-03": [\n    "000001"\n  ]\n}\n', encoding="utf-8")
            original = path.read_text(encoding="utf-8")

            with patch.object(stock_pool_publisher, "fetch_eastmoney_stock_codes", return_value=stock_pool_publisher.FetchResult(False, [], "network")), \
                    redirect_stdout(io.StringIO()):
                success = stock_pool_publisher.run_update("2026-05-04", path, dry_run=False, max_size=15)

            self.assertFalse(success)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_run_update_writes_empty_list_when_fetch_succeeds_with_empty_codes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "stock_pool.json"

            with patch.object(stock_pool_publisher, "fetch_eastmoney_stock_codes", return_value=stock_pool_publisher.FetchResult(True, [], "")), \
                    redirect_stdout(io.StringIO()):
                success = stock_pool_publisher.run_update("2026-05-04", path, dry_run=False, max_size=15)

            self.assertTrue(success)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"2026-05-04": []})


if __name__ == "__main__":
    unittest.main()
