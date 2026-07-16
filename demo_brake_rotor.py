# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
demo_brake_rotor.py — the requested workflow, end to end.

Spin a wheel in a virtual tunnel to get the speed-dependent convective coefficient
h_c, push a real autocross lap's braking energy (P = F_brake · v) through a
TRANSIENT rotor->hat->caliper->fluid thermal model, watch the friction ring heat
into every corner and shed on every straight, then let the tool mill the rotor as
light as the peak temperature allows — while proving the pad never fades and the
caliper fluid never boils. All in software, before anyone cuts metal.

Run:  python demo_brake_rotor.py
"""

import numpy as np

from suspension import (
    SuspensionKinematics, Hardpoints, VehicleDynamics, VehicleParams,
    default_tire,
    ReferenceRotorCFD, build_convective_map,
    RotorGeometry, RotorThermalParams, PadSpec, BRAKE_FLUIDS,
    simulate_rotor_thermal, fluid_boil_check,
    rotor_candidate_grid, optimize_rotor,
)
from suspension.lapsim import LapSimulator, LapSimParams, autocross_track


def sparkline(y, lo=None, hi=None, width=None):
    y = np.asarray(y, float)
    if width and y.size > width:
        idx = np.linspace(0, y.size - 1, width).astype(int)
        y = y[idx]
    ramp = "▁▂▃▄▅▆▇█"
    lo = float(np.nanmin(y)) if lo is None else lo
    hi = float(np.nanmax(y)) if hi is None else hi
    rng = max(hi - lo, 1e-6)
    return "".join(ramp[min(int((v - lo) / rng * (len(ramp) - 1)), len(ramp) - 1)]
                   for v in y)


def main():
    # --- the car + one autocross lap (the braking-energy source) ---
    kin = SuspensionKinematics(Hardpoints.default())
    veh = VehicleDynamics(VehicleParams(), front_kin=kin, rear_kin=kin,
                          tire=default_tire())
    params = LapSimParams()
    lap = LapSimulator(veh, params).simulate(autocross_track(laps=1))
    print(f"Lap: {lap.lap_time:.2f} s, {lap.top_speed:.1f} m/s top, "
          f"{lap.avg_speed:.1f} m/s avg")

    # === 1) CONVECTIVE COOLING MAP — spin the wheel in the virtual tunnel ===
    # The wheel is spun at the angular speed that matches each road speed, so the
    # solver reports the barrel mass flow and the speed-dependent h_c. (Reference
    # analytic backend here; an OpenFOAM/STAR-CCM+ rotating-wheel solve drops into
    # the same seam — see ReferenceRotorCFD vs OpenFOAMRotorCFD.)
    cmap = build_convective_map(ReferenceRotorCFD(),
                                speeds_ms=[5, 10, 15, 20, 25, 30])
    print("\n[1] Convective cooling map (wheel spun in tunnel):")
    for v, h, m in zip(cmap.speeds_ms, cmap.h_face, cmap.mdot_barrel):
        print(f"      {v:4.0f} m/s -> h_face {h:6.1f} W/m²K,  "
              f"barrel ṁ {m*1000:5.1f} g/s")
    print("      " + cmap.provenance.status().split(" — ")[0] + ".")

    # === 2) TRANSIENT THERMAL FEA — alternating brake flux over the lap ===
    baseline = RotorGeometry(ring_thickness_mm=7.0, is_vented=False)  # solid disc
    pad = PadSpec(name="endurance compound", degradation_c=550.0)
    fluid = BRAKE_FLUIDS["Motul RBF 600"]
    res = simulate_rotor_thermal(lap, params, geom=baseline, cmap=cmap,
                                 pad=pad, fluid=fluid, corner="front", n_laps=4)
    print(f"\n[2] Transient FRONT rotor over 4 laps "
          f"(baseline solid {baseline.ring_thickness_mm:.0f} mm, "
          f"{baseline.total_mass_kg():.2f} kg):")
    print("      ring  " + sparkline(res.ring_temp_c, width=64)
          + f"  peak {res.peak_ring_c:.0f}°C")
    print("      fluid " + sparkline(res.fluid_temp_c, width=64)
          + f"  peak {res.peak_fluid_c:.0f}°C")
    print("      (ring climbs into every corner, sheds on every straight — the "
          "alternating heat-flux cycle, straight from the lap trace.)")
    pad_hr, fl_hr = res.headroom_c()
    print(f"      pad fade limit {pad.degradation_c:.0f}°C -> "
          f"{pad_hr:+.0f}°C headroom; "
          f"{'FADES' if res.ring_fades else 'no fade'}")

    # === caliper fluid boil check ===
    fc = fluid_boil_check(res, fluid, using_wet=True)
    print(f"\n      {fc.summary()}")

    # === 3) MASS REDUCTION & VENT OPTIMISATION ===
    # The ring has 166°C of pad headroom at baseline — that's mass we can remove.
    # Search ring thickness, vent fraction, cross-drillings and hat mass; keep only
    # rotors whose transient peak still passes BOTH the pad and fluid limits.
    cands = rotor_candidate_grid(
        baseline,
        thickness_mm=[7.0, 6.0, 5.0, 4.0, 3.5],
        vent_fraction=[0.0, 0.25, 0.45],
        n_drillings=[0, 24, 48],
        hat_mass_kg=[0.35, 0.25, 0.18],
        vented=[True])
    print(f"\n[3] Mass-reduction search over {len(cands)} rotor geometries "
          f"(shared cooling map, transient peak per candidate):")
    opt = optimize_rotor(lap, params, cands, cmap=cmap, pad=pad, fluid=fluid,
                         corner="front", n_laps=4, baseline=baseline)
    print("    " + opt.summary().replace("\n", "\n    "))

    print("\nEvery temperature above is from a transparent lumped network with "
          "representative\nconductances and an uncorrelated h_c map — the RANKING "
          "is what to trust. Calibrate\nthe masses, contact conductances and h_c "
          "against a real solve + thermocouple before\nreporting an absolute "
          "margin, exactly as for the tyre and pack thermal models.")


if __name__ == "__main__":
    main()
