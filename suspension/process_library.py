# ============================================================================
#  KinematiK — Manufacturing Process Library
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
process_library — a shared, editable Excel/CSV knowledge base that points an
engineer at *how a part is typically made* the moment they type in what they're
working on.

The problem this solves
-----------------------
A new member joins, gets handed an upright / a wing mount / an accumulator
segment, and has no idea how that class of part is normally manufactured. Today
they ask the same three veterans, or they Google blindly. Either way the
team's hard-won "this is how we actually make this" knowledge lives in a few
heads and walks out the door at graduation.

This module backs a feature that lives inside every subsystem tab (except
Cost/BOM): a single search box. You type the component or process you're
working on ("upright", "wishbone bonding", "carbon layup", "busbar") and it
filters a curated, growing library of articles down to the relevant rows —
each with a one-line summary so you can tell at a glance whether it's worth
clicking, plus the subsystem tag so electrics / brakes / suspension etc. each
surface their own pool.

Storage model
-------------
One workbook, ``process_library.xlsx`` (with a ``process_library.csv`` mirror),
lives next to the project file in the app's working directory so the whole
team reads and writes the same shared list. On first run we seed it from
``SEED_ROWS`` below. Anyone can append a row from the UI; appends are written
straight back to disk so the next person who loads sees them.

Columns
-------
``Component``   — the part/feature the article is about (e.g. "Upright").
``Process``     — the manufacturing process(es) covered (e.g. "5-axis CNC").
``Subsystem``   — one of the canonical subsystem keys, for pool filtering.
``Summary``     — one line on what the article gives you and why it's useful.
``Link``        — URL to the real resource.
``Tags``        — free extra keywords to widen search (optional).
``AddedBy``     — who added it (optional, for appended rows).
"""

from __future__ import annotations

import os
import datetime as _dt

import pandas as pd

# Canonical column order for the workbook.
COLUMNS = ["Component", "Process", "Subsystem", "Summary", "Link", "Tags", "AddedBy"]

# Subsystem keys mirror the app's role/subsystem vocabulary so each tab can ask
# for "its" pool. ``general`` rows show up in every subsystem's search.
SUBSYSTEMS = [
    "suspension", "aero", "powertrain", "electrics",
    "brakes", "chassis", "cooling", "dataacq", "general",
]

# Friendly labels for the picker / captions.
SUBSYSTEM_LABELS = {
    "suspension": "Suspension / Dynamics",
    "aero":       "Aerodynamics",
    "powertrain": "Powertrain / Drivetrain",
    "electrics":  "Electrics",
    "brakes":     "Brakes",
    "chassis":    "Chassis / Frame",
    "cooling":    "Cooling",
    "dataacq":    "Data Acquisition",
    "general":    "General / Cross-team",
}


def _r(component, process, subsystem, summary, link, tags=""):
    return {
        "Component": component, "Process": process, "Subsystem": subsystem,
        "Summary": summary, "Link": link, "Tags": tags, "AddedBy": "seed",
    }


# ---------------------------------------------------------------------------
#  SEED LIBRARY
#  Curated, real, public resources keyed to the components KinematiK teams are
#  designing. Summaries are written to be skimmable — they say what you'll get.
# ---------------------------------------------------------------------------
SEED_ROWS = [
    # ---------------------------- SUSPENSION ------------------------------ #
    _r("Upright / Knuckle", "5-axis CNC machining (billet aluminium)", "suspension",
       "Covers how FSAE teams machine a billet 7075 upright in one or two set-ups, including datum strategy and bearing-bore tolerancing. If you're making an upright, this shows you how to fixture it and which features to hold tight so the bearings and hardpoints end up where the geometry needs them.",
       "https://www.fsae.com/forums/archive/index.php/t-7845.html",
       "knuckle, hub carrier, billet, 7075, bearing bore"),
    _r("Upright / Knuckle", "Topology optimisation for machined / cast parts", "suspension",
       "Walks through topology-optimising an upright and turning the raw result into a shape a CNC can actually cut, rather than an un-machinable blob. Useful if you've been handed an optimised concept and need to make it light without it becoming impossible (or hugely expensive) to manufacture.",
       "https://www.altair.com/resource/topology-optimization-for-additive-and-subtractive-manufacturing",
       "lightweighting, optiStruct, design for manufacture"),
    _r("A-arm / Wishbone", "Steel tube mitre, jig & TIG welding", "suspension",
       "Explains coping (fish-mouthing) 4130 tube, building a welding jig, and the weld sequence that controls distortion on a wishbone. Read this before you weld your first control arm so the finished length and angles match the CAD instead of pulling out of tolerance.",
       "https://www.fsae.com/forums/archive/index.php/t-8060.html",
       "control arm, 4130, chromoly, fishmouth, tube notching, jig"),
    _r("A-arm / Wishbone", "Bonded carbon tube + aluminium insert", "suspension",
       "Covers bonding CFRP suspension tubes into aluminium clevis inserts: adhesive choice, bond length, surface prep and pull-testing. Go here if your team is going composite on the arms and you need the bonded joint to actually hold the loads it sees on track.",
       "https://www.compositesworld.com/articles/fabrication-methods",
       "carbon, adhesive bonding, insert, clevis, CFRP rod end"),
    _r("Rod end / Spherical bearing", "Selection, press-fit & staking", "suspension",
       "Explains how to size rod ends and spherical bearings for suspension links, including press-fit housings, staking, and avoiding pull-out. Use it when you're specifying or installing the joints at the ends of your links so they don't loosen or fail under load.",
       "https://www.aurorabearing.com/technical-resources.html",
       "heim joint, monoball, spherical, misalignment, press fit"),
    _r("Pushrod / Pullrod", "Tube + threaded clevis machining", "suspension",
       "Shows how to make a length-adjustable pushrod: turning the threaded ends, left/right thread for in-situ adjustment, and a buckling check on the tube. Relevant if you're fabricating the actuation rods and need them adjustable at the car without buckling under compression.",
       "https://www.fsae.com/forums/archive/index.php/t-10618.html",
       "actuation, buckling, adjuster, turnbuckle"),
    _r("Rocker / Bellcrank", "Waterjet + CNC, double-shear mounting", "suspension",
       "Covers making a suspension rocker from plate — waterjet blank, CNC the bearing pockets, and a double-shear mount for stiffness. Helpful when you're manufacturing the bellcrank and want the bearings located accurately and the pivot stiff enough not to flex away your motion ratio.",
       "https://www.fsae.com/forums/archive/index.php/t-7281.html",
       "bellcrank, motion ratio, double shear, bearing pocket"),
    _r("Hub", "Turning, splines & wheel-stud press", "suspension",
       "Explains hub manufacturing: turning the bearing journals, cutting drive splines, and pressing wheel studs with the right interference fit. Read it before you make a hub so the bearing fits, stud retention, and runout all come out right the first time.",
       "https://www.fsae.com/forums/printthread.php?t=12169&pp=10",
       "wheel bearing, stud, spline, journal, interference"),
    _r("Coilover / Damper mount", "Sheet bracket forming & welding", "suspension",
       "Covers folding sheet-metal damper mounts, weld prep, and gusseting to react the high shock loads into the chassis. Use it when you're fabricating the shock mounts so they don't crack or tear out where the damper feeds load into the frame.",
       "https://www.fsae.com/forums/archive/index.php/t-11844.html",
       "shock mount, gusset, sheet metal, bracket"),
    _r("Anti-roll bar", "Blade ARB machining & heat treat", "suspension",
       "Explains making an adjustable blade-type anti-roll bar: spring-steel selection, machining the blades, and heat-treating for fatigue life. Relevant if you're building the ARB and need the blades to survive repeated cycling without fatiguing or taking a set.",
       "https://www.fsae.com/forums/archive/index.php/t-7552.html",
       "sway bar, roll stiffness, blade, spring steel, fatigue"),

    # ------------------------------- AERO --------------------------------- #
    _r("Wing element", "Wet layup / prepreg CFRP over a plug", "aero",
       "Takes you end-to-end on an aero element: CNC the foam plug, pull a mould, then wet-layup or prepreg the skins over a foam or honeycomb core. This is your roadmap if you've been assigned a wing element and have never taken a part from plug to finished skin before.",
       "https://www.compositesworld.com/topics/composites-basics",
       "mould, tooling, prepreg, wet layup, foam core, skin"),
    _r("Wing element", "5-axis foam-core machining", "aero",
       "Covers machining accurate aerofoil cores from high-density foam for direct skinning, with toolpath and surface-finish tips. Useful when you're cutting the core yourself and need the profile accurate and the surface clean enough to skin without chasing the shape afterwards.",
       "https://www.compositesworld.com/topics/processes",
       "foam core, aerofoil, toolpath, surface finish"),
    _r("Endplate / Mount", "Carbon plate cutting & bonded brackets", "aero",
       "Explains cutting flat carbon endplates without delamination and bonding on aluminium brackets to feed wing loads into the nose or chassis. Go here when you're making endplates and mounts and want clean edges plus joints that won't peel under aero load.",
       "https://www.compositesworld.com/topics/processes",
       "endplate, delamination, routing, bonded bracket"),
    _r("Undertray / Diffuser", "Large-panel composite layup", "aero",
       "Covers laying up a one-piece undertray/diffuser: mould strategy, core selection for stiffness-to-weight, and mounting that survives curb strikes. Relevant if you're building the floor and need a big, stiff, light panel that doesn't crack the first time it touches a kerb.",
       "https://www.compositesworld.com/articles/fabrication-methods-2015",
       "floor, diffuser, ground effect, panel, core"),
    _r("Nose cone", "Mould + impact-attenuator integration", "aero",
       "Shows how to build a composite nose and integrate the impact attenuator so the structure passes the FSAE IA test while keeping its aero shape. Read it if you own the nose, because the IA requirements drive how you lay it up, not just the outer surface.",
       "https://www.fsae.com/forums/archive/index.php/t-11260.html",
       "nose, impact attenuator, IA, crash structure"),

    # ---------------------------- POWERTRAIN ------------------------------ #
    _r("Drive shaft / Halfshaft", "Splined shaft turning & heat treat", "powertrain",
       "Covers manufacturing a halfshaft: turning it, cutting splines or tripod lobes, and heat-treating 4340 for torque and fatigue. Use it when you're making the driveshafts so they transmit full torque repeatedly without twisting up or failing at the splines.",
       "https://www.fsae.com/forums/archive/index.php/t-10618.html",
       "axle, tripod, CV, spline, 4340, torque"),
    _r("Sprocket / Chain drive", "Waterjet + CNC, hard-anodise", "powertrain",
       "Explains making a lightweight rear sprocket: waterjet the profile, CNC the bore and bolt pattern, and hard-anodise the teeth for wear. Relevant if you're cutting the final-drive sprocket and want it light but still able to survive a season of chain wear.",
       "https://www.fsae.com/forums/archive/index.php/t-5804.html",
       "final drive, chain, anodise, bolt pattern"),
    _r("Diff mount / Carrier", "Machined carrier + bearing fit", "powertrain",
       "Covers designing and machining a differential carrier with correct bearing fits and adjustability for chain tension and alignment. Go here when you're making the diff mounts and need the bearings to seat properly while still letting you tension the chain.",
       "https://www.fsae.com/forums/archive/index.php/t-7732.html",
       "differential, LSD, carrier, bearing, chain tension"),
    _r("Motor mount (EV)", "Machined plate + dowel location", "powertrain",
       "Explains mounting an EV traction motor: locating with dowels, reacting the reaction torque, and keeping the gearset concentric. Useful if you're fabricating the motor mount and need the motor and gears to stay aligned under full drive torque.",
       "https://www.fsae.com/forums/archive/index.php/t-12232.html",
       "traction motor, gearbox, concentricity, reaction torque"),
    _r("Gearset", "Gear cutting, lapping & inspection", "powertrain",
       "Covers specifying and sourcing a single-stage reduction gearset, with notes on tooth profile, hardness, lapping and inspection on receipt. Relevant when you're ordering or checking gears and need to know what to ask for and how to verify what shows up.",
       "https://khkgears.net/new/gear_knowledge/gear_technical_reference.html",
       "reduction, hobbing, lapping, gear ratio, hardness"),

    # ----------------------------- ELECTRICS ------------------------------ #
    _r("Accumulator segment", "Cell tabbing & spot/ultrasonic welding", "electrics",
       "Covers building a battery segment: bus design, spot vs ultrasonic vs laser tab welding, per-cell fusing, and weld pull-testing. This is essential reading before you weld a single cell, because a bad tab weld is both a reliability and a safety problem on a high-voltage pack.",
       "https://www.fsae.com/forums/archive/index.php/t-11455.html",
       "battery, cell, tab weld, busbar, fuse, segment, HV"),
    _r("Busbar", "Copper laser/waterjet cut + tin plating", "electrics",
       "Explains fabricating HV busbars: cutting copper without burrs, sizing for current and temperature rise, and tin-plating the contact faces. Use it when you're making busbars so they carry the pack current without overheating or arcing at the joints.",
       "https://www.protospacemfg.com/blog/post/custom-copper-bus-bars-guide/",
       "copper, current capacity, plating, HV, interconnect"),
    _r("Accumulator container", "Sheet aluminium fold + rivet/weld", "electrics",
       "Covers making the accumulator container to the rules: folding aluminium, sealing, and the firewall and clearance requirements for HV enclosures. Read it before building the box so it passes tech inspection and actually protects and isolates the pack.",
       "https://www.fsae.com/forums/archive/index.php/t-11588.html",
       "enclosure, firewall, sealing, HV container, rules"),
    _r("Wiring harness", "Layout, crimp & strain relief", "electrics",
       "Explains building a motorsport harness: pinout planning, proper crimping (not soldering), concentric twisting, and strain relief. Relevant whenever you're making a loom, because most electrical gremlins on a race car trace back to bad crimps and poor strain relief.",
       "https://www.fsae.com/forums/archive/index.php/t-11882.html",
       "loom, crimp, connector, DTM, strain relief, vibration"),
    _r("Cooling plate (cells)", "CNC / friction-stir-welded cold plate", "electrics",
       "Covers making a liquid cold plate for the accumulator: machined channels vs friction-stir-welded lids, sealing, and flow vs pressure-drop. Useful if you're building pack cooling and need a leak-free plate that actually pulls heat out of the cells.",
       "https://www.eaton.com/us/en-us/products/thermal-management-solutions/cold-plate-heat-exchanger/custom-liquid-cold-plate-design.html",
       "cold plate, FSW, channel, coolant, thermal"),

    # ------------------------------ BRAKES -------------------------------- #
    _r("Brake rotor / Disc", "Laser/waterjet + grinding, slotting", "brakes",
       "Covers making a floating brake rotor: cutting the friction ring, grinding the faces parallel, slotting/drilling for cooling, and the float bobbin design. Read it before you make a rotor so it runs true, sheds heat, and floats without rattling or seizing.",
       "https://www.fsae.com/forums/archive/index.php/t-7905.html",
       "disc, floating, bobbin, friction ring, cooling slots"),
    _r("Caliper bracket", "Machined / waterjet double-shear mount", "brakes",
       "Explains designing and machining a caliper bracket in double shear, aligned to the rotor and stiff enough to avoid knock-back. Use it when you're making the bracket so the pads sit square on the disc and the pedal stays firm.",
       "https://www.fsae.com/forums/archive/index.php/t-7845.html",
       "caliper, bracket, double shear, knock-back, alignment"),
    _r("Brake pedal", "Machined / fabricated pedal + bias bar", "brakes",
       "Covers building a brake pedal assembly with a balance (bias) bar, including pivot bearing choice and FEA of the lever for the rules load case. Relevant if you're making the pedal, which must survive the panic-stop load test and let you tune front/rear bias.",
       "https://www.fsae.com/forums/archive/index.php/t-11548.html",
       "pedal box, bias bar, balance bar, master cylinder, FEA"),
    _r("Brake lines", "Hard-line bending & flaring + braided hose", "brakes",
       "Explains routing and making brake lines: bending and 37° flaring hard line, terminating braided PTFE hose, and bleed-friendly routing. Use it when you're plumbing the brakes so the joints don't leak and you can actually get the air out.",
       "https://www.fsae.com/forums/archive/index.php/t-7552.html",
       "hard line, flare, AN fitting, braided hose, bleeding"),

    # ------------------------------ CHASSIS ------------------------------- #
    _r("Spaceframe chassis", "Tube mitre, jig & TIG weld sequence", "chassis",
       "Covers building a steel spaceframe: tacking on a flat table or jig, weld sequence to control pull, and node design that meets the structural rules. Read it before welding the frame so it comes out straight, in tolerance, and passes the structural-equivalency requirements.",
       "https://www.fsae.com/forums/archive/index.php/t-11844.html",
       "frame, 4130, node, jig, distortion, weld sequence, SES"),
    _r("Monocoque", "Honeycomb sandwich layup & bonding", "chassis",
       "Explains laying up a carbon monocoque tub: core selection, the skin schedule for the structural-equivalency rules, and bonding/insert strategy. Relevant if you're building the tub and need it to meet the rules while giving you somewhere solid to mount hardware.",
       "https://www.fsae.com/forums/archive/index.php/t-11260.html",
       "tub, honeycomb, sandwich, insert, SEF, layup schedule"),
    _r("Suspension mount / Bracket", "Sheet bracket + weld to frame", "chassis",
       "Covers making weld-on suspension pickup brackets: laser blanks, fold lines, double-shear tabs, and locating them accurately on the jig. Use it when you're fabricating the pickups, because their position directly sets your suspension geometry on the real car.",
       "https://www.fsae.com/forums/archive/index.php/t-8060.html",
       "pickup, tab, bracket, double shear, jig fixture"),
    _r("Bulkhead / Firewall", "Sheet aluminium / composite panel", "chassis",
       "Explains making the firewall and front bulkhead to the rules: panel material, sealing the cockpit, and reacting harness and IA loads. Read it before you make these panels so they pass tech and properly separate the driver from the hazards behind them.",
       "https://www.fsae.com/forums/archive/index.php/t-7732.html",
       "firewall, bulkhead, panel, sealing, rules"),
    _r("Pedal box / Footwell", "Machined mounts + adjustable rails", "chassis",
       "Covers building an adjustable pedal box on rails so multiple drivers fit, with stiff mounting into the front bulkhead. Relevant if you're making the pedal box and need it to slide for driver fit without flexing under braking.",
       "https://www.fsae.com/forums/archive/index.php/t-11548.html",
       "footwell, adjustable, rails, driver fit, ergonomics"),

    # ------------------------------ COOLING ------------------------------- #
    _r("Radiator / Cooling duct", "Sheet/composite ducting fabrication", "cooling",
       "Covers making cooling ducts that actually feed the radiator core: sheet or composite ducting, sealing to the core, and avoiding recirculation. Use it when you're building the ducts so the air goes through the core instead of leaking around it.",
       "https://www.fsae.com/forums/archive/index.php/t-12214.html",
       "radiator, duct, shroud, core, airflow, sealing"),
    _r("Coolant lines / Manifold", "Hose, AN fittings & machined manifold", "cooling",
       "Explains plumbing the cooling loop: hose and AN fitting selection, machining a coolant manifold, and bleed points to purge air. Relevant if you're making the coolant plumbing and want a loop that seals, flows, and doesn't trap air pockets.",
       "https://www.fsae.com/forums/archive/index.php/t-7552.html",
       "hose, AN fitting, manifold, bleed, pump, loop"),

    # --------------------------- DATA ACQUISITION ------------------------- #
    _r("Sensor bracket", "3D-printed / machined sensor mounts", "dataacq",
       "Covers designing robust sensor mounts (wheel-speed, damper-pot, IMU) that survive vibration and keep their calibration. Use it when you're mounting sensors so your data stays trustworthy instead of drifting or dropping out over a session.",
       "https://www.fsae.com/forums/archive/index.php/t-12029.html",
       "sensor, mount, IMU, damper pot, wheel speed, vibration"),
    _r("Strain-gauge load cell", "Gauge bonding & calibration", "dataacq",
       "Explains turning a suspension link into a load cell: surface prep, strain-gauge bonding, half/full-bridge wiring, and bench calibration. Relevant if you're instrumenting a part to measure real loads and need the gauge bonded and calibrated correctly.",
       "https://www.vishaypg.com/docs/11092/tn5051.pdf",
       "strain gauge, load cell, bridge, calibration, bonding"),

    # ------------------------------ GENERAL ------------------------------- #
    _r("Any machined aluminium part", "DFM for CNC machining", "general",
       "Lays out design-for-manufacture rules for CNC parts: tool access, internal radii, wall thickness, and tolerances that keep machine time sane. Worth a read before you finalise any machined part, since small DFM choices decide whether it's quick and cheap or a nightmare to cut.",
       "https://www.protolabs.com/resources/design-for-machining-toolkit/",
       "DFM, CNC, tolerance, radius, wall thickness, machining"),
    _r("Any composite part", "Composites manufacturing primer", "general",
       "A from-scratch primer on composite fabrication: layup types, vacuum bagging, cure cycles, and how processing choices drive part quality. Start here if you've never made a composite part, before moving on to the part-specific aero or chassis guides.",
       "https://www.compositesworld.com/articles/fabrication-methods",
       "composite, layup, vacuum bag, cure, CFRP, primer"),
    _r("Any welded steel part", "TIG welding & distortion control", "general",
       "Covers TIG-welding thin steel tube and sheet for chassis/suspension work, with tacking patterns and fixturing to control distortion. Read it before any welding job so your parts come off the jig straight instead of warped out of tolerance.",
       "https://www.millerwelds.com/resources/article-library/welding-tips-the-secret-to-success-when-tig-welding",
       "TIG, GTAW, distortion, tacking, fixture, 4130"),
    _r("Any bonded joint", "Adhesive bonding best practice", "general",
       "Explains choosing and applying structural adhesives: surface prep, bond-line control, fillet design, and how to test a bond before trusting it. Useful for any glued joint, because surface prep and bond-line thickness matter far more than which glue you pick.",
       "https://www.compositesworld.com/articles/composites-materials-and-processes",
       "adhesive, bond line, surface prep, epoxy, fillet"),
    _r("Any sheet-metal part", "Bending, laser cutting & DFM", "general",
       "Covers sheet-metal DFM: bend radii, K-factor, relief cuts, and hole-to-edge distances so your flat pattern folds into the part you intended. Read it before sending a flat pattern out, so the folded part actually matches your model.",
       "https://www.protolabs.com/services/sheet-metal-fabrication/design-guidelines/",
       "sheet metal, bend, K-factor, laser, flat pattern, DFM"),
    _r("Any 3D-printed part", "FDM/SLS/SLA selection & DFAM", "general",
       "Helps you pick a 3D-printing process for a jig, duct, or bracket and follow design-for-additive rules so the part comes out usable. Relevant whenever you're printing something functional and want it to survive its job rather than warp or snap.",
       "https://www.hubs.com/knowledge-base/additive-manufacturing-process/",
       "3D print, FDM, SLS, SLA, DFAM, jig, prototype"),
    _r("Any fastened joint", "Bolted-joint preload & torque", "general",
       "Explains designing bolted joints properly: choosing grade, computing preload and torque, picking washers and locking, and why finger-tight kills fatigue life. Read it for any bolted connection, since most field failures come from joints that were never preloaded correctly.",
       "https://www.boltscience.com/pages/basics.htm",
       "bolt, preload, torque, fastener, locking, fatigue"),
]


# ---------------------------------------------------------------------------
#  File location & I/O
# ---------------------------------------------------------------------------
def default_xlsx_path():
    """Shared workbook path — sits next to project.json in the app's CWD."""
    return os.path.join(os.getcwd(), "process_library.xlsx")


def default_csv_path():
    return os.path.join(os.getcwd(), "process_library.csv")


def seed_dataframe():
    """A fresh DataFrame built from the curated seed rows."""
    df = pd.DataFrame(SEED_ROWS, columns=COLUMNS)
    return df


def _normalise(df):
    """Guarantee the expected columns exist and are strings, in order."""
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[COLUMNS].copy()
    for col in COLUMNS:
        df[col] = df[col].fillna("").astype(str)
    # Drop fully blank rows (e.g. a stray empty line in an edited CSV).
    mask = (df["Component"].str.strip() != "") | (df["Process"].str.strip() != "")
    return df[mask].reset_index(drop=True)


def write_library(df, xlsx_path=None, csv_path=None):
    """Persist the library to both .xlsx (primary) and .csv (mirror/fallback)."""
    df = _normalise(df)
    xlsx_path = xlsx_path or default_xlsx_path()
    csv_path = csv_path or default_csv_path()
    wrote_xlsx = False
    try:
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as xw:
            df.to_excel(xw, index=False, sheet_name="ProcessLibrary")
        wrote_xlsx = True
    except Exception:
        # openpyxl missing or path unwritable — CSV still gives us persistence.
        wrote_xlsx = False
    try:
        df.to_csv(csv_path, index=False)
    except Exception:
        pass
    return wrote_xlsx


def _apply_seed_links(df):
    """Overwrite any seed rows in df with the current canonical seed links.

    User-added rows (AddedBy != 'seed') are preserved untouched. This ensures
    stale persisted files can never display old / broken seed URLs.
    """
    seed_df = seed_dataframe()
    seed_key = seed_df[["Component", "Process"]].apply(
        lambda r: (r["Component"].strip().lower(), r["Process"].strip().lower()), axis=1
    )
    seed_map = {k: i for i, k in enumerate(seed_key)}

    def _update_row(row):
        key = (row["Component"].strip().lower(), row["Process"].strip().lower())
        if key in seed_map and str(row.get("AddedBy", "seed")).strip() == "seed":
            return seed_df.iloc[seed_map[key]]
        return row

    return _normalise(df.apply(_update_row, axis=1))


def load_library(xlsx_path=None, csv_path=None, seed_if_missing=True):
    """Load the shared library, seeding the file on first run.

    Order of preference: existing .xlsx, then existing .csv, then (if allowed)
    seed it from SEED_ROWS and write it out. Always returns a normalised
    DataFrame with the canonical columns.

    Seed rows are always refreshed from the current SEED_ROWS constants so
    stale persisted files can never display old or broken URLs.
    """
    xlsx_path = xlsx_path or default_xlsx_path()
    csv_path = csv_path or default_csv_path()

    if os.path.exists(xlsx_path):
        try:
            df = _normalise(pd.read_excel(xlsx_path, sheet_name="ProcessLibrary"))
            return _apply_seed_links(df)
        except Exception:
            try:
                df = _normalise(pd.read_excel(xlsx_path))
                return _apply_seed_links(df)
            except Exception:
                pass
    if os.path.exists(csv_path):
        try:
            df = _normalise(pd.read_csv(csv_path))
            return _apply_seed_links(df)
        except Exception:
            pass

    df = seed_dataframe()
    if seed_if_missing:
        write_library(df, xlsx_path, csv_path)
    return _normalise(df)


def append_row(component, process, subsystem, summary, link,
               tags="", added_by="", xlsx_path=None, csv_path=None):
    """Append one article and persist immediately. Returns the new DataFrame."""
    df = load_library(xlsx_path, csv_path)
    stamp = _dt.date.today().isoformat()
    who = (added_by or "").strip()
    new = {
        "Component": (component or "").strip(),
        "Process":   (process or "").strip(),
        "Subsystem": (subsystem or "general").strip().lower(),
        "Summary":   (summary or "").strip(),
        "Link":      (link or "").strip(),
        "Tags":      (tags or "").strip(),
        "AddedBy":   f"{who} ({stamp})" if who else stamp,
    }
    df = pd.concat([df, pd.DataFrame([new], columns=COLUMNS)], ignore_index=True)
    write_library(df, xlsx_path, csv_path)
    return _normalise(df)


# ---------------------------------------------------------------------------
#  Search / filter
# ---------------------------------------------------------------------------
def filter_library(df, query, subsystem=None, include_general=True):
    """Return rows relevant to ``query`` (and optionally a subsystem pool).

    Matching is a simple, forgiving token search across Component, Process,
    Summary and Tags: every whitespace-separated token in the query must appear
    somewhere in the row (case-insensitive substring). Results are ranked so
    Component/Process hits come before Tag/Summary-only hits.
    """
    df = _normalise(df)

    # Subsystem pool filter (general rows always included when asked).
    if subsystem:
        sub = subsystem.strip().lower()
        if include_general:
            df = df[df["Subsystem"].str.lower().isin([sub, "general"])]
        else:
            df = df[df["Subsystem"].str.lower() == sub]
        df = df.reset_index(drop=True)

    q = (query or "").strip().lower()
    if not q:
        return df

    tokens = [t for t in q.split() if t]
    hay_strong = (df["Component"].str.lower() + " " + df["Process"].str.lower())
    hay_all = (hay_strong + " " + df["Summary"].str.lower()
               + " " + df["Tags"].str.lower())

    def _row_matches(i):
        return all(tok in hay_all.iloc[i] for tok in tokens)

    keep = [i for i in range(len(df)) if _row_matches(i)]
    if not keep:
        return df.iloc[0:0]

    # Rank: rows where every token also appears in Component/Process score higher.
    def _score(i):
        strong = sum(1 for tok in tokens if tok in hay_strong.iloc[i])
        return -strong  # more strong hits => earlier
    keep.sort(key=_score)
    return df.iloc[keep].reset_index(drop=True)


# ---------------------------------------------------------------------------
#  Questionnaire support
#  Powers a tap-only "What are you developing?" flow per subsystem: we list the
#  actual components in that pool, and (once a component is chosen) the actual
#  processes the library has for it, so a new member can answer without knowing
#  any vocabulary and still land on relevant articles.
# ---------------------------------------------------------------------------
def components_for(df, subsystem_key, include_general=False):
    """Distinct component names available in a subsystem's pool, in order."""
    pool = filter_library(df, "", subsystem=subsystem_key,
                          include_general=include_general)
    out, seen = [], set()
    for i in range(len(pool)):
        c = str(pool.iloc[i]["Component"]).strip()
        if c and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out


def processes_for(df, subsystem_key, component, include_general=False):
    """Distinct processes the library lists for a given component in a pool."""
    pool = filter_library(df, "", subsystem=subsystem_key,
                          include_general=include_general)
    comp = (component or "").strip().lower()
    out, seen = [], set()
    for i in range(len(pool)):
        if str(pool.iloc[i]["Component"]).strip().lower() != comp:
            continue
        p = str(pool.iloc[i]["Process"]).strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def rows_for(df, subsystem_key, component, process=None, include_general=False):
    """The actual article rows matching a component (and optional process)."""
    pool = filter_library(df, "", subsystem=subsystem_key,
                          include_general=include_general)
    comp = (component or "").strip().lower()
    mask = pool["Component"].str.strip().str.lower() == comp
    sub = pool[mask]
    if process:
        proc = process.strip().lower()
        sub = sub[sub["Process"].str.strip().str.lower() == proc]
    return sub.reset_index(drop=True)
