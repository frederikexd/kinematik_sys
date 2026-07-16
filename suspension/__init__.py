# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
KinematiK public package API — lazy-import facade (PEP 562).

Importing this package executes NOTHING heavy. Every submodule and every
re-exported symbol load on FIRST attribute access only, so::

    import suspension                           # microseconds — no numpy, no scipy
    suspension.SuspensionKinematics             # imports kinematics on first touch
    from suspension import tubeframe            # imports only tubeframe
    from suspension.interfaces import Severity  # free — pure stdlib

The public API is UNCHANGED from the previous eager-import version.  Every
name and submodule that used to be importable from ``suspension`` still is.
The difference is *when* the cost is paid.

How it works
------------
``_SUBMODULES`` — submodules exposed as ``suspension.<name>`` attributes.
``_FROM`` — maps every re-exported symbol to (submodule, original_name).
``__getattr__`` resolves a name on first access, imports exactly the submodule
it needs, binds the result into the package namespace (so the second access is
a plain attribute lookup, not another __getattr__ call), and returns it.
``__dir__`` advertises the full public surface for tab-completion.

If you add a new public symbol, add it to ``_FROM`` (or ``_SUBMODULES``) and
to ``__all__``.  The test ``tests/test_lazy_init`` guards that these stay in
sync and that ``import suspension`` stays dependency-free.
"""

from importlib import import_module as _import_module

# ---------------------------------------------------------------------------
#  Submodules accessible as attributes of this package.
#  _SUBMODULES: every name importable as `suspension.<name>`.
# ---------------------------------------------------------------------------
_SUBMODULES = (
    "mythbuster",
    "myth_rules",
    "aero",
    "bolted_joint",
    "bracket_fos",
    "chassis",
    "compliance",
    "correlation",
    "damper",
    "dynamics",
    "electronics",
    "ev_powertrain",
    "flex",
    "fullcar3d",
    "ggv",
    "harness",
    "integration",
    "interfaces",
    "joints",
    "kinematics",
    "laptime",
    "lapsim",
    "loadpath",
    "mem_utils",
    "mountpoints",
    "pack_thermal",
    "pcb_doctor",
    "pcm_cooling",
    "process_library",
    "project",
    "pt_integration",
    "registry",
    "cad_ingest",
    "risk_propagation",
    "risk_engine",       # Integrated DFMEA Risk Engine (live matrix + slotted-joint calc)
    "release_gate",      # Manufacturing-Release Gate + clipboard checklist PDF
    "workspace",         # multi-tenant workspace isolation layer
    "setup",
    "status_dashboard",
    "tire_cosim",
    "tire_cosim_driver",
    "tire_cosim_ftire_example",
    "tire_thermal",
    "tirefit",
    "tiremodel",
    "topologies",
    "topology",
    "tractive_system",
    "transient",
    "tubeframe",          # Frame Planner — added from frame_planner branch
    "units",
    "adapter",
    "analytics",
    "dfmea",
    "ev_electrical_check",
    "ev_excel_roundtrip",
    "master_assembly",    # Master Assembly compilation engine (dummy/CAD blend)
    "throttle_return",         # throttle return-spring redundancy (T.6.2.4 / brake-pedal 2000 N)
    "throttle_return_ingest",  # bench-log / CAD cross-check ingest for return springs
    "throttle_dynamics",       # coupled return + plate-flutter screening
    "throttle_flutter_cosim",  # quasi-steady flutter co-simulation
    "history",                 # project version history: fetch, diff, restore
    "hardpoint_import",        # OptimumK / Excel / CSV hardpoint importer
)

# ---------------------------------------------------------------------------
#  Symbol → (submodule, original_name) for every re-exported public name.
# ---------------------------------------------------------------------------
_FROM = {
    # ---------- Integrated DFMEA Risk Engine ----------
    "RiskEngine":            ("risk_engine", "RiskEngine"),
    "RiskRule":              ("risk_engine", "RiskRule"),
    "RiskReport":            ("risk_engine", "RiskReport"),
    "LiveRisk":              ("risk_engine", "LiveRisk"),
    "Reading":               ("risk_engine", "Reading"),
    "PropagationEdge":       ("risk_engine", "PropagationEdge"),
    "default_rules":         ("risk_engine", "default_rules"),
    "elevate_severity":      ("risk_engine", "elevate_severity"),
    "manifold_readings":     ("risk_engine", "manifold_readings"),
    "network_readings":      ("risk_engine", "network_readings"),
    "SlottedHoleJoint":      ("risk_engine", "SlottedHoleJoint"),
    "SlottedJointResult":    ("risk_engine", "SlottedJointResult"),
    "analyze_slotted_joint": ("risk_engine", "analyze_slotted_joint"),
    # ---------- Manufacturing-Release Gate ----------
    "GateInputs":            ("release_gate", "GateInputs"),
    "GateCheck":             ("release_gate", "GateCheck"),
    "GateReport":            ("release_gate", "GateReport"),
    "TorqueSpec":            ("release_gate", "TorqueSpec"),
    "GateNotPassed":         ("release_gate", "GateNotPassed"),
    "run_gate":              ("release_gate", "run_gate"),
    "build_clipboard":       ("release_gate", "build_clipboard"),
    "render_clipboard_pdf":  ("release_gate", "render_clipboard_pdf"),
    "release_and_print":     ("release_gate", "release_and_print"),
    # ---------- workspace isolation ----------
    "Workspace":                      ("workspace", "Workspace"),
    "WorkspaceContext":               ("workspace", "WorkspaceContext"),
    "WorkspaceError":                 ("workspace", "WorkspaceError"),
    "CrossWorkspaceViolation":        ("workspace", "CrossWorkspaceViolation"),
    "LocalWorkspaceBackend":          ("workspace", "LocalWorkspaceBackend"),
    "WorkspaceScopedSupabaseBackend": ("workspace", "WorkspaceScopedSupabaseBackend"),
    "MemoryWorkspaceRegistry":        ("workspace", "MemoryWorkspaceRegistry"),
    "workspace_backend":              ("workspace", "workspace_backend"),
    "workspace_store":                ("workspace", "workspace_store"),
    "validate_workspace_id":          ("workspace", "validate_workspace_id"),
    "assert_payload_scoped":          ("workspace", "assert_payload_scoped"),
    # ---------- myth-buster engine ----------
    "check_myth":           ("mythbuster", "check"),
    "MythEngine":           ("mythbuster", "MythEngine"),
    "MythResult":           ("mythbuster", "MythResult"),
    "MythRule":             ("mythbuster", "Rule"),
    "MythVerdict":          ("mythbuster", "Verdict"),
    "parse_claim":          ("mythbuster", "parse_claim"),
    "myth_disciplines":     ("mythbuster", "disciplines"),
    "myth_reference_list":  ("mythbuster", "reference_myths"),
    # ---------- aero ----------
    "AeroMap":              ("aero", "AeroMap"),
    "AeroOrchestrator":     ("aero", "AeroOrchestrator"),
    "AeroProvider":         ("aero", "AeroProvider"),
    "AeroQuery":            ("aero", "AeroQuery"),
    "Attitude":             ("aero", "Attitude"),
    "CFDProvenance":        ("aero", "CFDProvenance"),
    "CFDSolver":            ("aero", "CFDSolver"),
    "CaseSpec":             ("aero", "CaseSpec"),
    "CoeffResult":          ("aero", "CoeffResult"),
    "FluentSolver":         ("aero", "FluentSolver"),
    "LocalSubmitter":       ("aero", "LocalSubmitter"),
    "MeshParams":           ("aero", "MeshParams"),
    "OpenFOAMSolver":       ("aero", "OpenFOAMSolver"),
    "OrchestratorReport":   ("aero", "OrchestratorReport"),
    "ReferenceAeroModel":   ("aero", "ReferenceAeroModel"),
    "RunMatrix":            ("aero", "RunMatrix"),
    "SlurmSSHSubmitter":    ("aero", "SlurmSSHSubmitter"),
    "SnappyMesher":         ("aero", "SnappyMesher"),
    "SolverFidelity":       ("aero", "SolverFidelity"),
    "SolverUnavailable":    ("aero", "SolverUnavailable"),
    "StarCCMSolver":        ("aero", "StarCCMSolver"),
    "SubmitResult":         ("aero", "SubmitResult"),
    "attitude_from_dynamics": ("aero", "attitude_from_dynamics"),
    "estimate_attitude":    ("aero", "estimate_attitude"),
    "get_aero_backend":     ("aero", "get_backend"),
    "parse_checkmesh":      ("aero", "parse_checkmesh"),
    # ---------- bolted_joint ----------
    "BOLT_GRADES":          ("bolted_joint", "BOLT_GRADES"),
    "BoltGrade":            ("bolted_joint", "BoltGrade"),
    "ClampedStack":         ("bolted_joint", "ClampedStack"),
    "Fastener":             ("bolted_joint", "Fastener"),
    "JointResult":          ("bolted_joint", "JointResult"),
    "METRIC_COARSE":        ("bolted_joint", "METRIC_COARSE"),
    "analyze_joint":        ("bolted_joint", "analyze_joint"),
    "joint_findings":       ("bolted_joint", "joint_findings"),
    # ---------- compliance ----------
    "MATERIALS":            ("flex", "MATERIALS"),
    "CompliantCorner":      ("compliance", "CompliantCorner"),
    "CompliantResult":      ("compliance", "CompliantResult"),
    "MemberStiffness":      ("compliance", "MemberStiffness"),
    "WheelLoad":            ("compliance", "WheelLoad"),
    "corner_wheel_load":    ("compliance", "corner_wheel_load"),
    # ---------- dynamics ----------
    "CornerLoads":          ("dynamics", "CornerLoads"),
    "VehicleDynamics":      ("dynamics", "VehicleDynamics"),
    "VehicleParams":        ("dynamics", "VehicleParams"),
    # ---------- electronics ----------
    "Aggressor":            ("electronics", "Aggressor"),
    "BoardCheckResult":     ("electronics", "BoardCheckResult"),
    "BoardLedger":          ("electronics", "BoardLedger"),
    "DiffPair":             ("electronics", "DiffPair"),
    "Trace":                ("electronics", "Trace"),
    "check_board":          ("electronics", "check_board"),
    "min_parallel_distance_mm": ("electronics", "min_parallel_distance_mm"),
    "parallel_run_length_mm":   ("electronics", "parallel_run_length_mm"),
    "undeclared_loads":     ("electronics", "undeclared_loads"),
    "worst_case_currents":  ("electronics", "worst_case_currents"),
    # ---------- ev_powertrain ----------
    "ArchitectureComparison": ("ev_powertrain", "ArchitectureComparison"),
    "EVLapSimulator":       ("ev_powertrain", "EVLapSimulator"),
    "EVParams":             ("ev_powertrain", "EVParams"),
    "EVRunResult":          ("ev_powertrain", "EVRunResult"),
    "Powertrain":           ("ev_powertrain", "Powertrain"),
    # ---------- flex ----------
    "CondensedFlexBody":    ("flex", "CondensedFlexBody"),
    "FlexElement":          ("flex", "FlexElement"),
    "FlexMesh":             ("flex", "FlexMesh"),
    "Material":             ("flex", "Material"),
    "axial_stiffness_tube": ("flex", "axial_stiffness_tube"),
    "guyan_condense":       ("flex", "guyan_condense"),
    "load_flex_body":       ("flex", "load_flex_body"),
    "read_mnf":             ("flex", "read_mnf"),
    "solid_rod_section":    ("flex", "solid_rod_section"),
    "tube_section":         ("flex", "tube_section"),
    # ---------- fullcar3d ----------
    "build_full_car_figure": ("fullcar3d", "build_full_car_figure"),
    "influence_summary":    ("fullcar3d", "influence_summary"),
    "override_influence_summary": ("fullcar3d", "override_influence_summary"),
    # ---------- ggv ----------
    "GGVGenerator":         ("ggv", "GGVGenerator"),
    "GGVParams":            ("ggv", "GGVParams"),
    "GGVResult":            ("ggv", "GGVResult"),
    "quick_ggv":            ("ggv", "quick_ggv"),
    "sweep_parameter":      ("ggv", "sweep_parameter"),
    # ---------- harness ----------
    "Connector":            ("harness", "Connector"),
    "Formboard":            ("harness", "Formboard"),
    "FormboardBranch":      ("harness", "FormboardBranch"),
    "HarnessCheckResult":   ("harness", "HarnessCheckResult"),
    "HarnessLedger":        ("harness", "HarnessLedger"),
    "WireRun":              ("harness", "WireRun"),
    "awg_area_mm2":         ("harness", "awg_area_mm2"),
    "awg_nominal_od_mm":    ("harness", "awg_nominal_od_mm"),
    "check_harness":        ("harness", "check_harness"),
    # ---------- joints ----------
    "JointCompliance":      ("joints", "JointCompliance"),
    # ---------- kinematics ----------
    "CornerState":          ("kinematics", "CornerState"),
    "Hardpoints":           ("kinematics", "Hardpoints"),
    "SuspensionKinematics": ("kinematics", "SuspensionKinematics"),
    # ---------- loadpath ----------
    "MEMBERS":              ("loadpath", "MEMBERS"),
    "MemberForces":         ("loadpath", "MemberForces"),
    "WheelLoad":            ("loadpath", "WheelLoad"),
    "solve_member_forces":  ("loadpath", "solve_member_forces"),
    "wheel_load_from_corner": ("loadpath", "wheel_load_from_corner"),
    # ---------- mountpoints ----------
    "GeometryLedger":       ("mountpoints", "GeometryLedger"),
    "KeepOut":              ("mountpoints", "KeepOut"),
    "MountPoint":           ("mountpoints", "MountPoint"),
    "PropagationResult":    ("mountpoints", "PropagationResult"),
    "propagate_mount_move": ("mountpoints", "propagate_mount_move"),
    # ---------- pack_thermal ----------
    "AirflowParams":        ("pack_thermal", "AirflowParams"),
    "CellParams":           ("pack_thermal", "CellParams"),
    "Fan":                  ("pack_thermal", "Fan"),
    "FanPlacementCandidate": ("pack_thermal", "FanPlacementCandidate"),
    "FanPlacementStudy":    ("pack_thermal", "FanPlacementStudy"),
    "PackLayout":           ("pack_thermal", "PackLayout"),
    "PackThermalModel":     ("pack_thermal", "PackThermalModel"),
    "PackThermalResult":    ("pack_thermal", "PackThermalResult"),
    "default_cell_params":  ("pack_thermal", "default_cell_params"),
    "fan_grid_candidates":  ("pack_thermal", "fan_grid_candidates"),
    "optimize_fan_placement": ("pack_thermal", "optimize_fan_placement"),
    "pack_current_trace":   ("pack_thermal", "pack_current_trace"),
    "simulate_pack_thermal": ("pack_thermal", "simulate_pack_thermal"),
    # ---------- pcm_cooling ----------
    "PCMAllocation":        ("pcm_cooling", "PCMAllocation"),
    "PCMMaterial":          ("pcm_cooling", "PCMMaterial"),
    "PCMResult":            ("pcm_cooling", "PCMResult"),
    "check_pcm":            ("pcm_cooling", "check_pcm"),
    "default_pcm":          ("pcm_cooling", "default_pcm"),
    "evaluate_pcm_buffer":  ("pcm_cooling", "evaluate_pcm_buffer"),
    "size_pcm_for_hold":    ("pcm_cooling", "size_pcm_for_hold"),
    # ---------- pt_integration ----------
    "AssumptionResult":     ("pt_integration", "AssumptionResult"),
    "CoolingOperatingPoint": ("pt_integration", "CoolingOperatingPoint"),
    "FSAE_TRACTIVE_POWER_CAP_KW": ("pt_integration", "FSAE_TRACTIVE_POWER_CAP_KW"),
    "FanCurve":             ("pt_integration", "FanCurve"),
    "GearCandidate":        ("pt_integration", "GearCandidate"),
    "GearObjective":        ("pt_integration", "GearObjective"),
    "GearRatioSolver":      ("pt_integration", "GearRatioSolver"),
    "GearSweepResult":      ("pt_integration", "GearSweepResult"),
    "MotorEnvelope":        ("pt_integration", "MotorEnvelope"),
    "MythCheck":            ("pt_integration", "MythCheck"),
    "SPAL_VA14_AP11_C34A":  ("pt_integration", "SPAL_VA14_AP11_C34A"),
    "SprocketDesign":       ("pt_integration", "SprocketDesign"),
    "check_assumption":     ("pt_integration", "check_assumption"),
    "cooling_operating_point": ("pt_integration", "cooling_operating_point"),
    "dfmea_rows_from_analysis": ("pt_integration", "dfmea_rows_from_analysis"),
    "driveline_peak_torque_nm": ("pt_integration", "driveline_peak_torque_nm"),
    "estimate_motor_heat_w": ("pt_integration", "estimate_motor_heat_w"),
    "motor_envelope":       ("pt_integration", "motor_envelope"),
    "power_rpm_myth_checks": ("pt_integration", "power_rpm_myth_checks"),
    "powertrain_spec_sheet": ("pt_integration", "powertrain_spec_sheet"),
    "sprocket_design":      ("pt_integration", "sprocket_design"),
    "system_k_from_point":  ("pt_integration", "system_k_from_point"),
    # ---------- tire_cosim ----------
    "CDTireModel":          ("tire_cosim", "CDTireModel"),
    "FTireModel":           ("tire_cosim", "FTireModel"),
    "ReferenceTireModel":   ("tire_cosim", "ReferenceTireModel"),
    "StructuralTireModel":  ("tire_cosim", "StructuralTireModel"),
    "TireFidelity":         ("tire_cosim", "TireFidelity"),
    "TireOutput":           ("tire_cosim", "TireOutput"),
    "TireProvenance":       ("tire_cosim", "TireProvenance"),
    "WheelState":           ("tire_cosim", "WheelState"),
    "default_structural_tire": ("tire_cosim", "default_structural_tire"),
    "make_tire_backend":    ("tire_cosim", "make_tire_backend"),
    # ---------- tire_cosim_driver ----------
    "CosimCornerSet":       ("tire_cosim_driver", "CosimCornerSet"),
    "CosimTireHistory":     ("tire_cosim_driver", "CosimTireHistory"),
    "run_cosim_maneuver":   ("tire_cosim_driver", "run_cosim_maneuver"),
    # ---------- tire_thermal ----------
    "ThermalParams":        ("tire_thermal", "ThermalParams"),
    "ThermalRun":           ("tire_thermal", "ThermalRun"),
    "ThermalTireModel":     ("tire_thermal", "ThermalTireModel"),
    "default_thermal_params": ("tire_thermal", "default_thermal_params"),
    "simulate_warmup":      ("tire_thermal", "simulate_warmup"),
    # ---------- tiremodel ----------
    "CombinedSlipTire":     ("tiremodel", "CombinedSlipTire"),
    "PacejkaLateral":       ("tiremodel", "PacejkaLateral"),
    "default_combined_tire": ("tiremodel", "default_combined_tire"),
    "default_tire":         ("tiremodel", "default_tire"),
    "relaxation_length":    ("tiremodel", "relaxation_length"),
    # ---------- topologies ----------
    "TEMPLATES":            ("topologies", "TEMPLATES"),
    "double_wishbone":      ("topologies", "double_wishbone"),
    "example":              ("topologies", "example"),
    "from_links":           ("topologies", "from_links"),
    "list_templates":       ("topologies", "list_templates"),
    "macpherson_strut":     ("topologies", "macpherson_strut"),
    "multilink":            ("topologies", "multilink"),
    "semi_trailing_arm":    ("topologies", "semi_trailing_arm"),
    "solid_axle":           ("topologies", "solid_axle"),
    "trailing_arm":         ("topologies", "trailing_arm"),
    "truck_steer_linkage":  ("topologies", "truck_steer_linkage"),
    "twist_beam":           ("topologies", "twist_beam"),
    # ---------- topology ----------
    "AxleRoll":             ("topology", "AxleRoll"),
    "Body":                 ("topology", "Body"),
    "Coincident":           ("topology", "Coincident"),
    "Constraint":           ("topology", "Constraint"),
    "DriveZ":               ("topology", "DriveZ"),
    "InPlane":              ("topology", "InPlane"),
    "Link":                 ("topology", "Link"),
    "Mechanism":            ("topology", "Mechanism"),
    "MechanismBuilder":     ("topology", "MechanismBuilder"),
    "OnLine":               ("topology", "OnLine"),
    "Point":                ("topology", "Point"),
    "RackTranslation":      ("topology", "RackTranslation"),
    "Revolute":             ("topology", "Revolute"),
    # ---------- tractive_system ----------
    "BSPD":                 ("tractive_system", "BSPD"),
    "PrechargeCircuit":     ("tractive_system", "PrechargeCircuit"),
    "PrechargeTrace":       ("tractive_system", "PrechargeTrace"),
    "REQUIRED_SHUTDOWN_NODES": ("tractive_system", "REQUIRED_SHUTDOWN_NODES"),
    "Rules":                ("tractive_system", "Rules"),
    "ShutdownChain":        ("tractive_system", "ShutdownChain"),
    "ShutdownNode":         ("tractive_system", "ShutdownNode"),
    "TSAL":                 ("tractive_system", "TSAL"),
    "TractiveSafetyResult": ("tractive_system", "TractiveSafetyResult"),
    "check_bspd":           ("tractive_system", "check_bspd"),
    "check_precharge":      ("tractive_system", "check_precharge"),
    "check_shutdown_chain": ("tractive_system", "check_shutdown_chain"),
    "check_tractive_system": ("tractive_system", "check_tractive_system"),
    "check_tsal":           ("tractive_system", "check_tsal"),
    "simulate_precharge":   ("tractive_system", "simulate_precharge"),
    # ---------- transient ----------
    "DriverInput":          ("transient", "DriverInput"),
    "RoadInput":            ("transient", "RoadInput"),
    "SettlingResult":       ("transient", "SettlingResult"),
    "TransientParams":      ("transient", "TransientParams"),
    "TransientResult":      ("transient", "TransientResult"),
    "TransientSolver":      ("transient", "TransientSolver"),
    "brake_to_throttle_maneuver": ("transient", "brake_to_throttle_maneuver"),
    "curb_strike_maneuver": ("transient", "curb_strike_maneuver"),
    "run_maneuver":         ("transient", "run_maneuver"),
    "snap_oversteer_maneuver": ("transient", "snap_oversteer_maneuver"),
    "step_steer_maneuver":  ("transient", "step_steer_maneuver"),
    "transient_vs_qss_corner": ("transient", "transient_vs_qss_corner"),
    # ---------- brakes: rotor thermal / CFD / optimiser ----------
    "BRAKE_FLUIDS":           ("brakes", "BRAKE_FLUIDS"),
    "OpenFOAMRotorCFD":       ("brakes", "OpenFOAMRotorCFD"),
    "PadSpec":                ("brakes", "PadSpec"),
    "ReferenceRotorCFD":      ("brakes", "ReferenceRotorCFD"),
    "RotorGeometry":          ("brakes", "RotorGeometry"),
    "RotorSolverUnavailable": ("brakes", "RotorSolverUnavailable"),
    "RotorThermalModel":      ("brakes", "RotorThermalModel"),
    "WheelTunnelPoint":       ("brakes", "WheelTunnelPoint"),
    "braking_power_trace":    ("brakes", "braking_power_trace"),
    "build_convective_map":   ("brakes", "build_convective_map"),
    "fluid_boil_check":       ("brakes", "fluid_boil_check"),
    "optimize_rotor":         ("brakes", "optimize_rotor"),
    "rotor_candidate_grid":   ("brakes", "rotor_candidate_grid"),
    "simulate_rotor_thermal": ("brakes", "simulate_rotor_thermal"),
    # ---------- throttle return / flutter ----------
    "ReturnResistance":       ("throttle_return", "ReturnResistance"),
    "ReturnSpring":           ("throttle_return", "ReturnSpring"),
    "check_return_redundancy": ("throttle_return", "check_return_redundancy"),
    "simulate_return_snap":   ("throttle_return", "simulate_return_snap"),
    "simulate_return_snap_single_failures":
        ("throttle_return", "simulate_return_snap_single_failures"),
    "crosscheck_pedal_against_cad":
        ("throttle_return_ingest", "crosscheck_pedal_against_cad"),
    "spring_rate_from_bench_log":
        ("throttle_return_ingest", "spring_rate_from_bench_log"),
    "screen_plate_flutter":   ("throttle_dynamics", "screen_plate_flutter"),
    "simulate_coupled_return": ("throttle_dynamics", "simulate_coupled_return"),
    "FlutterDerivative":      ("throttle_flutter_cosim", "FlutterDerivative"),
    "OscillationCase":        ("throttle_flutter_cosim", "OscillationCase"),
    "QuasiSteadyFlutterModel": ("throttle_flutter_cosim", "QuasiSteadyFlutterModel"),
    "BenchFit":               ("throttle_return_ingest", "BenchFit"),
    "CadCrossCheck":          ("throttle_return_ingest", "CadCrossCheck"),
    "ExternalCFDFlutterBackend": ("throttle_flutter_cosim", "ExternalCFDFlutterBackend"),
    "FlutterFidelity":        ("throttle_flutter_cosim", "FlutterFidelity"),
    "FlutterParams":          ("throttle_dynamics", "FlutterParams"),
    "ManifoldParams":         ("throttle_dynamics", "ManifoldParams"),
    "SnapModel":              ("throttle_return", "SnapModel"),
    "SnapResult":             ("throttle_return", "SnapResult"),
    "ThrottleInertia":        ("throttle_return", "ThrottleInertia"),
    "compressible_mass_flow": ("throttle_dynamics", "compressible_mass_flow"),
    "estimate_throttle_inertia": ("throttle_return", "estimate_throttle_inertia"),
    "check_brake_pedal_2000N": ("throttle_return", "check_brake_pedal_2000N"),
    "k_from_deflection":       ("throttle_return", "k_from_deflection"),
    "k_compression_spring":    ("throttle_return", "k_compression_spring"),
    "extract_flutter_derivative": ("throttle_flutter_cosim", "extract_flutter_derivative"),
    "throttle_flow_area":     ("throttle_dynamics", "throttle_flow_area"),
    # ---------- tubeframe (Frame Planner) ----------
    "DEMO_PATH_FROM":       ("tubeframe", "DEMO_PATH_FROM"),
    "DEMO_PATH_TO":         ("tubeframe", "DEMO_PATH_TO"),
    "FASTENER_OPTIONS":     ("tubeframe", "FASTENER_OPTIONS"),
    "FrameGraph":           ("tubeframe", "FrameGraph"),
    "FrameNode":            ("tubeframe", "FrameNode"),
    "FrameTube":            ("tubeframe", "FrameTube"),
    "MEMBER_CLASS_LABELS":  ("tubeframe", "MEMBER_CLASS_LABELS"),
    "MEMBER_CLASS_MIN_SIZE": ("tubeframe", "MEMBER_CLASS_MIN_SIZE"),
    "PANEL_MATERIALS":      ("tubeframe", "PANEL_MATERIALS"),
    "RULES_DISCLAIMER":     ("tubeframe", "RULES_DISCLAIMER"),
    "TubeSpec":             ("tubeframe", "TubeSpec"),
    "default_size_table":   ("tubeframe", "default_size_table"),
    "demo_frame":           ("tubeframe", "demo_frame"),
    "dynamic_pressure_kPa": ("tubeframe", "dynamic_pressure_kPa"),
    "equivalency_check":    ("tubeframe", "equivalency_check"),
    "frame_summary_for_ledger": ("tubeframe", "frame_summary_for_ledger"),
    "harness_attachment_loads": ("tubeframe", "harness_attachment_loads"),
    "plan_panel_attachment": ("tubeframe", "plan_panel_attachment"),
    "seat_mount_check":     ("tubeframe", "seat_mount_check"),
    "size_meets_minimum":   ("tubeframe", "size_meets_minimum"),
    # ---------- adapter ----------
    "GenericKinematics":    ("adapter", "GenericKinematics"),
    # ---------- generic (topology-agnostic) compliance ----------
    "solve_generic_compliance": ("generic_compliance", "solve_generic_compliance"),
    "GenericComplianceResult":  ("generic_compliance", "GenericComplianceResult"),
}

# ---------------------------------------------------------------------------
#  All submodules that become package-level attributes (union of _SUBMODULES
#  and every home module named in _FROM, because CPython previously bound
#  those as package attributes as a side effect of `from .X import ...`).
# ---------------------------------------------------------------------------
_ATTR_SUBMODULES = frozenset(_SUBMODULES) | {mod for (mod, _) in _FROM.values()}


# ---------------------------------------------------------------------------
#  PEP 562 — lazy attribute resolution.
# ---------------------------------------------------------------------------
def __getattr__(name: str):
    # 1) Submodule exposed directly, e.g. `suspension.aero`, `suspension.tubeframe`.
    if name in _ATTR_SUBMODULES:
        mod = _import_module(f"{__name__}.{name}")
        globals()[name] = mod          # cache — __getattr__ never re-fires
        return mod
    # 2) Symbol re-exported from a submodule, e.g. `SuspensionKinematics`.
    src = _FROM.get(name)
    if src is not None:
        submod_name, attr = src
        submod = _import_module(f"{__name__}.{submod_name}")
        try:
            value = getattr(submod, attr)
        except AttributeError as exc:
            raise ImportError(
                f"suspension.{submod_name} no longer provides '{attr}', "
                f"which suspension.__init__ re-exports as '{name}'. "
                f"Update _FROM in suspension/__init__.py."
            ) from exc
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    """Full public surface for tab-completion / dir() without importing anything."""
    return sorted(set(globals()) | set(_ATTR_SUBMODULES) | set(_FROM) | set(__all__))


# ---------------------------------------------------------------------------
#  __all__ — full public API, grouped by discipline.
# ---------------------------------------------------------------------------
__all__ = [
    # myth-buster engine
    "check_myth", "MythEngine", "MythResult", "MythRule", "MythVerdict",
    "parse_claim", "myth_disciplines", "myth_reference_list", "mythbuster",
    # kinematics
    "SuspensionKinematics", "Hardpoints", "CornerState",
    # topology engine
    "topology", "topologies", "GenericKinematics",
    "Point", "Body", "Constraint", "Link", "Coincident", "OnLine", "InPlane",
    "Revolute", "DriveZ", "RackTranslation", "AxleRoll", "Mechanism",
    "MechanismBuilder",
    "double_wishbone", "macpherson_strut", "multilink", "trailing_arm",
    "semi_trailing_arm", "solid_axle", "twist_beam", "truck_steer_linkage",
    "from_links", "TEMPLATES", "list_templates", "example",
    # vehicle dynamics
    "VehicleDynamics", "VehicleParams", "CornerLoads",
    # tyre model
    "PacejkaLateral", "default_tire", "CombinedSlipTire",
    "default_combined_tire", "relaxation_length",
    # submodules
    "chassis", "integration", "project", "tiremodel", "tirefit", "setup",
    "laptime", "correlation", "damper", "interfaces",
    # flexible-body extension
    "Material", "MATERIALS", "tube_section", "solid_rod_section",
    "axial_stiffness_tube", "FlexElement", "FlexMesh", "guyan_condense",
    "CondensedFlexBody", "load_flex_body", "read_mnf",
    "WheelLoad", "MemberForces", "solve_member_forces",
    "wheel_load_from_corner", "MEMBERS",
    "MemberStiffness", "CompliantResult", "CompliantCorner", "corner_wheel_load",
    "JointCompliance",
    # generic (topology-agnostic) compliance
    "solve_generic_compliance", "GenericComplianceResult",
    "flex", "loadpath", "compliance", "joints", "generic_compliance",
    # transient DAE solver
    "TransientSolver", "TransientParams", "TransientResult", "SettlingResult",
    "DriverInput", "RoadInput",
    "step_steer_maneuver", "snap_oversteer_maneuver", "brake_to_throttle_maneuver",
    "curb_strike_maneuver", "run_maneuver", "transient_vs_qss_corner",
    "transient",
    # EV powertrain & energy
    "Powertrain", "EVParams", "EVLapSimulator",
    "EVRunResult", "ArchitectureComparison", "ev_powertrain",
    # battery pack thermal
    "CellParams", "default_cell_params", "PackLayout",
    "Fan", "AirflowParams", "PackThermalModel", "PackThermalResult",
    "pack_current_trace", "simulate_pack_thermal",
    "FanPlacementCandidate", "FanPlacementStudy",
    "optimize_fan_placement", "fan_grid_candidates", "pack_thermal",
    # structural tire co-simulation
    "StructuralTireModel", "ReferenceTireModel", "FTireModel", "CDTireModel",
    "WheelState", "TireOutput", "TireProvenance", "TireFidelity",
    "make_tire_backend", "default_structural_tire",
    "CosimCornerSet", "CosimTireHistory", "run_cosim_maneuver",
    "tire_cosim", "tire_cosim_driver", "tire_cosim_ftire_example",
    # tyre thermal
    "ThermalTireModel", "ThermalParams", "ThermalRun",
    "default_thermal_params", "simulate_warmup", "tire_thermal",
    # aerodynamic CFD co-simulation
    "Attitude", "RunMatrix", "CaseSpec", "CoeffResult", "CFDProvenance",
    "SolverFidelity", "CFDSolver", "SolverUnavailable",
    "ReferenceAeroModel", "OpenFOAMSolver", "StarCCMSolver", "FluentSolver",
    "get_aero_backend",
    "LocalSubmitter", "SlurmSSHSubmitter", "SubmitResult",
    "AeroMap", "AeroQuery", "AeroOrchestrator", "OrchestratorReport",
    "AeroProvider", "estimate_attitude", "attitude_from_dynamics",
    "MeshParams", "SnappyMesher", "parse_checkmesh", "aero",
    # mount-point clash + CG propagation
    "MountPoint", "KeepOut", "GeometryLedger",
    "PropagationResult", "propagate_mount_move",
    "mountpoints",
    # electronics / PCB layer
    "Trace", "DiffPair", "Aggressor", "BoardLedger", "BoardCheckResult",
    "check_board", "worst_case_currents", "undeclared_loads",
    # harness / 3-D loom
    "Connector", "WireRun", "HarnessLedger", "HarnessCheckResult",
    "Formboard", "FormboardBranch", "check_harness",
    "awg_area_mm2", "awg_nominal_od_mm", "harness",
    "min_parallel_distance_mm", "parallel_run_length_mm",
    "electronics",
    # bolted joints
    "BOLT_GRADES", "BoltGrade", "ClampedStack", "Fastener", "JointResult",
    "METRIC_COARSE", "analyze_joint", "joint_findings",
    # fullcar3d
    "build_full_car_figure", "influence_summary", "override_influence_summary",
    # GGV
    "GGVGenerator", "GGVParams", "GGVResult", "quick_ggv", "sweep_parameter",
    # PCM cooling
    "PCMAllocation", "PCMMaterial", "PCMResult", "check_pcm",
    "default_pcm", "evaluate_pcm_buffer", "size_pcm_for_hold",
    # pt_integration
    "AssumptionResult", "CoolingOperatingPoint", "FSAE_TRACTIVE_POWER_CAP_KW",
    "FanCurve", "GearCandidate", "GearObjective", "GearRatioSolver",
    "GearSweepResult", "MotorEnvelope", "MythCheck", "SPAL_VA14_AP11_C34A",
    "SprocketDesign", "check_assumption", "cooling_operating_point",
    "dfmea_rows_from_analysis", "driveline_peak_torque_nm",
    "estimate_motor_heat_w", "motor_envelope", "power_rpm_myth_checks",
    "powertrain_spec_sheet", "sprocket_design", "system_k_from_point",
    # tractive system
    "BSPD", "PrechargeCircuit", "PrechargeTrace", "REQUIRED_SHUTDOWN_NODES",
    "Rules", "ShutdownChain", "ShutdownNode", "TSAL", "TractiveSafetyResult",
    "check_bspd", "check_precharge", "check_shutdown_chain",
    "check_tractive_system", "check_tsal", "simulate_precharge",
    # tube frame planner (Frame Planner — triangulation/load-path/sourcing/attachments)
    "tubeframe",
    "FrameGraph", "FrameNode", "FrameTube", "TubeSpec",
    "MEMBER_CLASS_MIN_SIZE", "MEMBER_CLASS_LABELS",
    "FASTENER_OPTIONS", "PANEL_MATERIALS",
    "RULES_DISCLAIMER", "DEMO_PATH_FROM", "DEMO_PATH_TO",
    "default_size_table", "equivalency_check", "size_meets_minimum",
    "demo_frame", "frame_summary_for_ledger",
    "plan_panel_attachment", "harness_attachment_loads", "seat_mount_check",
    "dynamic_pressure_kPa",
    # Integrated DFMEA Risk Engine (live risk matrix + slotted-joint calculator)
    "risk_engine",
    "RiskEngine", "RiskRule", "RiskReport", "LiveRisk", "Reading",
    "PropagationEdge", "default_rules", "elevate_severity",
    "manifold_readings", "network_readings",
    "SlottedHoleJoint", "SlottedJointResult", "analyze_slotted_joint",
    # Manufacturing-Release Gate
    "release_gate",
    "GateInputs", "GateCheck", "GateReport", "TorqueSpec", "GateNotPassed",
    "run_gate", "build_clipboard", "render_clipboard_pdf", "release_and_print",
    # workspace isolation
    "workspace",
    "Workspace", "WorkspaceContext", "WorkspaceError", "CrossWorkspaceViolation",
    "LocalWorkspaceBackend", "WorkspaceScopedSupabaseBackend",
    "MemoryWorkspaceRegistry", "workspace_backend", "workspace_store",
    "validate_workspace_id", "assert_payload_scoped",
]

__version__ = "0.23.0"
