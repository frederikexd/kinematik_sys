"""Tests for the front-page Status Dashboard metadata validator.

Pure validator logic — number coercion, rule evaluation (all ops + between),
component rollup (green/amber/red), and the car-level rollup. No CAD, no DB.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from suspension import status_dashboard as sd


# --------------------------------------------------------------------------- #
#  number coercion                                                            #
# --------------------------------------------------------------------------- #
def test_coerce_number():
    assert sd.coerce_number("42.0 mm") == 42.0
    assert sd.coerce_number("2,500 g") == 2500.0
    assert sd.coerce_number(1.8) == 1.8
    assert sd.coerce_number({"value": "3.5 mm"}) == 3.5
    assert sd.coerce_number("no number here") is None
    assert sd.coerce_number(None) is None


# --------------------------------------------------------------------------- #
#  single-rule evaluation                                                     #
# --------------------------------------------------------------------------- #
def test_rule_pass_and_fail():
    rule = {"param": "Weight", "op": "<=", "value": 2.5, "unit": "kg",
            "label": "Mass"}
    assert sd.evaluate_rule(rule, {"Weight": "2.1 kg"}).status == sd.GREEN
    assert sd.evaluate_rule(rule, {"Weight": "3.0 kg"}).status == sd.RED


def test_rule_between():
    rule = {"param": "Offset", "op": "between", "value": [41.0, 43.0],
            "unit": "mm", "label": "Offset"}
    assert sd.evaluate_rule(rule, {"Offset": "42 mm"}).status == sd.GREEN
    assert sd.evaluate_rule(rule, {"Offset": "44 mm"}).status == sd.RED


def test_rule_missing_input_is_amber():
    rule = {"param": "FoS", "op": ">=", "value": 1.5, "label": "FoS"}
    rr = sd.evaluate_rule(rule, {})           # not declared
    assert rr.status == sd.AMBER
    assert "not declared" in rr.message


def test_all_ops():
    specs = {"x": "5"}
    cases = [("<", 6, sd.GREEN), ("<", 4, sd.RED),
             (">", 4, sd.GREEN), (">", 6, sd.RED),
             ("==", 5, sd.GREEN), ("!=", 5, sd.RED),
             (">=", 5, sd.GREEN), ("<=", 5, sd.GREEN)]
    for op, val, want in cases:
        rule = {"param": "x", "op": op, "value": val, "label": "x"}
        assert sd.evaluate_rule(rule, specs).status == want, (op, val)


# --------------------------------------------------------------------------- #
#  component rollup                                                           #
# --------------------------------------------------------------------------- #
def _row(name="Part", has_file=True, verified=True, specs=None):
    return {"comp_id": "c1", "name": name, "subteam": "suspension",
            "status": "verified" if verified else "unverified",
            "has_file": has_file, "specs": specs or {}}


def test_component_green_when_file_verified_rules_pass():
    row = _row(specs={"Weight": "2.0 kg"})
    rules = [{"param": "Weight", "op": "<=", "value": 2.5, "label": "Mass"}]
    cs = sd.status_for_component(row, rules)
    assert cs.status == sd.GREEN
    assert cs.headline == "Ready for manufacturing"


def test_component_red_when_rule_fails():
    row = _row(specs={"Weight": "3.0 kg"})
    rules = [{"param": "Weight", "op": "<=", "value": 2.5, "label": "Mass"}]
    cs = sd.status_for_component(row, rules)
    assert cs.status == sd.RED
    assert "Mass" in cs.headline


def test_component_red_when_no_file():
    cs = sd.status_for_component(_row(has_file=False), [])
    assert cs.status == sd.RED
    assert "No file" in cs.headline


def test_component_amber_when_unverified():
    row = _row(verified=False, specs={"Weight": "2.0 kg"})
    rules = [{"param": "Weight", "op": "<=", "value": 2.5, "label": "Mass"}]
    cs = sd.status_for_component(row, rules)
    assert cs.status == sd.AMBER
    assert "signed off" in cs.headline


def test_component_red_beats_amber():
    # one rule fails (red), one input missing (amber) -> overall red
    row = _row(specs={"Weight": "3.0 kg"})
    rules = [{"param": "Weight", "op": "<=", "value": 2.5, "label": "Mass"},
             {"param": "FoS", "op": ">=", "value": 1.5, "label": "FoS"}]
    cs = sd.status_for_component(row, rules)
    assert cs.status == sd.RED


# --------------------------------------------------------------------------- #
#  car-level rollup                                                           #
# --------------------------------------------------------------------------- #
def test_car_rollup_takes_worst():
    green = sd.status_for_component(_row("A", specs={"W": "1"}), [])
    red = sd.status_for_component(_row("B", has_file=False), [])
    car = sd.roll_up([green, red])
    assert car.overall == sd.RED
    assert car.counts[sd.GREEN] == 1 and car.counts[sd.RED] == 1
    assert not car.is_ready


def test_car_rollup_all_green_is_ready():
    rules = [{"param": "Weight", "op": "<=", "value": 2.5, "label": "Mass"}]
    a = sd.status_for_component(_row("A", specs={"Weight": "2.0 kg"}), rules)
    b = sd.status_for_component(_row("B", specs={"Weight": "1.5 kg"}), rules)
    car = sd.roll_up([a, b])
    assert car.overall == sd.GREEN
    assert car.is_ready
    assert "ready for manufacturing" in car.headline.lower()


def test_extra_flag_pushes_red():
    green = sd.status_for_component(_row("A", specs={"W": "1"}), [])
    car = sd.roll_up([green], extra_flags=[
        {"status": "red", "message": "Myth failing: bigger rotor stops faster"}])
    assert car.overall == sd.RED


def test_empty_registry_is_amber():
    car = sd.roll_up([])
    assert car.overall == sd.AMBER
    assert "No components" in car.headline


# --------------------------------------------------------------------------- #
#  integration with the real Registry                                         #
# --------------------------------------------------------------------------- #
def test_with_real_registry(tmp_path):
    from suspension.registry import Registry
    reg = Registry(str(tmp_path / "registry.json"))
    c = reg.add_component("Differential Mount", "powertrain", "Dustin")
    v = reg.add_version(c.id, "Rev C", link="https://drive/x",
                        specs={"Offset": "42.0 mm", "Weight": "2.1 kg"})
    reg.verify_version(c.id, v.id, "Aidan")
    reg.set_rules(c.id, [sd.template_for("Offset"), sd.template_for("Weight")])
    reg.save()

    # reload to prove rules persist
    reg2 = Registry(str(tmp_path / "registry.json"))
    rows = reg2.summary_rows()
    assert rows[0]["rules"]          # rules survived the round-trip
    statuses = [sd.status_for_component(r, r.get("rules", [])) for r in rows]
    car = sd.roll_up(statuses)
    assert car.overall == sd.GREEN


if __name__ == "__main__":
    import traceback, tempfile, pathlib
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = 0
    for n, f in fns:
        try:
            import inspect
            kw = {}
            if "tmp_path" in inspect.signature(f).parameters:
                kw["tmp_path"] = pathlib.Path(tempfile.mkdtemp())
            f(**kw)
            print("✓", n)
            passed += 1
        except Exception:
            print("✗", n)
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
