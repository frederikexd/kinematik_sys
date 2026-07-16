# ============================================================================
#  KinematiK — mission-briefing concept visuals
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
# ============================================================================
"""Concept visuals for the mission briefing — pure Python, zero network.

One small figure per briefing tool id, built from simple but PHYSICALLY
CORRECT relations (linearised where noted), so a visual thinker — or someone
brand new to engineering — instantly sees WHY the tool matters before they
open it. Values are representative of a ~280 kg FSAE car + driver and every
caption says so: these illustrate the physics, they are not your car's
numbers (the tools compute those).

Public API:
    concept_figure(tab_id) -> (plotly Figure | None, caption str | None)

Never raises: any internal failure returns (None, None) so the briefing can
always render text-only.
"""

import math

import numpy as np
import plotly.graph_objects as go

# Representative FSAE-scale constants used across the illustrations.
_G = 9.81          # m/s²
_MASS = 280.0      # kg, car + driver
_TRACK = 1.20      # m
_WHEELBASE = 1.60  # m
_CG_H = 0.30       # m
_RHO = 1.225       # kg/m³ air density (sea level, 15 °C)

_ILLU = ("Illustrative physics with representative FSAE values — "
         "not your car's numbers; the tool computes those.")
_CONC = "Conceptual illustration."


def _base(fig, title, x_title="", y_title="", height=280):
    fig.update_layout(
        title=dict(text=title, font=dict(size=14)),
        xaxis_title=x_title, yaxis_title=y_title,
        height=height, margin=dict(l=10, r=10, t=42, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.0,
                    xanchor="right", x=1.0, font=dict(size=11)),
        hovermode="x unified",
    )
    return fig


# --------------------------------------------------------------------------- #
#  Per-tool builders
# --------------------------------------------------------------------------- #

def _fig_kinematics():
    # Linearised camber gain: dCamber/dz = 1/FVSA (rad per m of bump).
    z = np.linspace(-30.0, 30.0, 16)                       # mm of bump travel
    for fvsa_mm, name, dash in ((800.0, "Short FVSA (0.8 m) — aggressive", None),
                                (1600.0, "Long FVSA (1.6 m) — gentle", "dash")):
        camber = -(z / fvsa_mm) * (180.0 / math.pi)        # deg
        yield name, z, camber, dash


def _kinematics():
    fig = go.Figure()
    for name, z, camber, dash in _fig_kinematics():
        fig.add_trace(go.Scatter(x=z, y=camber, name=name,
                                 line=dict(dash=dash)))
    _base(fig, "Camber gain: geometry sets it, nothing else can fix it",
          "Bump travel (mm)", "Camber change (deg)")
    return fig, ("Two front-view swing-arm lengths, linearised (Δcamber = "
                 "travel ÷ FVSA). Halving FVSA doubles camber gain over the "
                 "same 30 mm of travel — a pure geometry decision. " + _ILLU)


def _roll():
    ay = np.linspace(0.0, 1.6, 17)                          # lateral accel, g
    fig = go.Figure()
    for h, name, dash in ((0.30, "CG height 300 mm", None),
                          (0.25, "CG height 250 mm", "dash")):
        dW = _MASS * ay * _G * h / _TRACK                   # N, total transfer
        fig.add_trace(go.Scatter(x=ay, y=dW, name=name, line=dict(dash=dash)))
    _base(fig, "Lateral load transfer: ΔW = m·a·h / t",
          "Lateral acceleration (g)", "Load transferred (N)")
    return fig, ("Exact rigid-car relation. 50 mm of CG height is ~170 N of "
                 "extra transfer at 1.5 g — grip the inside tyres lose and the "
                 "outside tyres only partly get back (tyre load sensitivity). "
                 + _ILLU)


def _compliance():
    F = np.linspace(0.0, 3000.0, 16)                        # N lateral at patch
    arm = 100.0                                             # mm steer arm
    fig = go.Figure()
    for k, name, dash in ((5000.0, "Stiff link (5 kN/mm)", None),
                          (800.0, "Compliant link (0.8 kN/mm)", "dash")):
        toe = np.degrees(np.arctan((F / k) / arm))          # deg
        fig.add_trace(go.Scatter(x=F, y=toe, name=name, line=dict(dash=dash)))
    _base(fig, "Compliance steer: δ = F/k, toe = atan(δ / arm)",
          "Lateral force at contact patch (N)", "Unwanted toe change (deg)")
    return fig, ("Hooke's law at the toe link. The compliant link steers the "
                 "wheel over 1° at 2 kN — the car steers itself, and no "
                 "geometry you drew survives it. " + _ILLU)


def _tire():
    alpha = np.linspace(-12.0, 12.0, 61)                   # slip angle, deg
    a = np.radians(alpha)
    B, C, D = 10.0, 1.6, 1.0                                # Pacejka magic
    mu = D * np.sin(C * np.arctan(B * a))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=alpha, y=mu, name="Normalised lateral grip"))
    pk = alpha[int(np.argmax(mu))]
    fig.add_vline(x=pk, line_dash="dot",
                  annotation_text=f"peak ≈ {pk:.0f}°", annotation_font_size=11)
    _base(fig, "Tyre grip vs slip angle (Pacejka magic formula)",
          "Slip angle (deg)", "Lateral force / vertical load  (μ)")
    return fig, ("The curve every handling number lives on: grip builds with "
                 "slip, peaks, then falls away — past the peak, more steering "
                 "gives LESS grip. " + _ILLU)


def _setup():
    f = np.linspace(0.30, 0.70, 21)     # front share of lateral load transfer
    s = 0.6                              # tyre load-sensitivity strength
    front = 1.0 - s * f ** 2             # normalised axle grip
    rear = 1.0 - s * (1.0 - f) ** 2
    bal = front - rear
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=f * 100, y=bal, name="Grip balance (F − R)"))
    fig.add_hline(y=0.0, line_dash="dot")
    fig.add_annotation(x=36, y=0.08, text="oversteer ↑", showarrow=False,
                       font=dict(size=11))
    fig.add_annotation(x=64, y=-0.08, text="understeer ↓", showarrow=False,
                       font=dict(size=11))
    _base(fig, "Springs & ARBs steer the balance via load-transfer split",
          "Front share of load transfer (%)", "Front − rear axle grip")
    return fig, ("Tyre load sensitivity (grip loss ∝ transfer²) means the "
                 "axle that carries more of the transfer gives up more grip — "
                 "that's the entire mechanism behind ARB tuning. " + _ILLU)


def _laptime():
    v = np.linspace(5.0, 35.0, 16)                          # m/s
    mu, cla = 1.5, 3.5
    fig = go.Figure()
    a_mech = np.full_like(v, mu)                            # g
    a_aero = mu * (1.0 + 0.5 * _RHO * cla * v ** 2 / (_MASS * _G))
    fig.add_trace(go.Scatter(x=v * 3.6, y=a_mech, name="Mechanical grip only",
                             line=dict(dash="dash")))
    fig.add_trace(go.Scatter(x=v * 3.6, y=a_aero, name="With downforce"))
    _base(fig, "Cornering envelope: a_max = μ·(g + ½ρ·CLA·v²/m)/g",
          "Speed (km/h)", "Max lateral acceleration (g)")
    return fig, ("The GGV idea in one line: downforce grows with v², so the "
                 "faster the corner, the more grip you have — lap simulation "
                 "integrates this envelope around a track. " + _ILLU)


def _aero():
    v = np.linspace(0.0, 35.0, 36)
    fig = go.Figure()
    for cla, name, dash in ((4.0, "CL·A = 4.0 (big wings)", None),
                            (2.5, "CL·A = 2.5 (modest)", "dash")):
        df = 0.5 * _RHO * cla * v ** 2
        fig.add_trace(go.Scatter(x=v * 3.6, y=df, name=name,
                                 line=dict(dash=dash)))
    fig.add_hline(y=_MASS * _G, line_dash="dot",
                  annotation_text="car weight", annotation_font_size=11)
    _base(fig, "Downforce = ½·ρ·v²·CL·A — it buys grip with speed²",
          "Speed (km/h)", "Downforce (N)")
    return fig, ("Exact aerodynamic relation. At autocross speeds a big "
                 "package adds a meaningful fraction of the car's weight in "
                 "free vertical load — pointed the right way. " + _ILLU)


def _ev():
    P_max, T_max = 80e3, 240.0                              # W, Nm
    w_base = P_max / T_max                                  # rad/s
    rpm = np.linspace(1.0, 6000.0, 100)
    w = rpm * 2 * math.pi / 60.0
    torque = np.where(w <= w_base, T_max, P_max / w)
    power = torque * w / 1000.0
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=rpm, y=torque, name="Torque (Nm)"))
    fig.add_trace(go.Scatter(x=rpm, y=power, name="Power (kW)", yaxis="y2"))
    _base(fig, "Motor map: constant torque, then constant power (P = τ·ω)",
          "Motor speed (rpm)", "Torque (Nm)")
    fig.update_layout(yaxis2=dict(title="Power (kW)", overlaying="y",
                                  side="right", showgrid=False))
    return fig, ("The exact trade every architecture study turns on: gearing "
                 "moves you along this curve, and where you sit decides "
                 "launch, top speed and the energy bill. " + _ILLU)


def _accum():
    t = np.linspace(0.0, 22.0, 23)                          # minutes
    m_pack, c_cell, R = 40.0, 1000.0, 0.05                  # kg, J/kgK, Ω
    fig = go.Figure()
    for i_amp, name, dash in ((120.0, "120 A average", None),
                              (80.0, "80 A average", "dash")):
        dT = (i_amp ** 2) * R * (t * 60.0) / (m_pack * c_cell)
        fig.add_trace(go.Scatter(x=t, y=dT, name=name, line=dict(dash=dash)))
    fig.add_hline(y=25.0, line_dash="dot",
                  annotation_text="typical cell ΔT limit",
                  annotation_font_size=11)
    _base(fig, "Pack self-heating, no cooling: ΔT = I²·R·t / (m·c)",
          "Endurance time (min)", "Pack temperature rise (K)")
    return fig, ("Joule heating, adiabatic worst case. Current enters "
                 "SQUARED — an endurance at 120 A makes 2.25× the heat of "
                 "80 A, which is why pack sizing and cooling are one "
                 "decision. " + _ILLU)


def _brakes():
    a = np.linspace(0.0, 1.5, 16)                           # decel, g
    lr, mu_fix = 0.80, 0.65
    ideal = (lr / _WHEELBASE) + a * (_CG_H / _WHEELBASE)    # front force share
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=a, y=ideal * 100, name="Ideal front bias"))
    fig.add_trace(go.Scatter(x=a, y=np.full_like(a, mu_fix * 100),
                             name="Fixed 65 % bias", line=dict(dash="dash")))
    _base(fig, "Braking weight transfer: ideal bias = lr/L + (a/g)·h/L",
          "Deceleration (g)", "Front share of braking force (%)")
    return fig, ("Exact rigid-car relation: braking throws weight forward, so "
                 "the bias the car NEEDS rises with deceleration. Where the "
                 "fixed line sits below ideal, the rears lock first — the "
                 "dangerous order. " + _ILLU)


def _pcb():
    # IPC-2221 external trace: I = 0.048 · ΔT^0.44 · A^0.725  (A in mil²)
    I = np.linspace(0.5, 12.0, 24)
    fig = go.Figure()
    for w_mm, name, dash in ((1.0, "1 mm trace, 1 oz Cu", "dash"),
                             (3.0, "3 mm trace, 1 oz Cu", None)):
        area_mil2 = (w_mm / 0.0254) * 1.37
        dT = (I / (0.048 * area_mil2 ** 0.725)) ** (1.0 / 0.44)
        fig.add_trace(go.Scatter(x=I, y=dT, name=name, line=dict(dash=dash)))
    fig.add_hline(y=30.0, line_dash="dot",
                  annotation_text="30 K rise — common design limit",
                  annotation_font_size=11)
    _base(fig, "Trace heating (IPC-2221): current capacity is brutal math",
          "Current (A)", "Trace temperature rise (K)")
    return fig, ("The industry-standard IPC-2221 relation, inverted for "
                 "temperature. A 1 mm trace that looks fine at 3 A is a fuse "
                 "at 8 A — this is the board-killer the tool hunts. " + _ILLU)


def _tractive():
    V0, Rc, C = 400.0, 1000.0, 1.0e-3                       # V, Ω, F
    t = np.linspace(0.0, 6.0, 61)
    v = V0 * (1.0 - np.exp(-t / (Rc * C)))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=t, y=v, name="TS bus voltage"))
    fig.add_hline(y=0.95 * V0, line_dash="dot",
                  annotation_text="95 % — rules threshold",
                  annotation_font_size=11)
    _base(fig, "Precharge: V(t) = V₀·(1 − e^(−t/RC))",
          "Time (s)", "Bus voltage (V)")
    return fig, ("Exact RC charging law. The rules require reaching ~95 % "
                 "before the AIRs close; your resistor choice sets both this "
                 "time AND how much heat that resistor must survive. " + _ILLU)


def _dfmea():
    sev = [9, 7, 4, 8, 3, 6]
    occ = [3, 6, 7, 2, 4, 5]
    lab = ["HV isolation loss", "Radiator airflow", "Sensor dropout",
           "Upright bolt", "Telemetry", "Chain tension"]
    rpn = [s * o for s, o in zip(sev, occ)]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=occ, y=sev, mode="markers+text", text=lab, textposition="top center",
        textfont=dict(size=10),
        marker=dict(size=[r / 2.2 + 8 for r in rpn]),
        name="Failure modes (size = risk)"))
    fig.add_shape(type="rect", x0=4.5, y0=6.5, x1=10, y1=10,
                  fillcolor="rgba(255,80,80,0.15)", line_width=0)
    _base(fig, "Risk map: severity × occurrence — hunt the top-right",
          "Occurrence (1–10)", "Severity (1–10)")
    fig.update_layout(hovermode="closest",
                      xaxis=dict(range=[0, 10]), yaxis=dict(range=[0, 10.8]))
    return fig, ("Standard DFMEA scoring. The point isn't the numbers — it's "
                 "that the worst risks usually sit on a BOUNDARY between two "
                 "subteams, which is exactly what this tool propagates. "
                 + _CONC)


def _teamfit():
    fig = go.Figure()
    # Un-triangulated bay: square + its racked (sheared) ghost.
    sq_x, sq_y = [0, 1, 1, 0, 0], [0, 0, 1, 1, 0]
    rk_x, rk_y = [0, 1, 1.25, 0.25, 0], [0, 0, 1, 1, 0]
    fig.add_trace(go.Scatter(x=sq_x, y=sq_y, mode="lines",
                             name="Square bay (as drawn)"))
    fig.add_trace(go.Scatter(x=rk_x, y=rk_y, mode="lines",
                             line=dict(dash="dot"),
                             name="Same bay under load — it racks"))
    # Triangulated bay: shifted right, with a diagonal.
    tx = 1.9
    fig.add_trace(go.Scatter(x=[x + tx for x in sq_x], y=sq_y, mode="lines",
                             name="Triangulated bay", showlegend=True))
    fig.add_trace(go.Scatter(x=[tx, tx + 1], y=[0, 1], mode="lines",
                             line=dict(width=4), name="One diagonal = rigid"))
    _base(fig, "Why triangulation: a quadrilateral racks, a triangle can't",
          "", "", height=260)
    fig.update_layout(hovermode="closest",
                      xaxis=dict(visible=False), yaxis=dict(visible=False,
                                 scaleanchor="x", scaleratio=1))
    return fig, ("Pure kinematics of pin-jointed frames: four bars hinge "
                 "freely; adding one diagonal makes the shape geometrically "
                 "rigid — load then travels as tension/compression, not "
                 "bending. This is what the frame audit checks bay by bay. "
                 + _CONC)


def _model3d():
    # Minimal wireframe car: chassis box, nose, wing, four wheels.
    def box(x0, x1, y0, y1, z0, z1):
        pts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
               (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7),
                 (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
        xs, ys, zs = [], [], []
        for a, b in edges:
            xs += [pts[a][0], pts[b][0], None]
            ys += [pts[a][1], pts[b][1], None]
            zs += [pts[a][2], pts[b][2], None]
        return xs, ys, zs

    fig = go.Figure()
    for name, bx in (("Chassis", box(0.0, 1.7, -0.30, 0.30, 0.05, 0.55)),
                     ("Nose", box(1.7, 2.3, -0.18, 0.18, 0.10, 0.35)),
                     ("Rear wing", box(-0.35, -0.05, -0.45, 0.45, 0.60, 0.95)),
                     ("Accumulator", box(0.15, 0.75, -0.22, 0.22, 0.08, 0.38))):
        xs, ys, zs = bx
        fig.add_trace(go.Scatter3d(x=xs, y=ys, z=zs, mode="lines", name=name))
    th = np.linspace(0, 2 * math.pi, 25)
    for cx, cy in ((0.25, -0.62), (0.25, 0.62), (1.55, -0.62), (1.55, 0.62)):
        fig.add_trace(go.Scatter3d(
            x=cx + 0.22 * np.cos(th), y=[cy] * len(th),
            z=0.22 + 0.22 * np.sin(th), mode="lines",
            showlegend=False, line=dict(width=5)))
    fig.update_layout(height=320, margin=dict(l=0, r=0, t=42, b=0),
                      title=dict(text="One car, every subsystem in context "
                                      "(drag to spin)", font=dict(size=14)),
                      scene=dict(aspectmode="data",
                                 xaxis=dict(visible=False),
                                 yaxis=dict(visible=False),
                                 zaxis=dict(visible=False)))
    return fig, ("Every part you design lives next to everyone else's — the "
                 "3D Model tab shows the real car the same way, with your "
                 "subsystem lit up. " + _CONC)


def _integration():
    subs = ["Suspension", "Brakes", "Aero", "Powertrain", "Electrics",
            "Chassis", "Cooling", "Cost"]
    n = len(subs)
    ang = [2 * math.pi * i / n for i in range(n)]
    xs = [math.cos(a) for a in ang]
    ys = [math.sin(a) for a in ang]
    fig = go.Figure()
    for x, y in zip(xs, ys):
        fig.add_trace(go.Scatter(x=[0, x], y=[0, y], mode="lines",
                                 line=dict(width=1), showlegend=False,
                                 hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers+text", text=subs,
                             textposition="top center",
                             textfont=dict(size=11),
                             marker=dict(size=14), showlegend=False))
    fig.add_trace(go.Scatter(x=[0], y=[0], mode="markers+text",
                             text=["Shared<br>numbers"],
                             textposition="middle center",
                             textfont=dict(size=10),
                             marker=dict(size=46, opacity=0.35),
                             showlegend=False))
    _base(fig, "One ledger, eight subteams — change once, everyone sees it",
          "", "", height=300)
    fig.update_layout(hovermode="closest",
                      xaxis=dict(visible=False, range=[-1.6, 1.6]),
                      yaxis=dict(visible=False, range=[-1.45, 1.55],
                                 scaleanchor="x", scaleratio=1))
    return fig, ("The mechanism that kills the classic failure: two subteams "
                 "simulating two different versions of the same number. "
                 + _CONC)


def _validation():
    stages = ["Ideas explored", "Survive KinematiK screening",
              "Full ANSYS/ADAMS runs", "Built on the car"]
    counts = [100, 12, 3, 1]
    fig = go.Figure(go.Bar(x=counts, y=stages, orientation="h",
                           text=counts, textposition="outside"))
    _base(fig, "The funnel: cheap tools explore, expensive tools confirm",
          "Design candidates", "", height=270)
    fig.update_layout(yaxis=dict(autorange="reversed"), hovermode="closest",
                      xaxis=dict(range=[0, 118]))
    return fig, ("The workflow this tab formalises: burn licence-hours and "
                 "meshing-days on the 3 finalists, never on the 100 guesses. "
                 + _CONC)


def _cost():
    parts = ["Powertrain", "Frame & body", "Suspension", "Wheels & tyres",
             "Electrical", "Brakes", "Misc"]
    share = [30, 18, 15, 12, 10, 6, 9]
    fig = go.Figure(go.Bar(x=parts, y=share, text=[f"{s}%" for s in share],
                           textposition="outside"))
    _base(fig, "Where an FSAE car's cost typically lives",
          "", "Share of total cost (%)", height=270)
    fig.update_layout(hovermode="closest", yaxis=dict(range=[0, 36]))
    return fig, ("Representative cost-event breakdown — knowing the big "
                 "levers is how you win points without redesigning in April. "
                 + _CONC)


def _weight():
    items = ["Driver", "Accumulator", "Frame", "Wheels & tyres", "Motor",
             "Everything else", "Aero"]
    kg = [68, 45, 32, 28, 22, 40, 12]
    fig = go.Figure(go.Bar(x=items, y=kg, text=[f"{k} kg" for k in kg],
                           textposition="outside"))
    _base(fig, "A car is a few heavy things — place them deliberately",
          "", "Mass (kg)", height=270)
    fig.update_layout(hovermode="closest", yaxis=dict(range=[0, 80]))
    return fig, ("Representative masses: the top four items set your CG "
                 "almost by themselves, which is why one shared weight ledger "
                 "matters. " + _ILLU)


def _docs():
    events = ["Endurance", "Design", "Autocross", "Cost", "Efficiency",
              "Acceleration", "Presentation", "Skidpad"]
    pts = [275, 150, 125, 100, 100, 100, 75, 75]
    colors = ["#8d99a6" if e not in ("Design", "Cost", "Presentation")
              else "#ffd93b" for e in events]
    fig = go.Figure(go.Bar(x=events, y=pts, marker_color=colors,
                           text=pts, textposition="outside"))
    _base(fig, "FSAE scoring: 325 of 1000 points are won on paper",
          "", "Points", height=270)
    fig.update_layout(hovermode="closest", yaxis=dict(range=[0, 310]))
    return fig, ("The classic 1000-point split — the highlighted static "
                 "events (Design, Cost, Presentation) are scored on evidence "
                 "and reasoning, which is what this tab produces. " + _ILLU)


def _registry():
    fig = go.Figure(go.Pie(
        labels=["Locked", "Declared, unlocked", "Stale / unsourced"],
        values=[14, 7, 4], hole=0.55, sort=False,
        textinfo="label+value"))
    fig.update_layout(height=270, margin=dict(l=10, r=10, t=42, b=10),
                      title=dict(text="Every number has a status and a source",
                                 font=dict(size=14)), showlegend=False)
    return fig, ("What a healthy registry looks like mid-season: most values "
                 "locked, a few in flux, and the stale ones VISIBLE instead "
                 "of hiding in someone's spreadsheet. " + _CONC)


def _notes():
    t = np.linspace(0.0, 14.0, 29)                          # days
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=t, y=100 * np.exp(-t / 3.0),
                             name="Memory of an unwritten decision"))
    fig.add_trace(go.Scatter(x=t, y=np.full_like(t, 100.0),
                             name="Written down next to the numbers",
                             line=dict(dash="dash")))
    _base(fig, "The forgetting curve: R = e^(−t/s)",
          "Days since the meeting", "How much the team remembers (%)")
    return fig, ("Ebbinghaus' exponential-decay model of memory — two weeks "
                 "after a design review, the unwritten 'why' is mostly gone. "
                 + _CONC)


def _analytics():
    subs = ["Suspension", "Electrics", "Aero", "Brakes", "Chassis",
            "Powertrain"]
    hrs = [34, 28, 19, 11, 9, 7]
    fig = go.Figure(go.Bar(x=subs, y=hrs, text=[f"{h} h" for h in hrs],
                           textposition="outside"))
    _base(fig, "Where the design effort actually went this month",
          "", "Tool-hours", height=270)
    fig.update_layout(hovermode="closest", yaxis=dict(range=[0, 42]))
    return fig, ("Example month: seeing effort (not just output) is how a "
                 "lead spots a stalled subsystem before the deadline does. "
                 + _CONC)


_BUILDERS = {
    "kinematics": _kinematics,
    "roll": _roll,
    "compliance": _compliance,
    "tire": _tire,
    "setup": _setup,
    "laptime": _laptime,
    "aero": _aero,
    "ev": _ev,
    "accum": _accum,
    "brakes": _brakes,
    "pcb": _pcb,
    "tractive": _tractive,
    "dfmea": _dfmea,
    "teamfit": _teamfit,
    "model3d": _model3d,
    "integration": _integration,
    "validation": _validation,
    "cost": _cost,
    "weight": _weight,
    "docs": _docs,
    "registry": _registry,
    "notes": _notes,
    "analytics": _analytics,
}


def concept_figure(tab_id):
    """(figure, caption) for a briefing tool id, or (None, None). Never raises."""
    try:
        builder = _BUILDERS.get(tab_id)
        if builder is None:
            return None, None
        return builder()
    except Exception:
        return None, None
