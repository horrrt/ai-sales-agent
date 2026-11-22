"""docs/use-cases.md and the tests agree: every row's tests exist, every row has a Fleet case, every test is in a row."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROW = re.compile(r"^\| ([EP]\d+) \| (.+?) \| (.+?) \| (.+?) \|$")


def rows():
    out = [ROW.match(line).groups() for line in (REPO / "docs/use-cases.md").read_text().splitlines() if ROW.match(line)]
    assert out, "no use-case rows found"
    return out


def unit_tests() -> set[str]:
    return {m for p in (REPO / "mcp/tests").glob("test_*.py") for m in re.findall(r"^def (test_\w+)", p.read_text(), re.M)}


def fleet_cases() -> set[str]:
    return {f"{p.parent.name}/{p.stem}" for p in (REPO / "evals/fleet/cases").glob("*/*.yaml")}


def test_every_row_points_at_tests_that_exist():
    units, cases, ids = unit_tests(), fleet_cases(), []
    for uid, _, unit, fleet in rows():
        ids.append(uid)
        named_units = re.findall(r"`(test_\w+)`", unit)
        assert named_units or unit.strip() == "agent only", f"{uid}: no unit test (or 'agent only')"
        assert not set(named_units) - units, f"{uid}: unknown unit tests {set(named_units) - units}"
        named_cases = re.findall(r"`([\w-]+/[\w-]+)`", fleet)
        assert named_cases, f"{uid}: no Fleet case"
        assert not set(named_cases) - cases, f"{uid}: unknown Fleet cases {set(named_cases) - cases}"
    assert len(ids) == len(set(ids)), "duplicate use-case IDs"


def test_every_test_is_in_a_use_case():
    text = "\n".join(unit + fleet for _, _, unit, fleet in rows())
    assert not {t for t in unit_tests() if f"`{t}`" not in text} - {"test_every_row_points_at_tests_that_exist", "test_every_test_is_in_a_use_case"}
    assert not {c for c in fleet_cases() if f"`{c}`" not in text}
