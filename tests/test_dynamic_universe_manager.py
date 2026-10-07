"""Offline regression tests for the universe cache lifecycle.

Run with: python -m unittest discover -s tests -v
"""

import asyncio
import json
import logging
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core_v2 import dynamic_universe_manager as module


class FrozenDatetime(datetime):
    current = datetime(2026, 10, 7, 12, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.current


class UniverseCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache_dir = Path(directory.name)
        self.start = datetime(2026, 10, 7, 12, 0)
        FrozenDatetime.current = self.start
        clock = patch.object(module, "datetime", FrozenDatetime)
        clock.start()
        self.addCleanup(clock.stop)
        core = patch.object(module.DynamicUniverseManager, "_fetch_sp500_list", return_value=["AAPL"])
        core.start()
        self.addCleanup(core.stop)
        self.manager = self.make_manager()

    def make_manager(self):
        manager = module.DynamicUniverseManager(None, self.cache_dir)
        manager._fetch_yfinance_screeners = AsyncMock(return_value={"HOT": "yfinance_gainers"})
        manager._fetch_twelve_data_movers = AsyncMock(return_value={})
        manager._fetch_polygon_movers = AsyncMock(return_value={})
        return manager

    def refresh(self, manager=None):
        return asyncio.run((manager or self.manager).refresh_universe())

    def test_first_refresh_persists_current_timestamp_and_stocks(self):
        self.assertIsNone(self.manager.last_refresh)
        self.assertTrue(self.refresh())
        snapshot = json.loads(self.manager.cache_file.read_text())
        self.assertEqual(snapshot["last_refresh"], self.start.isoformat())
        self.assertEqual(self.manager.last_refresh, self.start)
        self.assertEqual(set(snapshot["stocks"]), {"AAPL", "HOT"})
        self.assertEqual(snapshot["total_stocks"], 2)

    def test_restart_within_interval_does_not_need_refresh(self):
        self.assertTrue(self.refresh())
        FrozenDatetime.current = self.start + timedelta(minutes=59, seconds=59)
        restarted = self.make_manager()
        self.assertTrue(restarted.load_universe())
        self.assertEqual(restarted.last_refresh, self.start)
        self.assertEqual(set(restarted.universe), {"AAPL", "HOT"})
        self.assertFalse(restarted.should_refresh())

    def test_restart_at_and_after_interval_refreshes_and_persists_new_time(self):
        for elapsed in (timedelta(hours=1), timedelta(hours=1, seconds=1)):
            with self.subTest(elapsed=elapsed):
                FrozenDatetime.current = self.start
                self.assertTrue(self.refresh())
                FrozenDatetime.current = self.start + elapsed
                restarted = self.make_manager()
                self.assertTrue(restarted.load_universe())
                self.assertTrue(restarted.should_refresh())
                self.assertTrue(self.refresh(restarted))
                snapshot = json.loads(restarted.cache_file.read_text())
                self.assertEqual(snapshot["last_refresh"], FrozenDatetime.current.isoformat())
                self.assertFalse(restarted.should_refresh())

    def test_failed_cache_write_returns_false_and_keeps_refresh_due(self):
        for previous in (None, self.start - timedelta(hours=2)):
            with self.subTest(previous=previous):
                self.manager.last_refresh = previous
                # Exercise persist_universe's actual exception handling without
                # relying on filesystem permissions (tests may run as root).
                with patch("builtins.open", side_effect=OSError("disk full")):
                    with self.assertLogs(module.logger, level=logging.INFO) as logs:
                        self.assertFalse(self.refresh())
                self.assertEqual(self.manager.last_refresh, previous)
                self.assertTrue(self.manager.should_refresh())
                self.assertTrue(any("Failed to persist universe" in line for line in logs.output))
                self.assertFalse(any("Universe refresh complete" in line for line in logs.output))
                self.assertTrue(self.refresh())
                self.assertFalse(self.manager.should_refresh())


if __name__ == "__main__":
    unittest.main()
