# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
Tests for suspension.aero.plug_builder — the plug & layup build planner.

The claims under test are the ones a build Saturday depends on:
  * the slice plan reaches the crest (never a short stack) and reports the trim;
  * stack-up tolerance is real RSS/worst-case arithmetic and feeds ToleranceBudget;
  * templates are honest 1:1 SVGs with the 100 mm verification bar;
  * BOM quantities scale the right way with plies/area and carry their basis;
  * the scheduler respects dependencies, crews, and cures — and says NO
    honestly when the single-day plan doesn't fit;
  * the two hard gates (release-before-layup, don't-sand-into-carbon) trip.
"""

import math
import unittest

from suspension.aero.plug_builder import (
    NoseconeBody, FoamSheet, SlicePlan, StackTolerance, layer_template_svg,
    LayupRecipe, MaterialsEstimate, BuildStep, default_build_day,
    BuildDaySchedule, PreflightGate, PlugBuildPlan,
)
from suspension.aero.scale_model import ScaleSpec, ToleranceBudget


def _meeting_body():
    """The Aero Meeting #4 article: a 1:2.5 nosecone, scaled dims in mm."""
    return NoseconeBody(length_mm=520, base_width_mm=250, base_height_mm=260)


def _sheet():
    return FoamSheet(thickness_mm=25.4, length_mm=1220, width_mm=610,
                     thickness_tol_mm=0.5)


class TestNoseconeBody(unittest.TestCase):
    def test_validation(self):
        with self.assertRaises(ValueError):
            NoseconeBody(length_mm=0, base_width_mm=250, base_height_mm=260)
        with self.assertRaises(ValueError):
            NoseconeBody(length_mm=500, base_width_mm=250, base_height_mm=260,
                         section_exponent=1.0)

    def test_loft_monotone_and_bounded(self):
        b = _meeting_body()
        self.assertAlmostEqual(b.plan_half_width(b.length_mm), 125.0)
        self.assertAlmostEqual(b.crest_height(b.length_mm), 260.0)
        self.assertEqual(b.plan_half_width(0.0), 0.0)
        # half-width shrinks with height and vanishes at the crest
        w0 = b.half_width_at(400, 0.0)
        w1 = b.half_width_at(400, 100.0)
        self.assertGreater(w0, w1)
        self.assertEqual(b.half_width_at(400, b.crest_height(400) + 1), 0.0)

    def test_slice_outline_shrinks_with_height(self):
        b = _meeting_body()
        low = b.slice_outline(0.0)
        high = b.slice_outline(200.0)
        self.assertTrue(low and high)
        span = lambda poly: max(p[0] for p in poly) - min(p[0] for p in poly)
        self.assertGreater(span(low), span(high))
        # at/above the crest there is nothing to cut
        self.assertEqual(b.slice_outline(260.0), [])

    def test_area_and_volume_sane(self):
        b = _meeting_body()
        area = b.shell_area_m2()
        # bounded below by the planform floor and above by the bbox surface
        floor = sum(2 * b.plan_half_width((i + 0.5) * b.length_mm / 200)
                    * (b.length_mm / 200) for i in range(200)) / 1e6
        self.assertGreater(area, floor)
        bbox = 2 * (0.52 * 0.25 + 0.52 * 0.26 + 0.25 * 0.26)
        self.assertLess(area, bbox)
        vol = b.volume_l()
        self.assertGreater(vol, 0.0)
        self.assertLess(vol, 0.52 * 0.25 * 0.26 * 1000)   # < bounding box litres

    def test_from_sections_override(self):
        # a constant 100×100 square prism via CAD-section callback
        def outline(z):
            return [] if z >= 100 else [(0, 50), (100, 50), (100, -50), (0, -50)]
        b = NoseconeBody.from_sections(100, 100, 100, outline)
        poly = b.slice_outline(10.0)
        self.assertEqual(len(poly), 4)
        self.assertEqual(b.slice_outline(150.0), [])


class TestSlicePlan(unittest.TestCase):
    def test_stack_reaches_crest_and_reports_trim(self):
        plan = SlicePlan.plan(_meeting_body(), _sheet(), bondline_mm=0.3)
        n = plan.n_layers()
        stack = n * 25.4 + (n - 1) * 0.3
        self.assertGreaterEqual(stack + 1e-9, 260.0)          # never short
        self.assertAlmostEqual(plan.top_trim_mm, stack - 260.0, places=6)
        # one fewer layer would be short
        self.assertLess((n - 1) * 25.4 + (n - 2) * 0.3, 260.0)

    def test_layers_ordered_and_shrinking(self):
        plan = SlicePlan.plan(_meeting_body(), _sheet())
        idxs = [l.index for l in plan.layers]
        self.assertEqual(idxs, list(range(1, len(idxs) + 1)))
        lengths = [l.length_mm for l in plan.layers]
        self.assertEqual(lengths, sorted(lengths, reverse=True))
        for l in plan.layers:
            self.assertGreater(len(l.cut_outline), 4)

    def test_stack_tolerance_math(self):
        plan = SlicePlan.plan(_meeting_body(), _sheet(),
                              bondline_mm=0.3, bondline_tol_mm=0.2)
        t = plan.tolerance
        n = plan.n_layers()
        self.assertAlmostEqual(
            t.height_rss_mm, math.sqrt(n * 0.5 ** 2 + (n - 1) * 0.2 ** 2), places=9)
        self.assertAlmostEqual(t.height_worst_mm, n * 0.5 + (n - 1) * 0.2, places=9)
        self.assertGreater(t.height_worst_mm, t.height_rss_mm)

    def test_tolerance_feeds_budget(self):
        plan = SlicePlan.plan(_meeting_body(), _sheet())
        spec = ScaleSpec(ratio=0.4, scaled_chord_mm=520,
                         scaled_height_mm=260, scaled_width_mm=250)
        budget = plan.tolerance.feed(ToleranceBudget(spec), 260.0)
        rep = budget.report()
        self.assertGreater(rep.cl_uncertainty_frac, 0.0)
        self.assertIn("foam-stack height", rep.summary)
        self.assertIn("ESTIMATE", rep.summary)

    def test_sheets_needed_and_oversize_guard(self):
        plan = SlicePlan.plan(_meeting_body(), _sheet())
        n = plan.sheets_needed()
        self.assertGreaterEqual(n, 1)
        self.assertEqual(
            n, math.ceil(plan.nesting_area_m2() / (_sheet().area_m2() * 0.75)))
        tiny = FoamSheet(thickness_mm=25.4, length_mm=300, width_mm=200)
        with self.assertRaises(ValueError):
            SlicePlan.plan(_meeting_body(), tiny).sheets_needed()

    def test_negative_inputs_rejected(self):
        with self.assertRaises(ValueError):
            SlicePlan.plan(_meeting_body(), _sheet(), bondline_mm=-0.1)


class TestTemplates(unittest.TestCase):
    def test_svg_is_true_scale_with_verification_bar(self):
        plan = SlicePlan.plan(_meeting_body(), _sheet())
        svg = layer_template_svg(plan.layers[0])
        self.assertIn("<svg", svg)
        self.assertIn("mm\"", svg)                      # physical mm units
        self.assertIn("must measure 100 mm", svg)       # the printer contract
        self.assertIn("stroke-dasharray", svg)          # sand-to line present
        self.assertIn("LAYER 1", svg)
        # page width covers the layer plus margins, in mm
        l = plan.layers[0]
        self.assertIn(f'width="{l.length_mm + 30:.1f}mm"', svg)

    def test_empty_layer_rejected(self):
        plan = SlicePlan.plan(_meeting_body(), _sheet())
        bad = plan.layers[0].__class__(index=99, z_bottom_mm=0, z_top_mm=1,
                                       cut_outline=(), sand_to_outline=(),
                                       length_mm=0, width_mm=0)
        with self.assertRaises(ValueError):
            layer_template_svg(bad)


class TestMaterials(unittest.TestCase):
    def _bom(self, recipe=None):
        body, sheet = _meeting_body(), _sheet()
        plan = SlicePlan.plan(body, sheet)
        return body, plan, MaterialsEstimate.compute(
            body, plan, recipe or LayupRecipe(), sheet, crew_size=6)

    def test_quantities_scale_with_plies(self):
        _, _, bom1 = self._bom(LayupRecipe(plies=1))
        _, _, bom2 = self._bom(LayupRecipe(plies=2))
        fab1 = next(l for l in bom1.lines if "carbon fabric" in l.item).quantity
        fab2 = next(l for l in bom2.lines if "carbon fabric" in l.item).quantity
        self.assertAlmostEqual(fab2 / fab1, 2.0, places=6)
        res1 = next(l for l in bom1.lines if "laminating" in l.item).quantity
        res2 = next(l for l in bom2.lines if "laminating" in l.item).quantity
        self.assertAlmostEqual(res2 / res1, 2.0, places=6)

    def test_resin_arithmetic(self):
        body, _, bom = self._bom()
        r = LayupRecipe()
        fab = body.shell_area_m2() * r.plies * 1.25
        expect = fab * 200.0 * 1.0 * 1.15
        got = next(l for l in bom.lines if "laminating" in l.item).quantity
        self.assertAlmostEqual(got, expect, places=6)

    def test_ppe_scales_with_crew_and_basis_present(self):
        _, _, bom = self._bom()
        resp = next(l for l in bom.lines if "respirator" in l.item)
        self.assertEqual(resp.quantity, 6)
        for l in bom.lines:
            self.assertTrue(l.basis)                    # no basis-less numbers

    def test_thin_coating_warns(self):
        _, _, bom = self._bom(LayupRecipe(coating_thickness_mm=0.35,
                                          flatting_removal_mm=0.3))
        self.assertTrue(any("structural carbon" in w for w in bom.warnings))

    def test_csv_roundtrip(self):
        _, _, bom = self._bom()
        csv_text = bom.to_csv()
        self.assertIn("Item,Quantity,Unit", csv_text)
        self.assertEqual(len(csv_text.strip().splitlines()), len(bom.lines) + 1)

    def test_recipe_validation(self):
        with self.assertRaises(ValueError):
            LayupRecipe(plies=0)
        with self.assertRaises(ValueError):
            LayupRecipe(fabric_waste_frac=1.5)


class TestSchedule(unittest.TestCase):
    def _steps(self, glue_cure=120, lam_cure=240):
        plan = SlicePlan.plan(_meeting_body(), _sheet())
        return plan, default_build_day(plan, LayupRecipe(),
                                       adhesive_cure_min=glue_cure,
                                       laminating_cure_min=lam_cure)

    def test_dependencies_and_cures_respected(self):
        _, steps = self._steps()
        sched = BuildDaySchedule.plan(steps)
        byk = {x.step.key: x for x in sched.scheduled}
        # rough shaping cannot start before the glue CURE (not just the gluing) ends
        self.assertGreaterEqual(byk["rough"].start_min, byk["glue"].cure_end_min)
        # layup strictly after the release barrier has flashed
        self.assertGreaterEqual(byk["layup1"].start_min, byk["release"].cure_end_min)
        # one crew never does two things at once
        for crew in ("cut", "shape", "layup"):
            xs = sorted((x for x in sched.scheduled if x.step.crew == crew),
                        key=lambda x: x.start_min)
            for a, b in zip(xs, xs[1:]):
                self.assertGreaterEqual(b.start_min, a.active_end_min)

    def test_crew_parallelism_during_cure(self):
        _, steps = self._steps()
        sched = BuildDaySchedule.plan(steps)
        byk = {x.step.key: x for x in sched.scheduled}
        # tool prep (shape crew) runs while the glue cures — the whole point
        self.assertLess(byk["prep_tools"].start_min, byk["glue"].cure_end_min)

    def test_realistic_cures_honestly_fail_the_day(self):
        _, steps = self._steps(glue_cure=120, lam_cure=240)
        sched = BuildDaySchedule.plan(steps, day_start="08:00", day_end="20:00")
        self.assertFalse(sched.fits_in_day)          # slide 13 vs reality
        self.assertIn("DOES NOT FIT", sched.verdict)
        self.assertTrue(sched.suggestions)
        self.assertTrue(any("evening before" in s or "next morning" in s
                            for s in sched.suggestions))

    def test_fast_cures_fit(self):
        _, steps = self._steps(glue_cure=30, lam_cure=60)
        sched = BuildDaySchedule.plan(steps, day_start="08:00", day_end="20:00")
        self.assertTrue(sched.fits_in_day)
        self.assertIn("fits", sched.verdict)

    def test_critical_path_named_and_cycle_detected(self):
        _, steps = self._steps()
        sched = BuildDaySchedule.plan(steps)
        self.assertIn("layup2", sched.critical_path)
        cyc = (BuildStep("a", "A", "cut", 10, needs=("b",)),
               BuildStep("b", "B", "cut", 10, needs=("a",)))
        with self.assertRaises(ValueError):
            BuildDaySchedule.plan(cyc)
        with self.assertRaises(ValueError):
            BuildDaySchedule.plan(
                (BuildStep("a", "A", "cut", 10, needs=("ghost",)),))

    def test_clock_formatting(self):
        _, steps = self._steps(glue_cure=30, lam_cure=60)
        sched = BuildDaySchedule.plan(steps, day_start="08:00")
        self.assertEqual(sched.clock(0), "08:00")
        self.assertEqual(sched.clock(90), "09:30")


class TestGatesAndPlan(unittest.TestCase):
    def test_gate_passes_a_sound_plan(self):
        plan = SlicePlan.plan(_meeting_body(), _sheet())
        recipe = LayupRecipe()
        sched = BuildDaySchedule.plan(default_build_day(plan, recipe))
        gate = PreflightGate.check(plan, recipe, schedule=sched, crew_size=6)
        self.assertTrue(gate.go)
        self.assertIn("GO", gate.summary())

    def test_gate_trips_on_thin_coating(self):
        plan = SlicePlan.plan(_meeting_body(), _sheet())
        recipe = LayupRecipe(coating_thickness_mm=0.35, flatting_removal_mm=0.3)
        sched = BuildDaySchedule.plan(default_build_day(plan, recipe))
        gate = PreflightGate.check(plan, recipe, schedule=sched, crew_size=6)
        self.assertFalse(gate.go)
        self.assertTrue(any("structural carbon" in i.text
                            for i in gate.items if not i.ok))

    def test_gate_trips_on_missing_crew(self):
        plan = SlicePlan.plan(_meeting_body(), _sheet())
        recipe = LayupRecipe()
        gate = PreflightGate.check(plan, recipe, crew_size=0)
        self.assertFalse(gate.go)

    def test_gate_trips_on_layup_before_release(self):
        plan = SlicePlan.plan(_meeting_body(), _sheet())
        recipe = LayupRecipe()
        # a deliberately broken day: layup with no release step at all
        steps = (BuildStep("layup1", "layup", "layup", 60),)
        sched = BuildDaySchedule.plan(steps)
        gate = PreflightGate.check(plan, recipe, schedule=sched, crew_size=6)
        self.assertFalse(gate.go)

    def test_full_plan_report_and_provenance(self):
        body, sheet = _meeting_body(), _sheet()
        plan = SlicePlan.plan(body, sheet)
        recipe = LayupRecipe()
        bom = MaterialsEstimate.compute(body, plan, recipe, sheet, crew_size=6)
        sched = BuildDaySchedule.plan(default_build_day(plan, recipe))
        spec = ScaleSpec(ratio=0.4, scaled_chord_mm=520,
                         scaled_height_mm=260, scaled_width_mm=250)
        gate = PreflightGate.check(plan, recipe, schedule=sched, crew_size=6)
        build = PlugBuildPlan(body, plan, recipe, bom, sched, gate=gate,
                              scale_spec=spec)
        rep = build.report()
        for token in ("SLICE PLAN", "MATERIALS", "BUILD DAY", "PREFLIGHT",
                      "provenance:", "ESTIMATE"):
            self.assertIn(token, rep)
        self.assertIn("1:2.5", build.provenance())
        budget = build.tolerance_budget()
        self.assertIsNotNone(budget)
        self.assertGreater(budget.report().cl_uncertainty_frac, 0.0)

    def test_tolerance_budget_needs_scale(self):
        body, sheet = _meeting_body(), _sheet()
        plan = SlicePlan.plan(body, sheet)
        recipe = LayupRecipe()
        bom = MaterialsEstimate.compute(body, plan, recipe, sheet, crew_size=6)
        sched = BuildDaySchedule.plan(default_build_day(plan, recipe))
        build = PlugBuildPlan(body, plan, recipe, bom, sched)
        self.assertIsNone(build.tolerance_budget())


class TestPackageExports(unittest.TestCase):
    def test_importable_from_suspension_aero(self):
        from suspension.aero import (NoseconeBody as N, SlicePlan as S,
                                     MaterialsEstimate as M, BuildDaySchedule as B,
                                     PreflightGate as G, PlugBuildPlan as P)
        self.assertTrue(all((N, S, M, B, G, P)))


if __name__ == "__main__":
    unittest.main()
