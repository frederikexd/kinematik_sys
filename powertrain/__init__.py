"""powertrain — lazy package facade (PEP 562), same contract as `suspension`:
`import powertrain` is inert; `powertrain.engine` (and its re-exports) load on
first attribute touch only."""
import importlib

_SUBMODULES = {"engine"}

_SYMBOL_HOME = {s: "engine" for s in (
    "MotorCurve", "DrivetrainParams", "DrivetrainResult", "simulate_launch",
    "optimize_gear_ratio", "GearSweepResult",
    "CoolantProps", "PipeSegment", "YBranch", "STANDARD_Y_BRANCHES",
    "CoolingNetwork", "PumpCurve", "Radiator", "JunctionAudit",
    "ThermalResult", "simulate_lap_thermal",
    "total_mass_from_ledger", "publish_to_ledger",
)}

__all__ = sorted(_SUBMODULES | set(_SYMBOL_HOME))


def __getattr__(name):
    if name in _SUBMODULES:
        mod = importlib.import_module(f".{name}", __name__)
        globals()[name] = mod
        return mod
    home = _SYMBOL_HOME.get(name)
    if home:
        obj = getattr(importlib.import_module(f".{home}", __name__), name)
        globals()[name] = obj
        return obj
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r} — add it to "
        f"powertrain.__init__._SYMBOL_HOME")


def __dir__():
    return __all__
