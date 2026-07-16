"""Run from your project root:  python verify_patch.py
Confirms the deployed backends.py has the virtual-tunnel fix BEFORE you start the app."""
import importlib, sys
try:
    bk = importlib.import_module("suspension.aero.backends")
except Exception as e:
    print("✗ could not import suspension.aero.backends:", e); sys.exit(1)

path = bk.__file__
have_entry = "virtual-tunnel" in getattr(bk, "BACKENDS", {})
src = open(path).read()
self_heal = "_make_ensemble" in src and "resolved == \"virtual-tunnel\"" in src

print(f"loaded file : {path}")
print(f"registry has 'virtual-tunnel' : {have_entry}")
print(f"resolver self-heal present    : {self_heal}")

ok = True
try:
    b = bk.get_backend("virtual-tunnel", reduction="mean",
                       agreement_tol=5.0, turbulence_model="kOmegaSST")
    print(f"get_backend('virtual-tunnel') : OK -> {type(b).__name__}")
except Exception as e:
    ok = False
    print(f"get_backend('virtual-tunnel') : ✗ {e}")

print("\n" + ("✅ PATCH IS LIVE — restart/refresh the app and the button will work."
              if (have_entry and self_heal and ok)
              else "❌ PATCH NOT LIVE — this is the OLD backends.py. Replace it at the path above."))
