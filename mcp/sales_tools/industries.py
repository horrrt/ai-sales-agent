"""The industry list (data/industries.json) and mapping free text onto it.

"Online brands / e-commerce (online shops)" and "D2C" both become "E-commerce", so the ICP check, price
rules and the ICP evidence from won deals compare the same names. A name that maps to nothing is refused
with the closest matches, so a new industry is added to the list on purpose, not by a typo.
"""
import difflib
import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[1].parent / "data" / "industries.json"


@lru_cache(maxsize=1)
def _table() -> tuple[list[str], dict[str, str]]:
    rows = json.loads(DATA.read_text())["industries"]
    names = [r["name"] for r in rows]
    index = {}
    for r in rows:
        for key in [r["name"], *r.get("aliases", [])]:
            index[_key(key)] = r["name"]
    return names, index


QUALIFIERS = {"b2b", "b2c", "company", "companies", "firm", "firms", "business", "businesses", "sector", "industry",
              "small", "large", "nordic", "private", "independent"}


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def names() -> list[str]:
    return list(_table()[0])


def normalize(text: str) -> tuple[list[str], list[str]]:
    """The industry names a piece of free text means, and the parts that matched nothing.

    The text is split on slashes, commas, "and", "or" and brackets, and each part is looked up by name or alias."""
    _, index = _table()
    if not isinstance(text, str) or not text.strip():
        return [], []
    whole = _key(text)
    if whole in index:
        return [index[whole]], []
    found, unknown = [], []
    parts = [p for p in re.split(r"[/,;()&+|]|\band\b|\bor\b", text, flags=re.I) if _key(p)]
    tail = _key(parts[-1]).split()[-1] if parts else ""  # "Marketing & creative agencies": marketing -> marketing agencies
    for part in parts:
        k = _key(part)
        if all(w in QUALIFIERS for w in k.split()):
            continue  # "(B2C)", "companies": says nothing about the industry
        k = " ".join(w for w in k.split() if w not in QUALIFIERS) or k
        hit = (index.get(k) or index.get(k.removesuffix("s")) or index.get(k + "s")
               or (index.get(f"{k} {tail}") if tail and not k.endswith(tail) else None))
        if hit:
            if hit not in found:
                found.append(hit)
        else:
            unknown.append(part.strip())
    return found, unknown


def suggest(text: str, n: int = 3) -> list[str]:
    names_, index = _table()
    keys = difflib.get_close_matches(_key(text), list(index), n=n * 3, cutoff=0.5)
    out = []
    for k in keys:
        if index[k] not in out:
            out.append(index[k])
    return out[:n] or difflib.get_close_matches(text, names_, n=n, cutoff=0.0)


def canonical(text: str) -> str | None:
    """One industry name for a prospect's industry, or None if it maps to nothing (or to several)."""
    found, unknown = normalize(text)
    return found[0] if len(found) == 1 and not unknown else None


def normalize_list(values: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """ICP industries as names on the list, and for each value that didn't map, the closest names."""
    out, problems = [], {}
    for v in values or []:
        found, unknown = normalize(v)
        if unknown or not found:
            problems[v] = suggest(" ".join(unknown) or v)
        for f in found:
            if f not in out:
                out.append(f)
    return out, problems
