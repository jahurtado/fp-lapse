"""Tests for `ScheduleStateStore` — runtime/schedule_state.json."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.schedule.store import ScheduleStateStore  # noqa: E402


class _StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "schedule_state.json"
        self.store = ScheduleStateStore(self.path)

    def tearDown(self):
        self._tmp.cleanup()


class TestFirstBoot(_StoreTestCase):
    def test_missing_file_creates_default_false(self):
        result = self.store.load()
        self.assertFalse(result)
        self.assertFalse(self.store.was_reset_from_corruption)
        self.assertTrue(self.path.exists())
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data, {"version": 1, "schedule_enabled": False})


class TestRoundtrip(_StoreTestCase):
    def test_save_true_then_load_returns_true(self):
        self.store.save(True)
        self.assertTrue(self.store.load())

    def test_save_false_then_load_returns_false(self):
        self.store.save(False)
        self.assertFalse(self.store.load())

    def test_save_overwrite(self):
        self.store.save(True)
        self.store.save(False)
        self.assertFalse(self.store.load())

    def test_load_then_save_then_reload_survives_restart(self):
        self.store.save(True)
        # Simulate a new process by re-instantiating.
        other = ScheduleStateStore(self.path)
        self.assertTrue(other.load())


class TestCorruptionRescue(_StoreTestCase):
    def test_unparseable_json_rescued(self):
        self.path.write_text("{ not json", encoding="utf-8")
        result = self.store.load()
        self.assertFalse(result)
        self.assertTrue(self.store.was_reset_from_corruption)
        # Bak file with timestamp suffix.
        baks = list(self.dir.glob("schedule_state.json.bak.*"))
        self.assertEqual(len(baks), 1)
        # And a fresh file with the default content.
        self.assertTrue(self.path.exists())
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertFalse(data["schedule_enabled"])

    def test_wrong_schema_rescued(self):
        self.path.write_text(
            json.dumps({"version": 99, "schedule_enabled": True}),
            encoding="utf-8",
        )
        result = self.store.load()
        self.assertFalse(result)
        self.assertTrue(self.store.was_reset_from_corruption)

    def test_missing_field_rescued(self):
        self.path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        result = self.store.load()
        self.assertFalse(result)
        self.assertTrue(self.store.was_reset_from_corruption)

    def test_wrong_type_rescued(self):
        self.path.write_text(
            json.dumps({"version": 1, "schedule_enabled": "yes"}),
            encoding="utf-8",
        )
        result = self.store.load()
        self.assertFalse(result)
        self.assertTrue(self.store.was_reset_from_corruption)


class TestAtomicWrite(_StoreTestCase):
    def test_no_tmp_file_left_after_save(self):
        self.store.save(True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        self.assertFalse(tmp.exists())

    def test_exception_between_tmp_write_and_replace_leaves_previous_file_intact(self):
        # Establish a prior file.
        self.store.save(True)
        original = self.path.read_text(encoding="utf-8")

        # Simulate os.replace failing.
        with patch("fp_lapse.schedule.store.os.replace", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                self.store.save(False)

        # Original file untouched.
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)


class TestWasResetFlagResetOnReload(_StoreTestCase):
    def test_flag_resets_on_subsequent_clean_load(self):
        self.path.write_text("{ not json", encoding="utf-8")
        self.store.load()
        self.assertTrue(self.store.was_reset_from_corruption)
        # Next load on a clean file: flag should reset.
        self.store.load()
        self.assertFalse(self.store.was_reset_from_corruption)


if __name__ == "__main__":
    unittest.main()
