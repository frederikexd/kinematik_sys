# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""Frame Planner demo — the 06/29 chassis meeting, computed end to end.

Run:  python demo_frame_planner.py
"""

from suspension import tubeframe as tf


def main():
    g = tf.demo_frame()

    print("=" * 72)
    print("1 · TRIANGULATION / LOAD-PATH AUDIT  (slide 4)")
    print("=" * 72)
    for f in g.midspan_landings():
        print(f"  ⚠ mid-span landing: node {f['end_node']} lands on "
              f"{f['host_tube']} at {f['host_fraction']:.0%} "
              f"(tubes: {', '.join(f['tubes'])})")
    for q in g.untriangulated_quads():
        print(f"  ⚠ open bay {'-'.join(q['bay_nodes'])} → add diagonal "
              f"{' → '.join(q['suggested_diagonal'])} "
              f"({q['diagonal_length_mm']:.0f} mm, size {q['diagonal_size']}, "
              f"{q['diagonal_mass_kg']:.2f} kg, ${q['diagonal_cost_usd']:.2f})")

    aud = g.load_path_audit(tf.DEMO_PATH_FROM, tf.DEMO_PATH_TO)
    print(f"\n  Main hoop support → lower side impact node: "
          f"[{aud['verdict'].upper()}] {aud['summary']}")

    # apply the suggested fix on the failing node and re-audit
    fix = next(w["fix"] for w in aud["weak_nodes"]
               if "suggested_diagonal" in w["fix"])
    d = fix["suggested_diagonal"]
    g.add_tube("fix_diag", d[0], d[1], member_class="side_impact", size="B")
    aud2 = g.load_path_audit(tf.DEMO_PATH_FROM, tf.DEMO_PATH_TO)
    print(f"  After adding {d[0]} → {d[1]}: [{aud2['verdict'].upper()}] "
          f"{aud2['summary']}")

    print("\n" + "=" * 72)
    print("2 · TUBE SOURCING TRADE STUDY  (slide 5)")
    print("=" * 72)
    bom = g.bom_by_spec()
    for r in bom["by_spec"]:
        print(f"  Size {r['size']} ({r['od_mm']:g}×{r['wall_mm']:g}): "
              f"{r['n_tubes']} tubes, {r['length_ft']:.1f} ft, "
              f"{r['mass_kg']:.2f} kg, ${r['cost_usd']:.2f}"
              + (f"  [{r['sourcing_risk']}]" if r["sourcing_risk"] else ""))
    t = g.consolidate_spec("C", "B")
    print(f"\n  'Increase Size C to match Size B': {t['n_tubes']} tubes, "
          f"{t['delta_mass_kg']:+.2f} kg, {t['delta_cost_usd']:+.2f} $, "
          f"{len(t['rules_violations'])} rules violations")
    eq = tf.equivalency_check(tf.TubeSpec("alt", 28.0, 1.6), "side_impact")
    print(f"  Alternative 28.0×1.6 vs side-impact baseline: "
          f"{'PASS' if eq['passes'] else 'FAIL'} "
          f"(EI ×{eq['EI_ratio']:.2f}, S ×{eq['bending_strength_ratio']:.2f}, "
          f"{eq['mass_per_m_delta_kg']:+.3f} kg/m)")

    print("\n" + "=" * 72)
    print("3 · PANELS & ATTACHMENTS  (slides 7/9 subteam briefs)")
    print("=" * 72)
    plan = tf.plan_panel_attachment("aero", 900, 450, 2.0,
                                    "Carbon laminate (quasi-iso)", 150.0,
                                    speed_kph=110)
    print(f"  Aero panel 900×450×2 CF @ 150 mm pitch, 110 km/h: "
          f"{plan.n_fasteners} fasteners, {plan.load_per_fastener_N:.0f} N "
          f"each, {plan.deflection_mm:.2f} mm sag, max stable pitch "
          f"{plan.max_pitch_mm:.0f} mm  [{plan.verdict.upper()}]")
    ok = [o["name"] for o in plan.options if o["ok"] and o["quick_release"]]
    print(f"  Quick-release options that screen green: {', '.join(ok)}")

    h = tf.harness_attachment_loads()
    for p in h["points"]:
        print(f"  Harness {p['point']}: {p['belt_tension_N']:.0f} N "
              f"→ {p['mounts_to']}")
    s = tf.seat_mount_check(4.0, 77.0)
    print(f"  Removable seat, 4 × M6+nutplate: "
          f"{s['load_per_mount_N']:.0f} N per mount  [{s['verdict'].upper()}]")

    print("\n  Ledger declaration:", tf.frame_summary_for_ledger(g))
    print("\n  " + tf.RULES_DISCLAIMER)


if __name__ == "__main__":
    main()
