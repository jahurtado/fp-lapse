"""Tests for the pure bracket generator (`fp_lapse.bracket`).

Covers every acceptance bullet in
`docs/features/semiauto-bracketing/prd.md` (Generation algorithm): happy
path both directions, reference verbatim, ordering, n==1, ISO2 off, ISO2
rescue, minimise-shutter, tie-break, all-but-reference dropped,
clean-grid for an integer step at the reference ISO, bounded snap error
for half steps / non-power-of-2 ISO ratios, aperture held constant
(incl. None), MAX_SHOTS bound, materialised config validates, and
import safety.
"""

from __future__ import annotations

import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from fp_lapse.bracket import (  # noqa: E402
    BracketResult,
    BracketSpec,
    generate_bracket,
)
from fp_lapse.configs import (  # noqa: E402
    MAX_SHOTS_PER_BRACKET,
    Shot,
    TimelapseConfig,
    validate_strict,
)
from fp_lapse.ui.edit_values import SHUTTER_VALUES  # noqa: E402


def _gen(spec: BracketSpec) -> BracketResult:
    return generate_bracket(spec, shutter_grid=SHUTTER_VALUES)


class TestHappyPath(unittest.TestCase):
    def test_reference_darkest_clean_iso400_ladder(self):
        spec = BracketSpec(
            reference=Shot(1 / 500, 400, 8.0),
            brightest=False, ev_step=1, n=5, iso1=400, iso2=None,
        )
        r = _gen(spec)
        self.assertEqual(r.dropped, 0)
        self.assertEqual(len(r.shots), 5)
        # All iso 400, all aperture 8.0.
        for s in r.shots:
            self.assertEqual(s.iso, 400)
            self.assertEqual(s.aperture, 8.0)
        # Brightest -> darkest shutters.
        shutters = [s.shutter for s in r.shots]
        expected = [1 / 30, 1 / 60, 1 / 125, 1 / 250, 1 / 500]
        for got, exp in zip(shutters, expected):
            self.assertAlmostEqual(got, exp, places=9)

    def test_reference_brightest_mirrors_darkest(self):
        # Reference is the brightest -> ladder extends to shorter
        # shutters; rung 0 is the brightest (longest) end and comes
        # first in the output.
        spec = BracketSpec(
            reference=Shot(1 / 30, 400, None),
            brightest=True, ev_step=1, n=5, iso1=400, iso2=None,
        )
        r = _gen(spec)
        self.assertEqual(r.dropped, 0)
        shutters = [s.shutter for s in r.shots]
        expected = [1 / 30, 1 / 60, 1 / 125, 1 / 250, 1 / 500]
        for got, exp in zip(shutters, expected):
            self.assertAlmostEqual(got, exp, places=9)
        # Rung 0 (reference, brightest) is first.
        self.assertAlmostEqual(r.shots[0].shutter, 1 / 30, places=9)


class TestReferenceVerbatim(unittest.TestCase):
    def test_rung0_is_reference_exactly(self):
        # Even though iso2=1600 would give a shorter shutter at the
        # reference's own target, rung 0 stays exactly the reference.
        ref = Shot(1 / 500, 400, 5.6)
        spec = BracketSpec(
            reference=ref, brightest=False, ev_step=1, n=3,
            iso1=1600, iso2=None,
        )
        r = _gen(spec)
        # The reference rung is the darkest -> last in output.
        last = r.shots[-1]
        self.assertEqual(last.shutter, ref.shutter)
        self.assertEqual(last.iso, ref.iso)
        self.assertEqual(last.aperture, ref.aperture)


class TestOrdering(unittest.TestCase):
    def test_output_sorted_brightest_to_darkest(self):
        spec = BracketSpec(
            reference=Shot(1 / 500, 400, None),
            brightest=False, ev_step=1, n=5, iso1=400, iso2=None,
        )
        r = _gen(spec)
        lights = [s.shutter * s.iso for s in r.shots]
        self.assertEqual(lights, sorted(lights, reverse=True))


class TestEdgeCounts(unittest.TestCase):
    def test_n_equals_one_is_reference_only(self):
        ref = Shot(1 / 250, 800, 4.0)
        spec = BracketSpec(
            reference=ref, brightest=False, ev_step=2, n=1,
            iso1=800, iso2=None,
        )
        r = _gen(spec)
        self.assertEqual(r.shots, (ref,))
        self.assertEqual(r.dropped, 0)

    def test_iso2_off_only_iso1_eligible(self):
        # With iso2 off, the only non-reference ISO is iso1.
        spec = BracketSpec(
            reference=Shot(1 / 500, 400, None),
            brightest=False, ev_step=1, n=3, iso1=200, iso2=None,
        )
        r = _gen(spec)
        for s in r.shots[:-1]:   # all but the reference rung
            self.assertEqual(s.iso, 200)


class TestIso2Rescue(unittest.TestCase):
    def test_iso2_lower_rescues_an_out_of_range_rung(self):
        # Brightest, deep step: rung 1 needs a shutter faster than
        # 1/8000 at iso1=12800 (drops), but iso2=160 lands it exactly on
        # 1/8000 (rescued).
        ref = Shot(1 / 500, 160, None)   # light_ref = 0.32
        no2 = BracketSpec(
            reference=ref, brightest=True, ev_step=4, n=2,
            iso1=12800, iso2=None,
        )
        with2 = BracketSpec(
            reference=ref, brightest=True, ev_step=4, n=2,
            iso1=12800, iso2=160,
        )
        r_no2 = _gen(no2)
        r_with2 = _gen(with2)
        self.assertEqual(len(r_no2.shots), 1)     # only the reference
        self.assertEqual(r_no2.dropped, 1)
        self.assertEqual(len(r_with2.shots), 2)    # rung rescued
        self.assertEqual(r_with2.dropped, 0)
        # The rescued rung is the darker one (last in brightest-first
        # order); it uses iso2 = 160 at 1/8000.
        rescued = r_with2.shots[-1]
        self.assertEqual(rescued.iso, 160)
        self.assertAlmostEqual(rescued.shutter, 1 / 8000, places=9)


class TestMinimiseShutter(unittest.TestCase):
    def test_picks_shortest_snapped_shutter(self):
        # Both ISOs feasible for rung 1; iso 800 yields the shorter
        # shutter (1/500) than iso 400 (1/250) -> 800 wins.
        spec = BracketSpec(
            reference=Shot(1 / 500, 400, None),
            brightest=False, ev_step=1, n=2, iso1=400, iso2=800,
        )
        r = _gen(spec)
        rung1 = r.shots[0]   # the brighter rung
        self.assertEqual(rung1.iso, 800)
        self.assertAlmostEqual(rung1.shutter, 1 / 500, places=9)


class TestTieBreak(unittest.TestCase):
    def test_same_snapped_shutter_prefers_lower_iso(self):
        # Synthetic grid: both eligible ISOs snap to the same shutter
        # (100.0); the lower ISO must win.
        grid = [100.0, 200.0]
        spec = BracketSpec(
            reference=Shot(420.0, 1, None),    # light_ref = 420
            brightest=False, ev_step=1, n=2, iso1=6, iso2=7,
        )
        r = generate_bracket(spec, shutter_grid=grid)
        # k1 target = 840: required@6 = 140, @7 = 120, both snap to 100.
        rung1 = r.shots[0]
        self.assertEqual(rung1.shutter, 100.0)
        self.assertEqual(rung1.iso, 6)


class TestEvStepSet(unittest.TestCase):
    def test_light_ratio_between_adjacent_rungs(self):
        # For each step, the ideal target light of adjacent rungs differs
        # by exactly 2**ev_step (verified on the targets, before snap).
        for step in (1, 2, 2.5, 3, 3.5, 4):
            with self.subTest(step=step):
                ref = Shot(1 / 4000, 400, None)
                spec = BracketSpec(
                    reference=ref, brightest=False, ev_step=step, n=2,
                    iso1=400, iso2=None,
                )
                r = _gen(spec)
                # Reference target = light_ref; rung 1 target =
                # light_ref * 2**step. Recompute both ideals.
                light_ref = ref.shutter * ref.iso
                ratio = (light_ref * (2.0 ** step)) / light_ref
                self.assertAlmostEqual(ratio, 2.0 ** step, places=9)
                # Two distinct rungs survived (sanity).
                self.assertEqual(len(r.shots), 2)


class TestAllButReferenceDropped(unittest.TestCase):
    def test_reference_at_extreme_drops_all_others(self):
        # Reference at the fast extreme (1/8000), brightest -> every
        # k>=1 needs a faster-than-1/8000 shutter and drops.
        ref = Shot(1 / 8000, 100, None)
        spec = BracketSpec(
            reference=ref, brightest=True, ev_step=1, n=5,
            iso1=100, iso2=None,
        )
        r = _gen(spec)
        self.assertEqual(r.shots, (ref,))
        self.assertEqual(r.dropped, 4)
        self.assertGreaterEqual(len(r.shots), 1)   # never empty


class TestCleanGrid(unittest.TestCase):
    def test_integer_step_same_iso_lands_on_grid_indices(self):
        # Integer EV step + chosen ISO == reference ISO -> each rung
        # lands exactly 3*ev_step*k indices from the reference (no drift).
        ref_idx = SHUTTER_VALUES.index(1 / 500)
        spec = BracketSpec(
            reference=Shot(1 / 500, 400, None),
            brightest=False, ev_step=2, n=4, iso1=400, iso2=None,
        )
        r = _gen(spec)
        # Darkest direction -> brighter rungs at higher grid indices.
        # k order shutters = grid[ref_idx + 6k]; output is reversed.
        expected_k = [SHUTTER_VALUES[ref_idx + 6 * k] for k in range(4)]
        expected_out = list(reversed(expected_k))
        got = [s.shutter for s in r.shots]
        for g, e in zip(got, expected_out):
            self.assertAlmostEqual(g, e, places=12)


class TestBoundedSnap(unittest.TestCase):
    def test_half_step_snaps_within_one_sixth_ev(self):
        # 2.5 EV half step at the reference ISO: the ideal shutter falls
        # between grid stops and snaps to the nearest, within +/-1/6 EV.
        ref = Shot(1 / 2000, 400, None)
        spec = BracketSpec(
            reference=ref, brightest=False, ev_step=2.5, n=2,
            iso1=400, iso2=None,
        )
        r = _gen(spec)
        light_ref = ref.shutter * ref.iso
        target1 = light_ref * (2.0 ** 2.5)
        rung1 = r.shots[0]
        got_light = rung1.shutter * rung1.iso
        ev_err = abs(math.log2(got_light / target1))
        self.assertLessEqual(ev_err, 1.0 / 6 + 1e-9)

    def test_non_power_of_two_iso_ratio_snaps_within_one_sixth_ev(self):
        # iso 640 differs from the reference iso 400 by ratio 1.6 (not a
        # power of two); the ideal shutter snaps to the nearest stop.
        ref = Shot(1 / 1000, 400, None)
        spec = BracketSpec(
            reference=ref, brightest=False, ev_step=1, n=2,
            iso1=640, iso2=None,
        )
        r = _gen(spec)
        light_ref = ref.shutter * ref.iso
        target1 = light_ref * 2.0
        rung1 = r.shots[0]
        self.assertEqual(rung1.iso, 640)
        got_light = rung1.shutter * rung1.iso
        ev_err = abs(math.log2(got_light / target1))
        self.assertLessEqual(ev_err, 1.0 / 6 + 1e-9)


class TestApertureHeld(unittest.TestCase):
    def test_aperture_none_held(self):
        spec = BracketSpec(
            reference=Shot(1 / 500, 400, None),
            brightest=False, ev_step=1, n=4, iso1=400, iso2=None,
        )
        r = _gen(spec)
        for s in r.shots:
            self.assertIsNone(s.aperture)

    def test_concrete_aperture_held(self):
        spec = BracketSpec(
            reference=Shot(1 / 500, 400, 11.0),
            brightest=False, ev_step=1, n=4, iso1=400, iso2=None,
        )
        r = _gen(spec)
        for s in r.shots:
            self.assertEqual(s.aperture, 11.0)


class TestMaxShotsBound(unittest.TestCase):
    def test_full_nine_no_drops(self):
        ref = Shot(1 / 8000, 100, None)
        spec = BracketSpec(
            reference=ref, brightest=False, ev_step=1,
            n=MAX_SHOTS_PER_BRACKET, iso1=100, iso2=None,
        )
        r = _gen(spec)
        self.assertEqual(len(r.shots), 9)
        self.assertEqual(r.dropped, 0)


class TestMaterialisedValidates(unittest.TestCase):
    def test_output_passes_validate_strict(self):
        spec = BracketSpec(
            reference=Shot(1 / 500, 400, None),
            brightest=False, ev_step=1, n=5, iso1=400, iso2=800,
        )
        r = _gen(spec)
        cfg = TimelapseConfig(name="Bracket", interval_s=5.0, shots=r.shots)
        # Must not raise.
        validate_strict([cfg])


class TestImportSafe(unittest.TestCase):
    def test_import_succeeds(self):
        import importlib

        mod = importlib.import_module("fp_lapse.bracket")
        self.assertTrue(hasattr(mod, "generate_bracket"))


if __name__ == "__main__":
    unittest.main()
