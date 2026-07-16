# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""PCB Doctor — parse a real board file, diagnose real-life failures, name the
component, patch the traces in place, and verify the patched file re-parses."""

import math
import unittest

from suspension.pcb_doctor import (
    parse_kicad_pcb, demo_kicad_pcb, auto_assign_net_currents, diagnose,
    apply_fixes, fix_report_md, prescribe_trace, required_width_mm,
    vias_needed, find_diff_pairs, board_svg, clearance_required_mm)
from suspension.interfaces import Severity


def _demo_setup(fan_a=8.0):
    board = parse_kicad_pcb(demo_kicad_pcb())
    assignments = auto_assign_net_currents(board, ledger=None)
    fan = board.net_id("FAN_PWR")
    assignments[fan]["current_a"] = fan_a
    hv = board.net_id("HV_INV_SENSE")
    assignments[hv]["voltage_v"] = 400.0
    return board, assignments


class TestParser(unittest.TestCase):
    def test_parses_everything(self):
        board = parse_kicad_pcb(demo_kicad_pcb())
        self.assertIn("FAN_PWR", board.nets.values())
        self.assertGreaterEqual(len(board.segments), 10)
        self.assertEqual(len(board.vias), 2)
        refs = {fp.ref for fp in board.footprints}
        self.assertTrue({"J1", "U1", "C1", "F1", "U2", "J2"} <= refs)
        self.assertIn("In1.Cu", board.copper_layers)
        self.assertAlmostEqual(board.board_thickness_mm, 1.6)

    def test_rejects_non_board(self):
        with self.assertRaises(ValueError):
            parse_kicad_pcb("(schematic (net 1))")

    def test_width_spans_point_at_the_width(self):
        board = parse_kicad_pcb(demo_kicad_pcb())
        for s in board.segments:
            a, b = s.width_span
            self.assertEqual(float(board.text[a:b]), s.width_mm)


class TestPrescriber(unittest.TestCase):
    def test_width_grows_with_current_and_shrinks_with_copper(self):
        w1 = required_width_mm(5.0, 20.0, 1.0, external=True)
        w2 = required_width_mm(10.0, 20.0, 1.0, external=True)
        w3 = required_width_mm(5.0, 20.0, 2.0, external=True)
        self.assertGreater(w2, w1)
        self.assertLess(w3, w1)
        # inner layers need more copper than outer
        self.assertGreater(required_width_mm(5.0, 20.0, 1.0, external=False), w1)

    def test_prescription_shape(self):
        p = prescribe_trace(8.0, dT_c=20.0, length_mm=120.0)
        self.assertEqual(len(p["rows"]), 6)
        self.assertGreaterEqual(p["vias_per_transition"], 2)
        self.assertGreater(vias_needed(8.0, 0.3, 20.0), 1)

    def test_clearance_table(self):
        self.assertAlmostEqual(clearance_required_mm(12), 0.1)
        self.assertAlmostEqual(clearance_required_mm(400), 2.5)
        self.assertGreater(clearance_required_mm(600), 2.5)


class TestDiagnosis(unittest.TestCase):
    def test_finds_the_planted_failures(self):
        board, assignments = _demo_setup(fan_a=8.0)
        rep = diagnose(board, assignments)
        checks = " | ".join(f.check for f in rep.findings)
        self.assertIn("trace ampacity — FAN_PWR", checks)
        self.assertIn("via bottleneck — FAN_PWR", checks)
        self.assertIn("fusing margin — FAN_PWR", checks)
        self.assertIn("component — C1", checks)      # cap on hot copper
        self.assertIn("component — F1", checks)      # 5 A fuse on an 8 A net
        self.assertIn("HV clearance — HV_INV_SENSE", checks)
        self.assertIn("HV coupling — CAN", checks)
        self.assertIn("diff pair skew — CAN", checks)
        self.assertTrue(any(f.severity == Severity.FAIL for f in rep.findings))
        self.assertTrue(rep.fixes)

    def test_quiet_board_is_ok(self):
        board, assignments = _demo_setup(fan_a=0.5)
        for a in assignments.values():
            a["current_a"] = 0.2
            a["voltage_v"] = 5.0
        rep = diagnose(board, assignments)
        hard = [f for f in rep.findings
                if f.severity == Severity.FAIL and "open" not in f.check]
        self.assertFalse(hard, [f.check for f in hard])

    def test_diff_pair_detection(self):
        board = parse_kicad_pcb(demo_kicad_pcb())
        pairs = find_diff_pairs(board)
        self.assertTrue(any(base.upper().startswith("CAN") for base, _, _ in pairs))

    def test_ir_drop_nodal_analysis_runs(self):
        board, assignments = _demo_setup(fan_a=8.0)
        rep = diagnose(board, assignments)
        nr = rep.net_reports[board.net_id("FAN_PWR")]
        self.assertIsNotNone(nr["worst_r_ohm"])
        self.assertGreater(nr["worst_r_ohm"], 0.0)
        self.assertFalse(nr["open_groups"])   # demo fan net is fully connected


class TestAutoFix(unittest.TestCase):
    def test_patched_file_reparses_with_wider_traces(self):
        board, assignments = _demo_setup(fan_a=8.0)
        rep = diagnose(board, assignments)
        patched, applied = apply_fixes(board, rep.fixes)
        self.assertTrue(applied)
        board2 = parse_kicad_pcb(patched)
        fan = board2.net_id("FAN_PWR")
        old_min = min(s.width_mm for s in board.segments_of(board.net_id("FAN_PWR")))
        new_min = min(s.width_mm for s in board2.segments_of(fan))
        self.assertGreater(new_min, old_min)
        # patched geometry clears ampacity at the same current
        assignments2 = dict(assignments)
        rep2 = diagnose(board2, assignments2)
        amp2 = [f for f in rep2.findings
                if f.check.startswith("trace ampacity — FAN_PWR")
                and f.severity == Severity.FAIL]
        self.assertFalse(amp2)
        # only widths changed: same net list, same segment count
        self.assertEqual(board.nets, board2.nets)
        self.assertEqual(len(board.segments), len(board2.segments))

    def test_diff_pair_members_never_auto_widened(self):
        board, assignments = _demo_setup()
        can = board.net_id("CAN_H")
        assignments[can]["current_a"] = 6.0   # absurd, to force a width finding
        rep = diagnose(board, assignments)
        pair_fixes = [fx for fx in rep.fixes if fx.nid == can]
        self.assertTrue(pair_fixes)
        self.assertTrue(all(not fx.auto for fx in pair_fixes))

    def test_fix_report_and_svg_render(self):
        board, assignments = _demo_setup(fan_a=8.0)
        rep = diagnose(board, assignments)
        patched, applied = apply_fixes(board, rep.fixes)
        md = fix_report_md(board, rep, applied, assignments)
        self.assertIn("Widths rewritten", md)
        self.assertIn("FAN_PWR", md)
        svg = board_svg(board, report=rep)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("#ff3333", svg)   # failing copper is haloed


if __name__ == "__main__":
    unittest.main()
