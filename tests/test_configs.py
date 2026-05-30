"""Tests for the configs data model + `ConfigStore`. Stdlib unittest."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, time, timedelta, datetime as dt
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.configs import (  # noqa: E402
    ISO_MAX,
    MAX_CONFIGS,
    MAX_NAME_LENGTH,
    MAX_SHOTS_PER_BRACKET,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    ConfigSchemaError,
    ConfigStore,
    ConfigValidationError,
    Shot,
    TimelapseConfig,
    validate_strict,
)
from fp_lapse.schedule.moment import ScheduledMoment  # noqa: E402


PARTIAL = TimelapseConfig(
    name="Partial",
    interval_s=10.0,
    shots=(Shot(shutter=1 / 1000, iso=200, aperture=None),),
)

TOTALITY = TimelapseConfig(
    name="Totality",
    interval_s=5.0,
    shots=(
        Shot(shutter=1 / 500, iso=400, aperture=None),
        Shot(shutter=1 / 125, iso=400, aperture=None),
        Shot(shutter=1 / 30, iso=400, aperture=None),
        Shot(shutter=1 / 8, iso=400, aperture=None),
        Shot(shutter=2.0, iso=1600, aperture=None),
    ),
)

# Auto-mode config — camera meters everything, 1 shot per interval.
AUTO_DAYTIME = TimelapseConfig(name="Auto day", interval_s=30.0, shots=())


class TestShotFormatters(unittest.TestCase):
    def test_format_iso(self):
        self.assertEqual(Shot(shutter=1/500, iso=200).format_iso(), "ISO 200")
        self.assertEqual(Shot(shutter=1/500, iso=1600).format_iso(), "ISO 1600")

    def test_format_aperture(self):
        s = Shot(shutter=1/500, iso=200, aperture=5.6)
        self.assertEqual(s.format_aperture(), "f/5.6")
        self.assertEqual(
            Shot(shutter=1/500, iso=200, aperture=8.0).format_aperture(), "f/8"
        )
        self.assertEqual(
            Shot(shutter=1/500, iso=200, aperture=1.4).format_aperture(), "f/1.4"
        )
        self.assertEqual(
            Shot(shutter=1/500, iso=200, aperture=None).format_aperture(), "f/—"
        )

    def test_format_shutter_uses_module_rules(self):
        self.assertEqual(Shot(shutter=0.002, iso=100).format_shutter(), "1/500")
        self.assertEqual(Shot(shutter=1.0, iso=100).format_shutter(), "1 s")


class _StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "configs.json"
        self.store = ConfigStore(self.path)

    def tearDown(self):
        self._tmp.cleanup()


class TestConfigStoreRoundtrip(_StoreTestCase):
    def test_load_missing_creates_empty_file(self):
        configs = self.store.load()
        self.assertEqual(configs, [])
        self.assertFalse(self.store.was_reset_from_corruption)
        self.assertTrue(self.path.exists())
        on_disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, {"version": SCHEMA_VERSION, "configs": []})

    def test_save_then_load(self):
        self.store.save([PARTIAL, TOTALITY, AUTO_DAYTIME])
        loaded = self.store.load()
        self.assertEqual(len(loaded), 3)
        self.assertEqual(loaded[0].name, "Partial")
        self.assertEqual(loaded[1].name, "Totality")
        self.assertEqual(loaded[1].shots[4].iso, 1600)
        self.assertAlmostEqual(loaded[1].shots[0].shutter, 1 / 500)
        self.assertEqual(loaded[2].name, "Auto day")
        self.assertEqual(loaded[2].shots, ())
        self.assertTrue(loaded[2].is_auto)

    def test_no_tmp_left_after_save(self):
        self.store.save([PARTIAL])
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        self.assertFalse(tmp.exists())

    def test_backup_rotated_on_overwrite(self):
        self.store.save([PARTIAL])
        self.store.save([TOTALITY])
        bak = self.path.with_suffix(self.path.suffix + ".bak")
        self.assertTrue(bak.exists())
        bak_data = json.loads(bak.read_text(encoding="utf-8"))
        self.assertEqual(bak_data["configs"][0]["name"], "Partial")


class TestConfigStoreCorruption(_StoreTestCase):
    def test_unparseable_json_is_rescued(self):
        self.path.write_text("{ not json", encoding="utf-8")
        loaded = self.store.load()
        self.assertEqual(loaded, [])
        self.assertTrue(self.store.was_reset_from_corruption)
        self.assertFalse(self.path.exists())
        rescues = list(self.dir.glob("configs.json.bak.*"))
        self.assertEqual(len(rescues), 1)

    def test_wrong_version_is_rescued(self):
        # 99 is not in SUPPORTED_SCHEMA_VERSIONS — must be rescued.
        # (1 and 2 are both supported; see TestConfigStoreV1Load.)
        self.path.write_text(
            json.dumps({"version": 99, "configs": []}), encoding="utf-8"
        )
        loaded = self.store.load()
        self.assertEqual(loaded, [])
        self.assertTrue(self.store.was_reset_from_corruption)

    def test_missing_iso_is_rescued(self):
        # `iso` is now a required numeric field — a shot dict without
        # it is a schema error, so the file is rescued (the user sees
        # `CONFIGS RESET` on next boot).
        self.path.write_text(
            json.dumps(
                {
                    "version": SCHEMA_VERSION,
                    "configs": [
                        {
                            "name": "X",
                            "interval_s": 5,
                            "shots": [{"shutter": "1/500"}],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        loaded = self.store.load()
        self.assertEqual(loaded, [])
        self.assertTrue(self.store.was_reset_from_corruption)

    def test_empty_shots_is_auto_mode(self):
        # Empty `shots` means "auto mode": the engine fires 1 shot per
        # interval with the camera metering. Legal, not corruption.
        self.path.write_text(
            json.dumps(
                {
                    "version": SCHEMA_VERSION,
                    "configs": [{"name": "X", "interval_s": 5, "shots": []}],
                }
            ),
            encoding="utf-8",
        )
        loaded = self.store.load()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].shots, ())
        self.assertTrue(loaded[0].is_auto)
        self.assertFalse(self.store.was_reset_from_corruption)


class TestLoaderLimits(_StoreTestCase):
    """§7.7: el cargador trunca cantidades excesivas con WARNING (sin rescate)."""

    def _write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_too_many_configs_truncated(self):
        too_many = [
            {
                "name": f"C{i}",
                "interval_s": 10,
                "shots": [{"shutter": "1/500", "iso": 200, "aperture": None}],
            }
            for i in range(MAX_CONFIGS + 5)
        ]
        self._write({"version": SCHEMA_VERSION, "configs": too_many})
        with self.assertLogs("fp_lapse.configs", level="WARNING"):
            loaded = self.store.load()
        self.assertEqual(len(loaded), MAX_CONFIGS)
        self.assertFalse(self.store.was_reset_from_corruption)

    def test_too_many_shots_truncated(self):
        many_shots = [
            {"shutter": "1/500", "iso": 200, "aperture": None}
            for _ in range(MAX_SHOTS_PER_BRACKET + 3)
        ]
        self._write(
            {
                "version": SCHEMA_VERSION,
                "configs": [{"name": "X", "interval_s": 5, "shots": many_shots}],
            }
        )
        with self.assertLogs("fp_lapse.configs", level="WARNING"):
            loaded = self.store.load()
        self.assertEqual(len(loaded[0].shots), MAX_SHOTS_PER_BRACKET)

    def test_long_name_truncated(self):
        long = "X" * (MAX_NAME_LENGTH + 5)
        self._write(
            {
                "version": SCHEMA_VERSION,
                "configs": [
                    {
                        "name": long,
                        "interval_s": 5,
                        "shots": [
                            {"shutter": "1/500", "iso": 200, "aperture": None}
                        ],
                    }
                ],
            }
        )
        with self.assertLogs("fp_lapse.configs", level="WARNING"):
            loaded = self.store.load()
        self.assertEqual(len(loaded[0].name), MAX_NAME_LENGTH)


def _shot(shutter=1 / 500, iso=200, aperture=None):
    return Shot(shutter=shutter, iso=iso, aperture=aperture)


class TestStrictValidation(unittest.TestCase):
    def test_empty_list_ok(self):
        validate_strict([])

    def test_too_many_configs(self):
        many = [
            TimelapseConfig(
                name=f"c{i}", interval_s=10.0, shots=(_shot(),)
            )
            for i in range(MAX_CONFIGS + 1)
        ]
        with self.assertRaises(ConfigValidationError):
            validate_strict(many)

    def test_duplicate_name(self):
        a = TimelapseConfig(name="dup", interval_s=10.0, shots=(_shot(),))
        b = TimelapseConfig(name="dup", interval_s=5.0, shots=(_shot(),))
        with self.assertRaises(ConfigValidationError):
            validate_strict([a, b])

    def test_empty_name(self):
        cfg = TimelapseConfig(name="", interval_s=10.0, shots=(_shot(),))
        with self.assertRaises(ConfigValidationError):
            validate_strict([cfg])

    def test_name_too_long(self):
        cfg = TimelapseConfig(
            name="X" * (MAX_NAME_LENGTH + 1),
            interval_s=10.0,
            shots=(_shot(),),
        )
        with self.assertRaises(ConfigValidationError):
            validate_strict([cfg])

    def test_too_many_shots(self):
        shots = tuple(_shot() for _ in range(MAX_SHOTS_PER_BRACKET + 1))
        cfg = TimelapseConfig(name="c", interval_s=10.0, shots=shots)
        with self.assertRaises(ConfigValidationError):
            validate_strict([cfg])

    def test_negative_interval(self):
        cfg = TimelapseConfig(name="c", interval_s=-1.0, shots=(_shot(),))
        with self.assertRaises(ConfigValidationError):
            validate_strict([cfg])

    def test_shutter_out_of_range(self):
        cfg = TimelapseConfig(
            name="c", interval_s=10.0, shots=(_shot(shutter=1 / 16000),)
        )
        with self.assertRaises(ConfigValidationError):
            validate_strict([cfg])

    def test_iso_out_of_range(self):
        cfg = TimelapseConfig(
            name="c", interval_s=10.0, shots=(_shot(iso=ISO_MAX * 2),)
        )
        with self.assertRaises(ConfigValidationError):
            validate_strict([cfg])

    def test_auto_mode_empty_shots_is_valid(self):
        # Auto mode: empty shots tuple is the legal sentinel.
        cfg = TimelapseConfig(name="auto", interval_s=10.0, shots=())
        validate_strict([cfg])


class TestConfigStoreRejectsInvalidOnSave(_StoreTestCase):
    def test_save_runs_strict_validation(self):
        bad = TimelapseConfig(
            name="", interval_s=10.0, shots=(_shot(),)
        )
        with self.assertRaises(ConfigValidationError):
            self.store.save([bad])
        # nothing leaked to disk
        self.assertFalse(self.path.exists())


# =========================================================================
# Schedule extension (PRD scheduled-configs §1)
# =========================================================================


class TestSchemaVersionConstants(unittest.TestCase):
    def test_schema_version_is_2(self):
        self.assertEqual(SCHEMA_VERSION, 2)

    def test_supported_versions_includes_1_and_2(self):
        self.assertEqual(SUPPORTED_SCHEMA_VERSIONS, (1, 2))


class TestTimelapseConfigScheduleFields(unittest.TestCase):
    def test_defaults_are_none(self):
        c = TimelapseConfig(name="X", interval_s=5.0, shots=(_shot(),))
        self.assertIsNone(c.start)
        self.assertIsNone(c.end)

    def test_with_schedule_moments(self):
        c = TimelapseConfig(
            name="Eclipse",
            interval_s=5.0,
            shots=(_shot(),),
            start=ScheduledMoment(time=time(11, 33, 23), date=date(2026, 8, 12)),
            end=ScheduledMoment(time=time(11, 36, 9), date=date(2026, 8, 12)),
        )
        self.assertEqual(c.start.date, date(2026, 8, 12))
        self.assertEqual(c.end.time, time(11, 36, 9))

    def test_backward_compatible_positional_construction(self):
        # Old call sites that don't pass start/end must keep working.
        c = TimelapseConfig("X", 5.0, (_shot(),))
        self.assertIsNone(c.start)


class TestConfigStoreV1Load(_StoreTestCase):
    """v1 files (no start/end) must load with start = end = None."""

    def test_v1_file_loads_with_none_schedule(self):
        self.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "configs": [
                        {
                            "name": "Old",
                            "interval_s": 10,
                            "shots": [
                                {"shutter": "1/500", "iso": 200, "aperture": None}
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        loaded = self.store.load()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].name, "Old")
        self.assertIsNone(loaded[0].start)
        self.assertIsNone(loaded[0].end)
        self.assertFalse(self.store.was_reset_from_corruption)

    def test_v1_file_save_rewrites_as_v2(self):
        self.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "configs": [
                        {
                            "name": "Old",
                            "interval_s": 10,
                            "shots": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        loaded = self.store.load()
        self.store.save(loaded)
        on_disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["version"], 2)


class TestConfigStoreV2Load(_StoreTestCase):
    def test_v2_one_shot_and_daily_and_no_schedule(self):
        self.path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "configs": [
                        {
                            "name": "Totality",
                            "interval_s": 5,
                            "shots": [
                                {"shutter": "1/500", "iso": 400, "aperture": None}
                            ],
                            "start": {"date": "2026-08-12", "time": "11:33:23"},
                            "end":   {"date": "2026-08-12", "time": "11:36:09"},
                        },
                        {
                            "name": "Sunrise",
                            "interval_s": 30,
                            "shots": [],
                            "start": {"date": None, "time": "07:00:00"},
                            "end":   {"date": None, "time": "19:00:00"},
                        },
                        {
                            "name": "Ad-hoc",
                            "interval_s": 10,
                            "shots": [
                                {"shutter": "1/500", "iso": 200, "aperture": None}
                            ],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        loaded = self.store.load()
        self.assertEqual(len(loaded), 3)
        # One-shot.
        self.assertEqual(loaded[0].start.date, date(2026, 8, 12))
        self.assertEqual(loaded[0].start.time, time(11, 33, 23))
        self.assertEqual(loaded[0].end.time, time(11, 36, 9))
        # Daily.
        self.assertIsNone(loaded[1].start.date)
        self.assertEqual(loaded[1].start.time, time(7, 0, 0))
        # No schedule.
        self.assertIsNone(loaded[2].start)
        self.assertIsNone(loaded[2].end)

    def test_v2_save_roundtrip(self):
        configs = [
            TimelapseConfig(
                name="Totality",
                interval_s=5.0,
                shots=(_shot(),),
                start=ScheduledMoment(time=time(11, 33, 23), date=date(2026, 8, 12)),
                end=ScheduledMoment(time=time(11, 36, 9), date=date(2026, 8, 12)),
            ),
            TimelapseConfig(
                name="Sunrise",
                interval_s=30.0,
                shots=(),
                start=ScheduledMoment(time=time(7, 0, 0)),
                end=ScheduledMoment(time=time(19, 0, 0)),
            ),
        ]
        self.store.save(configs)
        on_disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["version"], 2)
        c0 = on_disk["configs"][0]
        self.assertEqual(c0["start"], {"date": "2026-08-12", "time": "11:33:23"})
        self.assertEqual(c0["end"], {"date": "2026-08-12", "time": "11:36:09"})
        c1 = on_disk["configs"][1]
        self.assertEqual(c1["start"], {"date": None, "time": "07:00:00"})
        # Now reload — moments come back identical.
        reloaded = self.store.load()
        self.assertEqual(reloaded[0].start, configs[0].start)
        self.assertEqual(reloaded[0].end, configs[0].end)
        self.assertEqual(reloaded[1].start, configs[1].start)

    def test_v2_null_start_end_deserialize_to_none(self):
        self.path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "configs": [
                        {
                            "name": "X",
                            "interval_s": 5,
                            "shots": [
                                {"shutter": "1/500", "iso": 200, "aperture": None}
                            ],
                            "start": None,
                            "end": None,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        loaded = self.store.load()
        self.assertIsNone(loaded[0].start)
        self.assertIsNone(loaded[0].end)


class TestConfigStoreV2SchemaViolations(_StoreTestCase):
    def test_date_without_time_rescued(self):
        self.path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "configs": [
                        {
                            "name": "X",
                            "interval_s": 5,
                            "shots": [],
                            "start": {"date": "2026-08-12"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        loaded = self.store.load()
        self.assertEqual(loaded, [])
        self.assertTrue(self.store.was_reset_from_corruption)

    def test_time_null_rescued(self):
        self.path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "configs": [
                        {
                            "name": "X",
                            "interval_s": 5,
                            "shots": [],
                            "start": {"date": None, "time": None},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        loaded = self.store.load()
        self.assertEqual(loaded, [])
        self.assertTrue(self.store.was_reset_from_corruption)

    def test_bogus_time_rescued(self):
        self.path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "configs": [
                        {
                            "name": "X",
                            "interval_s": 5,
                            "shots": [],
                            "start": {"date": None, "time": "bogus"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        loaded = self.store.load()
        self.assertEqual(loaded, [])
        self.assertTrue(self.store.was_reset_from_corruption)

    def test_bogus_date_rescued(self):
        self.path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "configs": [
                        {
                            "name": "X",
                            "interval_s": 5,
                            "shots": [],
                            "start": {"date": "not-a-date", "time": "09:00:00"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        loaded = self.store.load()
        self.assertEqual(loaded, [])
        self.assertTrue(self.store.was_reset_from_corruption)


class TestStrictValidationPastDate(unittest.TestCase):
    def _past(self):
        return date.today() - timedelta(days=365)

    def test_past_start_date_warns_but_does_not_raise(self):
        cfg = TimelapseConfig(
            name="Old",
            interval_s=10.0,
            shots=(_shot(),),
            start=ScheduledMoment(time=time(9, 0, 0), date=self._past()),
        )
        with self.assertLogs("fp_lapse.configs", level="WARNING") as cm:
            validate_strict([cfg])  # MUST NOT raise.
        self.assertTrue(any("past" in msg.lower() for msg in cm.output))

    def test_past_end_date_warns_but_does_not_raise(self):
        cfg = TimelapseConfig(
            name="Old",
            interval_s=10.0,
            shots=(_shot(),),
            end=ScheduledMoment(time=time(9, 0, 0), date=self._past()),
        )
        with self.assertLogs("fp_lapse.configs", level="WARNING") as cm:
            validate_strict([cfg])
        self.assertTrue(any("past" in msg.lower() for msg in cm.output))

    def test_future_date_does_not_warn(self):
        cfg = TimelapseConfig(
            name="Future",
            interval_s=10.0,
            shots=(_shot(),),
            start=ScheduledMoment(
                time=time(9, 0, 0),
                date=date.today() + timedelta(days=365),
            ),
        )
        # No warnings on the configs logger expected.
        import logging
        with self.assertLogs("fp_lapse.configs", level="WARNING") as cm:
            logging.getLogger("fp_lapse.configs").warning("sentinel")
            validate_strict([cfg])
        # Only the sentinel was logged — no "past" warning emitted.
        past_msgs = [msg for msg in cm.output if "past" in msg.lower()]
        self.assertEqual(past_msgs, [])

    def test_daily_moment_no_date_no_warning(self):
        # Daily-recurring (date=None) moments have no past-date concept.
        cfg = TimelapseConfig(
            name="Daily",
            interval_s=10.0,
            shots=(_shot(),),
            start=ScheduledMoment(time=time(9, 0, 0)),
        )
        import logging
        with self.assertLogs("fp_lapse.configs", level="WARNING") as cm:
            logging.getLogger("fp_lapse.configs").warning("sentinel")
            validate_strict([cfg])
        past_msgs = [msg for msg in cm.output if "past" in msg.lower()]
        self.assertEqual(past_msgs, [])


if __name__ == "__main__":
    unittest.main()
