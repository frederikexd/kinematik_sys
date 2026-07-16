# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""suspension.aero — lazy subpackage facade (PEP 562).

Same contract as the parent package: ``import suspension.aero`` is inert;
every submodule and re-exported symbol loads on first attribute touch only.

Two entry paths:
  A — orchestrate runs:  AeroOrchestrator(backend, geometry).run(RunMatrix(...))
  B — bring a map:       AeroMap.from_csv(text) -> AeroProvider(...)

Virtual Wind Tunnel (CFD-validation path):
    from suspension.aero import PhysicalAeroMap, TunnelProvenance, VirtualWindTunnel
    phys = PhysicalAeroMap(TunnelProvenance("A2"), reference_area_m2=1.0)
    phys.add_measurement(RideHeights(20, 40, 25.0), c_lift=-2.8, c_drag=1.05)
    vwt  = VirtualWindTunnel(phys, "car.stl")
    report = vwt.correlate(cfd_results)

Plug & layup build planning (Frame Planner / aero manufacturing path):
    from suspension.aero import (NoseconeBody, FoamSheet, SlicePlan,
                                  LayupRecipe, MaterialsEstimate,
                                  BuildDaySchedule, PlugBuildPlan)
    body  = NoseconeBody(length_mm=520, base_width_mm=250, base_height_mm=260)
    plan  = SlicePlan.plan(body, FoamSheet(thickness_mm=25.4))
    bom   = MaterialsEstimate.compute(body, plan, LayupRecipe())
    sched = BuildDaySchedule.plan(...)

Quick start (runnable today, no solver):
    from suspension.aero import ReferenceAeroModel, AeroOrchestrator, RunMatrix
    orch = AeroOrchestrator(ReferenceAeroModel(), "car.stl", reference_area_m2=1.0)
    report = orch.run(RunMatrix(yaw_deg=[0,2,4,6]), workdir="/tmp/sweep")
"""
import importlib

# ---------------------------------------------------------------------------
#  Submodules — anything importable as suspension.aero.<name>.
# ---------------------------------------------------------------------------
_SUBMODULES = {
    "aeromap", "backends", "cfd", "coupling", "daq", "ensemble",
    "fluent_journal", "meshing", "orchestrator", "panel_method",
    "piv", "plug_builder", "pressure_tap", "scale_model",
    "submit", "windtunnel",
}

# ---------------------------------------------------------------------------
#  Symbol -> home submodule for every re-exported name.
# ---------------------------------------------------------------------------
_SYMBOL_HOME = {
    # cfd
    "Attitude":             "cfd",
    "RunMatrix":            "cfd",
    "CaseSpec":             "cfd",
    "CoeffResult":          "cfd",
    "CFDProvenance":        "cfd",
    "SolverFidelity":       "cfd",
    "CFDSolver":            "cfd",
    "SolverUnavailable":    "cfd",
    # backends
    "ReferenceAeroModel":   "backends",
    "FluentVerificationSolver": "backends",
    "OpenFOAMSolver":       "backends",
    "StarCCMSolver":        "backends",
    "FluentSolver":         "backends",
    "TSAutoSolver":         "backends",
    "BACKENDS":             "backends",
    "get_backend":          "backends",
    # panel_method
    "PanelMethodModel":     "panel_method",
    "PanelParams":          "panel_method",
    "PanelMethodUnavailable": "panel_method",
    # ensemble
    "EnsembleTunnelSolver": "ensemble",
    "EnsembleResult":       "ensemble",
    "MemberOutcome":        "ensemble",
    "fused_results":        "ensemble",
    "DEFAULT_MEMBER_NAMES": "ensemble",
    # submit
    "Submitter":            "submit",
    "LocalSubmitter":       "submit",
    "SlurmSSHSubmitter":    "submit",
    "SubmitResult":         "submit",
    # aeromap
    "AeroMap":              "aeromap",
    "AeroQuery":            "aeromap",
    # orchestrator
    "AeroOrchestrator":     "orchestrator",
    "OrchestratorReport":   "orchestrator",
    # coupling
    "AeroProvider":         "coupling",
    "estimate_attitude":    "coupling",
    "attitude_from_dynamics": "coupling",
    # meshing
    "MeshParams":           "meshing",
    "SnappyMesher":         "meshing",
    "parse_checkmesh":      "meshing",
    # windtunnel
    "RideHeights":          "windtunnel",
    "AeroMapGrid":          "windtunnel",
    "GroundState":          "windtunnel",
    "TunnelProvenance":     "windtunnel",
    "PhysicalAeroMap":      "windtunnel",
    "VirtualWindTunnel":    "windtunnel",
    "PointCorrelation":     "windtunnel",
    "TunnelCorrelationReport": "windtunnel",
    "ride_heights_to_attitude": "windtunnel",
    "attitude_to_ride_heights": "windtunnel",
    "downforce_to_clift":   "windtunnel",
    "drag_to_cdrag":        "windtunnel",
    "DEFAULT_TUNNEL_TOL":   "windtunnel",
    # piv
    "SheetOrientation":     "piv",
    "LaserSheetPlane":      "piv",
    "PIVProvenance":        "piv",
    "FramePair":            "piv",
    "VelocityField":        "piv",
    "PIVProcessor":         "piv",
    "AcquisitionPlan":      "piv",
    "PIVRig":               "piv",
    "OfflinePIVRig":        "piv",
    "RigUnavailable":       "piv",
    "CFDFieldSlice":        "piv",
    "FieldCorrelationReport": "piv",
    "correlate_field":      "piv",
    "separation_mask":      "piv",
    "DEFAULT_FIELD_TOL":    "piv",
    # pressure_tap
    "WingSurface":          "pressure_tap",
    "TapLocation":          "pressure_tap",
    "TapCalibration":       "pressure_tap",
    "ScanProvenance":       "pressure_tap",
    "RawPressureScan":      "pressure_tap",
    "CpField":              "pressure_tap",
    "StallVerdict":         "pressure_tap",
    "CFDSurfaceCp":         "pressure_tap",
    "TapResidual":          "pressure_tap",
    "CpCorrelationReport":  "pressure_tap",
    "correlate_cp":         "pressure_tap",
    "DEFAULT_CP_TOL":       "pressure_tap",
    # daq
    "BalanceAxis":          "daq",
    "BalanceCalibration":   "daq",
    "ScannerVendor":        "daq",
    "PressureScannerSpec":  "daq",
    "ForceBalanceSpec":     "daq",
    "DAQChassis":           "daq",
    "VibrationFilter":      "daq",
    "VibrationFilterReport": "daq",
    "ChannelFilter":        "daq",
    "StreamingVibrationFilter": "daq",
    "AcquisitionSpec":      "daq",
    "DAQProvenance":        "daq",
    "BalanceReading":       "daq",
    "DAQBackend":           "daq",
    "DAQUnavailable":       "daq",
    "OfflineDAQ":           "daq",
    "SyntheticDAQ":         "daq",
    "VirtualInstrument":    "daq",
    # scale_model
    "ScaleSpec":            "scale_model",
    "SimilitudePlan":       "scale_model",
    "ToleranceBudget":      "scale_model",
    "ToleranceReport":      "scale_model",
    "MountAlignment":       "scale_model",
    "ScaledRunPlan":        "scale_model",
    "reynolds":             "scale_model",
    "air_kinematic_viscosity": "scale_model",
    "DEFAULT_AIR_DENSITY":  "scale_model",
    "DEFAULT_AIR_KINEMATIC_VISCOSITY": "scale_model",
    "LOW_RE_BUBBLE_THRESHOLD": "scale_model",
    # plug_builder
    "NoseconeBody":         "plug_builder",
    "FoamSheet":            "plug_builder",
    "FoamLayer":            "plug_builder",
    "StackTolerance":       "plug_builder",
    "SlicePlan":            "plug_builder",
    "layer_template_svg":   "plug_builder",
    "LayupRecipe":          "plug_builder",
    "BOMLine":              "plug_builder",
    "MaterialsEstimate":    "plug_builder",
    "BuildStep":            "plug_builder",
    "default_build_day":    "plug_builder",
    "ScheduledStep":        "plug_builder",
    "BuildDaySchedule":     "plug_builder",
    "GateItem":             "plug_builder",
    "PreflightGate":        "plug_builder",
    "PlugBuildPlan":        "plug_builder",
}

# Fallback scan order when a _SYMBOL_HOME entry is wrong/missing.
_FALLBACK_SCAN = (
    "cfd", "backends", "aeromap", "orchestrator", "coupling", "meshing",
    "windtunnel", "piv", "pressure_tap", "daq", "scale_model",
    "plug_builder", "ensemble", "submit", "panel_method",
)

__all__ = sorted(_SUBMODULES | set(_SYMBOL_HOME))


def __getattr__(name: str):
    if name in _SUBMODULES:
        mod = importlib.import_module(f".{name}", __name__)
        globals()[name] = mod          # cache — __getattr__ never re-fires
        return mod
    home = _SYMBOL_HOME.get(name)
    candidates = ([home] if home else []) + [m for m in _FALLBACK_SCAN
                                              if m != home]
    for cand in candidates:
        try:
            mod = importlib.import_module(f".{cand}", __name__)
        except ImportError:
            continue
        if hasattr(mod, name):
            obj = getattr(mod, name)
            globals()[name] = obj
            return obj
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r} — add it to "
        f"suspension.aero.__init__._SYMBOL_HOME"
    )


def __dir__():
    return __all__
