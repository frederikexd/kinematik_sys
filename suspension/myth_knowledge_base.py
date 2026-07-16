# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
myth_knowledge_base.py — the built-in FSAE physics graph for the Myth-Buster
============================================================================

The point of this file is COVERAGE. A team member should be able to type almost
any vehicle-dynamics assumption a Formula Student team argues about — aero,
tyres, mass, springs, dampers, geometry, powertrain (EV and combustion),
braking, chassis, thermal — and get back a real TRUE / MYTH / DEPENDS with an
explanation, instead of "unknown".

It is pure data: three lists (``ENTITIES``, ``FORMULAS``, ``RELATIONSHIPS``) plus
broad ``FALLBACK_LAWS`` for the long tail. The engine (myth_entity_engine.py)
loads these; nothing here imports the engine or Streamlit, so it stays trivially
editable and testable. Discipline leads extend it from the UI (which appends to
the user store) or by adding a dict here.

HOW COVERAGE WORKS
------------------
1. ENTITIES carry generous ``aliases`` — the surface words people actually type
   ("df", "stiffer springs", "unsprung", "regen", "rotor", "psi"). Broad aliases
   are what let a casual phrasing resolve to the right quantity.
2. RELATIONSHIPS are directed edges between two entities with a verdict. Most
   real assumptions are "more X → Y?", which is exactly an edge. Many edges are
   ``bidirectional`` so the question works asked either way.
3. FALLBACK_LAWS catch pairs with no specific edge using coarse entity ``kind``
   (force/speed/mass/grip/…), so even an un-modelled pairing gets a physically
   honest DEPENDS rather than silence.

VERDICT DISCIPLINE
------------------
We are honest, not flattering. A claim that's only true with caveats is DEPENDS,
not TRUE. Common oversimplifications FSAE teams believe ("lighter is always
faster", "stiffer is always better", "more downforce is free speed") are tagged
MYTH or DEPENDS with the reason. The explanation always says where to verify
(lap sim, skidpad, FEA, dyno) so the tool teaches rather than just judges.
"""

from __future__ import annotations


# =========================================================================== #
#  ENTITIES — the quantities claims are about                                 #
# =========================================================================== #
# kind buckets (used by fallback laws): force, speed, time, mass, grip,
# stiffness, angle, energy, power, thermal, pressure, length, ratio, count.
ENTITIES = [
    # ---- aero -------------------------------------------------------------
    dict(slug="downforce", label="Downforce", discipline="aerodynamics",
         symbol="F_z", canonical_unit="N", kind="force",
         aliases=["downforce", "down force", "df", "aero load", "vertical load",
                  "aero grip", "grip from aero", "wing load"],
         registry_key="aero.downforce_n"),
    dict(slug="drag", label="Drag", discipline="aerodynamics", symbol="F_x",
         canonical_unit="N", kind="force",
         aliases=["drag", "aero drag", "cda", "cd a", "air resistance"]),
    dict(slug="ld_ratio", label="Aero efficiency (L/D)", discipline="aerodynamics",
         symbol="LD", canonical_unit="", kind="ratio",
         aliases=["l/d", "lift to drag", "aero efficiency", "downforce to drag",
                  "efficiency of the wing"]),
    dict(slug="ride_height", label="Ride height", discipline="aerodynamics",
         symbol="h", canonical_unit="mm", kind="length",
         aliases=["ride height", "ride-height", "lower the car", "car height",
                  "rake"]),
    # ---- speed / time -----------------------------------------------------
    dict(slug="speed", label="Speed", discipline="shared", symbol="v",
         canonical_unit="m/s", kind="speed",
         aliases=["speed", "velocity", "top speed", "straight line speed",
                  "straight-line speed", "how fast", "faster", "quicker",
                  "more pace", "pace"]),
    dict(slug="accel", label="Acceleration", discipline="shared", symbol="a",
         canonical_unit="m/s^2", kind="speed",
         aliases=["acceleration", "accelerate", "launch", "off the line",
                  "0-100", "pickup", "get going"]),
    dict(slug="laptime", label="Lap time", discipline="shared", symbol="t",
         canonical_unit="s", kind="time",
         aliases=["lap time", "laptime", "faster lap", "quicker lap",
                  "lower lap", "lap", "overall pace"]),
    dict(slug="cornerspeed", label="Cornering speed", discipline="suspension",
         symbol="v_c", canonical_unit="m/s", kind="speed",
         aliases=["corner speed", "cornering speed", "cornering", "mid-corner speed",
                  "through the corners", "corner exit speed", "corners",
                  "cornering performance"]),
    # ---- grip / tyre ------------------------------------------------------
    dict(slug="grip", label="Mechanical grip", discipline="suspension",
         symbol="mu", canonical_unit="", kind="grip",
         aliases=["grip", "mechanical grip", "traction", "tyre grip",
                  "tire grip", "lateral grip", "cornering grip",
                  "grip in corners", "hold the road"]),
    dict(slug="tyre_grip_total", label="Total tyre force", discipline="suspension",
         symbol="F_t", canonical_unit="N", kind="force",
         aliases=["tyre force", "tire force", "contact patch force",
                  "friction force"]),
    dict(slug="tyre_pressure", label="Tyre pressure", discipline="suspension",
         symbol="P", canonical_unit="psi", kind="pressure",
         aliases=["tyre pressure", "tire pressure", "psi", "inflation",
                  "pressures", "hot pressure", "cold pressure"]),
    dict(slug="tyre_temp", label="Tyre temperature", discipline="suspension",
         symbol="T_t", canonical_unit="C", kind="thermal",
         aliases=["tyre temp", "tire temp", "tyre temperature",
                  "tire temperature", "tyre heat", "warm the tyres", "hotter tyres",
                  "hotter tires", "hot tyres", "tyre warmers",
                  "get heat in the tyres"]),
    dict(slug="tyre_width", label="Tyre width", discipline="suspension",
         symbol="w_t", canonical_unit="mm", kind="length",
         aliases=["tyre width", "tire width", "wider tyres", "wider tires",
                  "bigger tyres", "wider contact patch", "tyre size"]),
    dict(slug="contact_patch", label="Contact patch", discipline="suspension",
         symbol="A_c", canonical_unit="mm^2", kind="length",
         aliases=["contact patch", "footprint", "rubber on the road"]),
    # ---- mass -------------------------------------------------------------
    dict(slug="mass", label="Vehicle mass", discipline="shared", symbol="m",
         canonical_unit="kg", kind="mass",
         aliases=["weight", "mass", "lighter", "heavier", "lightness",
                  "heavy", "light", "car weight", "kerb weight", "lighter car",
                  "heavier car", "shed weight", "add weight"]),
    dict(slug="unsprung", label="Unsprung mass", discipline="suspension",
         symbol="m_u", canonical_unit="kg", kind="mass",
         aliases=["unsprung", "unsprung mass", "unsprung weight",
                  "wheel mass", "rotating mass at the wheel"]),
    dict(slug="cg_height", label="CG height", discipline="suspension",
         symbol="h_cg", canonical_unit="mm", kind="length",
         aliases=["cg height", "centre of gravity", "center of gravity",
                  "cog", "cg", "lower cg", "lower the cg", "high cg"]),
    dict(slug="weight_dist", label="Weight distribution", discipline="suspension",
         symbol="wd", canonical_unit="%", kind="ratio",
         aliases=["weight distribution", "weight balance", "front rear balance",
                  "front-to-rear", "static balance"]),
    # ---- suspension / kinematics -----------------------------------------
    dict(slug="spring_rate", label="Spring rate / stiffness", discipline="suspension",
         symbol="k", canonical_unit="N/mm", kind="stiffness",
         aliases=["spring rate", "stiffer springs", "stiffer", "softer springs",
                  "softer", "wheel rate", "spring stiffness", "stiff suspension",
                  "soft suspension", "stiffer setup", "spring"]),
    dict(slug="arb", label="Anti-roll bar", discipline="suspension",
         symbol="k_arb", canonical_unit="N/mm", kind="stiffness",
         aliases=["stiffer anti-roll bar", "stiffer anti roll bar", "stiffer arb",
                  "stiffer sway bar", "anti-roll bar", "anti roll bar", "arb",
                  "sway bar", "roll bar", "roll stiffness"]),
    dict(slug="roll", label="Body roll", discipline="suspension",
         symbol="phi", canonical_unit="deg", kind="angle",
         aliases=["body roll", "roll", "lean", "roll angle", "leans over"]),
    dict(slug="camber", label="Camber", discipline="suspension",
         symbol="gamma", canonical_unit="deg", kind="angle",
         aliases=["camber", "negative camber", "camber angle", "more camber",
                  "static camber", "camber gain"]),
    dict(slug="toe", label="Toe", discipline="suspension", symbol="toe",
         canonical_unit="deg", kind="angle",
         aliases=["toe", "toe in", "toe-in", "toe out", "toe-out", "toe angle"]),
    dict(slug="caster", label="Caster", discipline="suspension", symbol="caster",
         canonical_unit="deg", kind="angle",
         aliases=["caster", "castor", "caster angle"]),
    dict(slug="damping", label="Damping", discipline="suspension", symbol="c",
         canonical_unit="Ns/mm", kind="stiffness",
         aliases=["damping", "dampers", "shocks", "stiffer dampers",
                  "softer dampers", "rebound", "compression damping"]),
    dict(slug="mech_grip", label="Mechanical balance", discipline="suspension",
         symbol="bal", canonical_unit="", kind="grip",
         aliases=["balance", "understeer", "oversteer", "handling",
                  "handles better", "better handling", "neutral handling"]),
    dict(slug="track_width", label="Track width", discipline="suspension",
         symbol="t_w", canonical_unit="mm", kind="length",
         aliases=["track width", "wider track", "track", "wheelbase width"]),
    dict(slug="wheelbase", label="Wheelbase", discipline="suspension",
         symbol="L", canonical_unit="mm", kind="length",
         aliases=["wheelbase", "longer wheelbase", "shorter wheelbase"]),
    # ---- powertrain -------------------------------------------------------
    dict(slug="power", label="Power", discipline="powertrain", symbol="P",
         canonical_unit="kW", kind="power",
         aliases=["power", "more power", "horsepower", "hp", "kw", "engine power",
                  "motor power", "output"]),
    dict(slug="torque", label="Torque", discipline="powertrain", symbol="tq",
         canonical_unit="Nm", kind="force",
         aliases=["torque", "more torque", "wheel torque", "pull", "grunt"]),
    dict(slug="voltage", label="Pack voltage", discipline="electrics", symbol="V",
         canonical_unit="V", kind="ratio",
         aliases=["voltage", "higher voltage", "pack voltage", "bus voltage",
                  "more cells in series"]),
    dict(slug="gear_ratio", label="Final drive ratio", discipline="powertrain",
         symbol="G", canonical_unit="", kind="ratio",
         aliases=["gear ratio", "final drive", "gearing", "shorter gearing",
                  "taller gearing", "diff ratio", "sprocket"]),
    dict(slug="traction_limit", label="Traction limit", discipline="powertrain",
         symbol="F_tr", canonical_unit="N", kind="force",
         aliases=["traction limit", "wheelspin", "spin the wheels",
                  "put the power down", "traction"]),
    dict(slug="regen", label="Regen braking", discipline="electrics", symbol="reg",
         canonical_unit="", kind="energy",
         aliases=["regen", "regenerative braking", "energy recovery",
                  "recover energy"]),
    dict(slug="range_energy", label="Energy / endurance range", discipline="electrics",
         symbol="E", canonical_unit="kWh", kind="energy",
         aliases=["range", "energy use", "efficiency endurance", "battery life",
                  "endurance range", "wh per lap", "consumption"]),
    # ---- braking ----------------------------------------------------------
    dict(slug="brake_force", label="Braking force", discipline="brakes",
         symbol="F_b", canonical_unit="N", kind="force",
         aliases=["braking", "braking force", "stopping power", "brake harder",
                  "stop faster", "stopping", "decel", "deceleration"]),
    dict(slug="rotor_size", label="Brake rotor size", discipline="brakes",
         symbol="r_d", canonical_unit="mm", kind="length",
         aliases=["bigger rotor", "bigger rotors", "rotor", "rotors", "rotor size",
                  "disc size", "bigger disc", "larger rotor", "brake disc",
                  "brake rotor", "brake rotors"]),
    dict(slug="brake_bias", label="Brake bias", discipline="brakes", symbol="bias",
         canonical_unit="%", kind="ratio",
         aliases=["brake bias", "bias", "front bias", "rear bias",
                  "brake balance"]),
    dict(slug="brake_temp", label="Brake temperature", discipline="brakes",
         symbol="T_b", canonical_unit="C", kind="thermal",
         aliases=["brake temp", "brake fade", "brake temperature", "fade",
                  "overheating brakes"]),
    # ---- chassis / structure ---------------------------------------------
    dict(slug="chassis_stiff", label="Chassis stiffness", discipline="chassis",
         symbol="k_c", canonical_unit="Nm/deg", kind="stiffness",
         aliases=["chassis stiffness", "torsional stiffness", "stiffer chassis",
                  "frame stiffness", "stiffer frame", "rigid chassis",
                  "chassis rigidity"]),
    dict(slug="fos", label="Factor of safety", discipline="chassis", symbol="FoS",
         canonical_unit="", kind="ratio",
         aliases=["factor of safety", "fos", "safety factor", "stronger part",
                  "thicker wall", "more material"]),
    dict(slug="part_mass", label="Part mass", discipline="chassis", symbol="m_p",
         canonical_unit="kg", kind="mass",
         aliases=["part weight", "component weight", "bracket weight",
                  "thinner wall", "thicker wall", "thicker walls", "wall thickness",
                  "lighten the part", "pocketing", "add material", "more material"]),
    dict(slug="strength", label="Part strength", discipline="chassis", symbol="sigma",
         canonical_unit="MPa", kind="force",
         aliases=["stronger part", "stronger", "strength", "beefier",
                  "stiffer part", "load capacity"]),
    # ---- generic "is it better / faster overall" target -------------------
    # Lets single-quantity claims ("is a stiffer chassis better?") resolve to a
    # performance target so they get a real verdict instead of 'unknown'.
    dict(slug="performance", label="Overall performance", discipline="shared",
         symbol="perf", canonical_unit="", kind="performance",
         aliases=["better", "worse", "improve", "improves", "improved",
                  "best", "good", "always better", "is better", "performance",
                  "overall", "competitive"]),
]


# =========================================================================== #
#  FORMULAS — deterministic expressions the safe sandbox evaluates            #
# =========================================================================== #
FORMULAS = [
    dict(slug="aero_force", label="Aerodynamic force", discipline="aerodynamics",
         expression="0.5 * rho * v**2 * CA", inputs=["rho", "v", "CA"],
         defaults={"rho": 1.225, "CA": 1.0}, output_unit="N", basis="physics"),
    dict(slug="speed_force_ratio", label="Force ratio from speed ratio",
         discipline="shared", expression="(v2 / v1)**2", inputs=["v1", "v2"],
         defaults={}, output_unit="ratio", basis="physics"),
    dict(slug="accel_from_mass", label="a = F/m", discipline="shared",
         expression="F / m", inputs=["F", "m"], defaults={"F": 1.0, "m": 1.0},
         output_unit="m/s^2", basis="physics"),
    dict(slug="kinetic_energy", label="Braking energy", discipline="brakes",
         expression="0.5 * m * v**2", inputs=["m", "v"],
         defaults={"m": 250.0, "v": 20.0}, output_unit="J", basis="physics"),
]


# =========================================================================== #
#  RELATIONSHIPS — directed edges, the bulk of the coverage                   #
# =========================================================================== #
# Helper to keep the list readable.
def _rel(slug, src, tgt, effect, verdict, expl, disc="shared",
         basis="modeled", prio=50, bidir=False, formula=None, prov=""):
    return dict(slug=slug, discipline=disc, source_slug=src, target_slug=tgt,
                effect=effect, verdict=verdict, explanation=expl,
                confidence_basis=basis, priority=prio, bidirectional=bidir,
                formula_slug=formula, provenance=prov)


RELATIONSHIPS = [
    # ---- AERO -------------------------------------------------------------
    _rel("aero.downforce_vs_speed", "downforce", "speed", "depends", "depends",
         "More downforce raises cornering grip but its drag lowers straight-line "
         "speed and costs energy. It's a lap-time trade, track-dependent: downforce "
         "wins on tight autocross and can LOSE on a fast track. Resolve it in the "
         "lap sim with your real aero map.",
         disc="aerodynamics", prio=20,
         prov="F=1/2 rho V^2 C A: downforce and drag both scale with V^2"),
    _rel("aero.downforce_vs_cornering", "downforce", "cornerspeed", "increases",
         "true",
         "More downforce adds vertical load on the tyres, raising the lateral "
         "force they make, so corner speed goes up. Gains taper with tyre load "
         "sensitivity and cost drag, but the sign is positive.",
         disc="aerodynamics", basis="verified", prio=15, formula="aero_force",
         prov="downforce adds tyre normal load -> more lateral grip"),
    _rel("aero.downforce_vs_grip", "downforce", "grip", "increases", "true",
         "Downforce is extra vertical load, so the tyres can make more force — "
         "aero grip. Unlike mechanical grip it grows with speed (V^2), so it helps "
         "most in fast corners and barely at all at low speed.",
         disc="aerodynamics", basis="verified", prio=20),
    _rel("aero.downforce_vs_laptime", "downforce", "laptime", "depends", "depends",
         "Usually faster on an FSAE track (lots of slow/medium corners), but not "
         "free: drag hurts the straights and endurance energy. Net lap time depends "
         "on the circuit — confirm in the lap sim, don't assume.",
         disc="aerodynamics", prio=25),
    _rel("aero.drag_vs_speed", "drag", "speed", "decreases", "true",
         "Drag is the force resisting forward motion; more of it lowers top speed "
         "and acceleration for the same power. This is the cost side of downforce.",
         disc="aerodynamics", basis="verified", prio=20),
    _rel("aero.ridehgt_vs_downforce", "ride_height", "downforce", "depends",
         "depends",
         "Lowering the car usually raises underbody downforce and drops CG — both "
         "good — until you bottom out or stall the floor, where it reverses. There's "
         "an optimum; sweep it, don't just go lower.",
         disc="aerodynamics", prio=40),
    _rel("aero.ld_vs_laptime", "ld_ratio", "laptime", "increases", "true",
         "Higher L/D means more downforce for less drag — strictly good. Improving "
         "aero efficiency lowers lap time without the straight-line penalty.",
         disc="aerodynamics", basis="verified", prio=30),

    # ---- TYRES ------------------------------------------------------------
    _rel("tyre.width_vs_grip", "tyre_width", "grip", "depends", "depends",
         "Wider tyres often grip more, but NOT because of 'more contact area' "
         "(friction is roughly load-independent of area for a given pressure). They "
         "help by lowering pressure in the patch, running cooler, and load "
         "sensitivity — within limits, and they add weight and drag. Depends.",
         disc="suspension", prio=30),
    _rel("tyre.pressure_vs_grip", "tyre_pressure", "grip", "depends", "depends",
         "There's an optimum pressure. Too low overheats the shoulders and rolls "
         "the tyre; too high shrinks the patch and overheats the centre. More or "
         "less is only better relative to that optimum — find it on the skidpad with "
         "tyre temps.",
         disc="suspension", prio=25),
    _rel("tyre.temp_vs_grip", "tyre_temp", "grip", "depends", "depends",
         "Grip peaks in a temperature window. Below it the tyre is glassy; above it "
         "greases off. Getting heat in is good up to the window, bad past it — it's "
         "not 'hotter = grippier'.",
         disc="suspension", prio=30),
    _rel("tyre.grip_vs_cornering", "grip", "cornerspeed", "increases", "true",
         "More grip is more lateral force capacity, which directly raises the speed "
         "the car can hold through a corner. The cleanest positive in the sport.",
         disc="suspension", basis="verified", prio=20),
    _rel("tyre.grip_vs_laptime", "grip", "laptime", "increases", "true",
         "More grip helps every phase — braking, cornering, traction out — so it "
         "lowers lap time. It's the thing nearly every other change is ultimately "
         "chasing.",
         disc="suspension", basis="verified", prio=25),

    # ---- MASS -------------------------------------------------------------
    _rel("mass.vs_laptime", "mass", "laptime", "depends", "depends",
         "Lighter is ALMOST always faster — better accel, braking, cornering and "
         "energy use — but 'always' is the myth: removing mass that costs stiffness, "
         "reliability, or legality can lose more than it gains. Lighter is the right "
         "default, not an absolute law.",
         disc="shared", prio=20),
    _rel("mass.vs_accel", "mass", "accel", "decreases", "true",
         "a = F/m: for the same tractive force, more mass means less acceleration. "
         "Shedding mass is the most reliable way to accelerate harder.",
         disc="shared", basis="verified", prio=20, formula="accel_from_mass"),
    _rel("mass.vs_cornering", "mass", "cornerspeed", "decreases", "true",
         "More mass needs more lateral force for the same corner; tyre force doesn't "
         "scale up one-for-one with load (load sensitivity), so heavier corners "
         "slower. Lighter helps cornering.",
         disc="suspension", basis="verified", prio=25),
    _rel("mass.vs_braking", "mass", "brake_force", "depends", "depends",
         "A heavier car has more momentum to kill, so it stops over a longer "
         "distance even with the same tyres. Lighter brakes better — unless the "
         "weight removal cut your brake cooling or downforce.",
         disc="brakes", prio=30),
    _rel("mass.unsprung_vs_grip", "unsprung", "grip", "decreases", "true",
         "Lower unsprung mass lets the wheel follow bumps faster, keeping the tyre "
         "planted — so reducing it improves mechanical grip and is worth more per "
         "kg than sprung mass. A real, often-underrated win.",
         disc="suspension", basis="verified", prio=25),
    _rel("mass.cg_vs_cornering", "cg_height", "cornerspeed", "decreases", "true",
         "A lower CG cuts load transfer in corners, keeping the tyres more evenly "
         "loaded so they make more total grip. Lower CG → higher corner speed; it's "
         "one of the highest-value things you can chase.",
         disc="suspension", basis="verified", prio=20),

    # ---- SUSPENSION / GEOMETRY -------------------------------------------
    _rel("susp.spring_vs_grip", "spring_rate", "grip", "depends", "depends",
         "Stiffer is NOT automatically better. Stiffer springs cut body roll and "
         "sharpen response but follow bumps worse and can overload one tyre, losing "
         "mechanical grip. There's an optimum per track surface — 'stiffer = better' "
         "is a classic myth.",
         disc="suspension", prio=20),
    _rel("susp.spring_vs_handling", "spring_rate", "mech_grip", "depends", "depends",
         "Springs and bars are how you TUNE balance, not a one-way 'better' knob. "
         "Stiffer at one end pushes balance toward the other (stiffer front → more "
         "understeer). It's a balance tool; the right value depends on your target.",
         disc="suspension", prio=25),
    _rel("susp.arb_vs_roll", "arb", "roll", "decreases", "true",
         "A stiffer anti-roll bar resists body roll — that's its job. It also shifts "
         "lateral load transfer to that axle, which changes balance, so use it to "
         "trim handling, not just to stop roll.",
         disc="suspension", basis="verified", prio=25),
    _rel("susp.roll_vs_grip", "roll", "grip", "depends", "depends",
         "Some roll isn't inherently bad; what matters is the camber the tyre sees "
         "under roll. Too much roll wrecks camber and grip; killing all roll with "
         "huge stiffness hurts mechanical grip. Control it, don't eliminate it.",
         disc="suspension", prio=35),
    _rel("susp.camber_vs_cornering", "camber", "cornerspeed", "depends", "depends",
         "Negative camber helps the OUTSIDE tyre stay flat under roll, raising "
         "cornering grip — up to an optimum. Too much ruins straight-line braking "
         "and traction and overheats the inner edge. Tune to tyre temps, don't just "
         "add more.",
         disc="suspension", prio=25),
    _rel("susp.camber_vs_grip", "camber", "grip", "depends", "depends",
         "Static camber trades straight-line grip for cornering grip. The best value "
         "is whatever keeps the tyre flattest when it's loaded — found from tyre "
         "temps across the tread, not from a rule of thumb.",
         disc="suspension", prio=30),
    _rel("susp.track_vs_roll", "track_width", "roll", "decreases", "true",
         "A wider track increases the roll moment arm resisting body roll and cuts "
         "lateral load transfer, so the car rolls less and keeps tyres better loaded. "
         "Limited by rules and packaging.",
         disc="suspension", basis="verified", prio=30),
    _rel("susp.wheelbase_vs_handling", "wheelbase", "mech_grip", "depends", "depends",
         "A longer wheelbase calms the car (more stable, more understeer-ish, slower "
         "to change direction); shorter makes it nimble but edgy. Neither is 'better' "
         "— it's a stability-vs-agility choice for your driver and course.",
         disc="suspension", prio=35),
    _rel("susp.damping_vs_grip", "damping", "grip", "depends", "depends",
         "Dampers control how fast load changes, not steady grip. Right damping keeps "
         "the tyre planted over bumps and kerbs; too stiff skips, too soft wallows. "
         "It's a tuning optimum, not a 'more = better'.",
         disc="suspension", prio=35),

    # ---- POWERTRAIN -------------------------------------------------------
    _rel("pt.power_vs_accel", "power", "accel", "depends", "depends",
         "More power only accelerates you harder if the tyres can put it down. Past "
         "the traction limit it just spins the wheels — so on a low-grip FSAE launch, "
         "extra power often does nothing without more grip or traction control. "
         "Depends on whether you're traction- or power-limited.",
         disc="powertrain", prio=20),
    _rel("pt.power_vs_topspeed", "power", "speed", "increases", "true",
         "At the top end you're power/drag-limited, not traction-limited, so more "
         "power does raise top speed — but FSAE tracks rarely reach top speed, so it "
         "buys little lap time. True, but low-value.",
         disc="powertrain", basis="verified", prio=30),
    _rel("pt.power_vs_laptime", "power", "laptime", "depends", "depends",
         "More power helps only where you're power-limited (short bursts between slow "
         "corners). On a tight, traction-limited autocross, grip and mass usually buy "
         "more lap time than power. Check the lap sim before chasing kW.",
         disc="powertrain", prio=25),
    _rel("pt.torque_vs_traction", "torque", "traction_limit", "depends", "depends",
         "Wheel torque beyond what the tyres can hold just lights up the wheels. "
         "More torque helps only up to the traction limit; past it you need grip, "
         "weight transfer, or torque control, not more torque.",
         disc="powertrain", prio=25),
    _rel("pt.voltage_vs_power", "voltage", "power", "depends", "depends",
         "Higher pack voltage raises the power CEILING (P=VI, less current for the "
         "same power → less loss), but it doesn't add power by itself — the motor, "
         "inverter limits and the 80 kW rule still cap you. Enables power, isn't "
         "power.",
         disc="electrics", prio=30),
    _rel("pt.gear_vs_accel", "gear_ratio", "accel", "depends", "depends",
         "Shorter gearing multiplies wheel torque for stronger acceleration but caps "
         "top speed and can exceed the traction limit off the line. It's an optimum "
         "for the course's speed range, not 'shorter = faster'.",
         disc="powertrain", prio=30),
    _rel("pt.regen_vs_range", "regen", "range_energy", "increases", "true",
         "Regen recovers braking energy back into the pack, so it stretches "
         "endurance range / lowers energy per lap. Limited by how hard you can regen "
         "without upsetting brake balance, but the sign is positive.",
         disc="electrics", basis="verified", prio=30),

    # ---- BRAKES -----------------------------------------------------------
    _rel("brk.rotor_vs_stopping", "rotor_size", "brake_force", "depends", "depends",
         "A bigger rotor does NOT directly stop you faster — peak braking is limited "
         "by TYRE grip, not the brakes (you can already lock the wheels). A bigger "
         "rotor helps THERMAL capacity (less fade over a stint) and pedal feel, not "
         "single-stop distance. Classic myth.",
         disc="brakes", prio=20),
    _rel("brk.rotor_vs_speed", "rotor_size", "speed", "none", "myth",
         "A bigger brake rotor does not make the car stop in a shorter distance — "
         "you're already grip-limited and can lock the wheels with the brakes you "
         "have. Bigger rotors buy thermal capacity (less fade), not stopping "
         "distance. The 'bigger rotor stops faster' belief is a myth.",
         disc="brakes", basis="verified", prio=18),
    _rel("brk.rotor_vs_fade", "rotor_size", "brake_temp", "decreases", "true",
         "More rotor mass and swept area soak and shed more heat, so bigger rotors "
         "lower brake temperature and fade over an endurance run. This — not stopping "
         "power — is the real reason to size up.",
         disc="brakes", basis="verified", prio=22),
    _rel("brk.rotor_vs_temp", "rotor_size", "brake_temp", "decreases", "true",
         "More rotor mass and swept area soak and shed more heat, so a bigger rotor "
         "lowers brake temperature and fade over an endurance run. This — not stopping "
         "power — is the real reason to size up.",
         disc="brakes", basis="verified", prio=25),
    _rel("brk.brakeforce_vs_grip", "brake_force", "grip", "depends", "depends",
         "You can only brake as hard as the tyres allow; beyond that you lock up and "
         "stop slower. So 'more braking force' past the grip limit is counter-"
         "productive — the limit is grip, weight transfer and bias.",
         disc="brakes", prio=30),
    _rel("brk.temp_vs_braking", "brake_temp", "brake_force", "depends", "depends",
         "Pads need heat to work, but past their window they fade and lose bite. "
         "Hotter is better up to the operating range, worse beyond it — manage "
         "temperature, don't maximise it.",
         disc="brakes", prio=35),

    # ---- CHASSIS ----------------------------------------------------------
    _rel("ch.stiffness_vs_handling", "chassis_stiff", "mech_grip", "depends",
         "depends",
         "You want the chassis stiff ENOUGH that the suspension does the compliance, "
         "not the frame — past that point more stiffness adds weight for almost no "
         "handling gain. 'Stiffer chassis = better' is true only up to 'stiff enough'; "
         "then it's diminishing returns.",
         disc="chassis", prio=25),
    _rel("ch.stiffness_vs_mass", "chassis_stiff", "mass", "increases", "depends",
         "More chassis stiffness usually costs weight (more tubes/material). The art "
         "is hitting the stiffness target at minimum mass — so chasing stiffness "
         "blindly can make the car heavier and slower overall.",
         disc="chassis", prio=35),
    _rel("ch.fos_vs_mass", "fos", "mass", "increases", "depends",
         "A higher factor of safety usually means more material, which means more "
         "mass. The goal is adequate FoS (≥ your target) at minimum weight — over-"
         "building 'to be safe' is how brackets get heavy. Right-size it.",
         disc="chassis", prio=30),
    _rel("ch.wall_vs_strength", "part_mass", "strength", "increases", "true",
         "Thicker walls / more material do make a part stronger — that part is true. "
         "The catch is they also add mass, and past your FoS target that strength is "
         "wasted weight. Right-size to the FoS target in FEA; stronger-than-needed is "
         "just heavier.",
         disc="chassis", basis="verified", prio=30),
    _rel("ch.fos_vs_perf", "fos", "performance", "depends", "depends",
         "A higher factor of safety is safer but usually heavier, and past your "
         "target it's just dead weight that slows the car. 'Higher FoS is better' is "
         "a myth — adequate FoS at minimum mass is the goal. Right-size to your "
         "target, validated in FEA.",
         disc="chassis", prio=30),
    _rel("ch.stiff_vs_perf", "chassis_stiff", "performance", "depends", "depends",
         "Stiffen the chassis until the suspension does the compliance, then stop — "
         "more stiffness past 'stiff enough' just adds weight for no handling gain. "
         "Better up to a point, then counter-productive.",
         disc="chassis", prio=30),
]


# =========================================================================== #
#  FALLBACK LAWS — coarse physics for pairs with no specific edge             #
# =========================================================================== #
# Keyed by entity `kind`. Direction-tolerant in the engine, so order is loose.
FALLBACK_LAWS = [
    dict(slug="force_vs_speed_v2", source_kind="force", target_kind="speed",
         effect="increases", verdict="depends", formula_slug="speed_force_ratio",
         explanation=(
             "Aerodynamic and many resistive forces scale with the SQUARE of speed "
             "(F = 1/2 rho V^2 C A): double the speed → 4x the force. Evaluate this "
             "at the speeds the track actually spends time at, from the lap sim.")),
    dict(slug="mass_vs_speed", source_kind="mass", target_kind="speed",
         effect="decreases", verdict="depends",
         explanation=(
             "More mass means more inertia, so for a given force you accelerate and "
             "stop more slowly (a = F/m). Lighter is the right default for speed — but "
             "verify the weight you'd remove isn't doing a structural job.")),
    dict(slug="grip_vs_speed", source_kind="grip", target_kind="speed",
         effect="increases", verdict="true",
         explanation=(
             "More grip lets the car carry more speed through corners and brake later, "
             "which lowers lap time. Grip is what most setup work is ultimately "
             "chasing.")),
    dict(slug="stiffness_generic", source_kind="stiffness", target_kind="grip",
         effect="depends", verdict="depends",
         explanation=(
             "Stiffness (springs, bars, dampers, chassis) is a TUNING quantity with an "
             "optimum, not a 'more = better' knob. Too little and the car wallows or "
             "flexes; too much and the tyres skip and lose mechanical grip. Find the "
             "optimum on track, don't assume stiffer wins.")),
    dict(slug="mass_generic", source_kind="mass", target_kind="grip",
         effect="decreases", verdict="depends",
         explanation=(
             "Tyre force grows less than proportionally with load (load sensitivity), "
             "so adding mass rarely 'buys' grip and usually hurts the grip-to-weight "
             "that sets lap time. Lighter is the safe default — but check what the "
             "mass was doing first.")),
    dict(slug="angle_generic", source_kind="angle", target_kind="grip",
         effect="depends", verdict="depends",
         explanation=(
             "Geometry angles (camber, toe, caster) trade one operating condition for "
             "another and have an optimum set from tyre temperatures and data — not a "
             "single 'more is better' direction. Tune them to the tyre, not to a rule "
             "of thumb.")),
    dict(slug="thermal_generic", source_kind="thermal", target_kind="grip",
         effect="depends", verdict="depends",
         explanation=(
             "Tyres and brakes both have an operating-temperature WINDOW: too cold and "
             "they don't work, too hot and they fade or grease off. Hotter helps only "
             "up to the window. Manage temperature into the window, don't maximise "
             "it.")),
    dict(slug="stiffness_vs_perf", source_kind="stiffness", target_kind="performance",
         effect="depends", verdict="depends",
         explanation=(
             "Stiffness (springs, bars, dampers, chassis) has an OPTIMUM, not a "
             "'more = better' direction. 'Stiffer is always better' is one of the most "
             "common FSAE myths — past the optimum the tyres skip and you lose grip. "
             "Tune to the track surface and data.")),
    dict(slug="mass_vs_perf", source_kind="mass", target_kind="performance",
         effect="decreases", verdict="depends",
         explanation=(
             "Lighter is the right default — it helps accel, braking, cornering and "
             "energy — but 'always faster' is the myth: don't remove mass that's "
             "holding stiffness, reliability or legality. Lighter, where it's free.")),
    dict(slug="angle_vs_perf", source_kind="angle", target_kind="performance",
         effect="depends", verdict="depends",
         explanation=(
             "Geometry angles (camber, toe, caster) and body roll have an optimum set "
             "from tyre temperatures and data, not a single 'more is better' "
             "direction. Tune to the tyre, don't chase extremes.")),
    dict(slug="thermal_vs_perf", source_kind="thermal", target_kind="performance",
         effect="depends", verdict="depends",
         explanation=(
             "Tyres and brakes work in a temperature WINDOW — too cold or too hot both "
             "lose performance. Hotter helps only up to the window; manage temperature "
             "into it rather than maximising it.")),
    dict(slug="length_vs_perf", source_kind="length", target_kind="performance",
         effect="depends", verdict="depends",
         explanation=(
             "Geometry lengths (track, wheelbase, ride height, CG height) trade one "
             "behaviour for another and have an optimum for your course and driver — "
             "not a blanket 'more/less is better'. Check it against the lap sim.")),
    dict(slug="grip_vs_perf", source_kind="grip", target_kind="performance",
         effect="increases", verdict="true",
         explanation=(
             "More grip (mechanical or aero) lowers lap time in nearly every phase — "
             "braking, cornering, traction. It's the thing most setup work is chasing, "
             "so the sign is positive.")),
    dict(slug="force_vs_perf", source_kind="force", target_kind="performance",
         effect="depends", verdict="depends",
         explanation=(
             "Whether more of a force helps depends on what limits you — tyre grip, "
             "power, or thermal capacity. Past the limiting factor, more force does "
             "nothing or backfires. Identify the actual limit first.")),
    dict(slug="power_vs_perf", source_kind="power", target_kind="performance",
         effect="depends", verdict="depends",
         explanation=(
             "More power helps only where you're power-limited. On tight, traction-"
             "limited FSAE courses, grip and mass usually buy more lap time than kW. "
             "Confirm which limit you're against in the lap sim.")),
]


# convenience: kinds that mean "this is an overall-good/bad question"
PERFORMANCE_CUES = ("better", "worse", "best", "good", "improve", "improves",
                    "improved", "always", "faster", "competitive", "worth it")
