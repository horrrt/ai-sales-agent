#!/usr/bin/env python3
"""Build an upload-ready Fleet zip from fleet/, after checking everything Fleet's import needs.

    python3 scripts/package_fleet.py --mcp-url https://sales-tools-xyz.a.run.app/mcp
    python3 scripts/package_fleet.py --sandbox          # keep the placeholder URL (for a local look only)
    python3 scripts/package_fleet.py --check            # validate only, no zip

Fleet's sidebar: + > Import, then pick the zip. Import creates the agent exactly as written and skips
schedules, so add the schedules in config.json's metadata by hand.

The repo's fleet/ keeps the placeholder server URL (https://sales-tools.invalid/mcp). With --mcp-url the
zip gets your deployed tool server's URL in every tools.json and approval key; fleet/ itself is not changed.

Checks (any error stops the build):
- the layout: AGENTS.md, config.json, tools.json, subagents/<name>/{AGENTS.md, tools.json}, skills/<name>/SKILL.md
- every JSON file parses; every subagent and skill has frontmatter with a description (no colons for
  subagents, which Fleet's import rejects); every @name the manager or a skill routes to exists
- every tool on our server that a tools.json lists exists in mcp/sales_tools, and has an approval entry
- the tools that commit the company (APPROVAL_TOOLS) ask a person in every tools.json that lists them
- no secrets (API keys, tokens, .env files) anywhere in the zip
- only files that belong in Fleet's export layout go in (a stray notes file in subagents/ is left out, with a warning)
Warnings (printed, not fatal): TODO placeholders such as the Slack channel ID, and the placeholder URL.
"""
import argparse
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FLEET = REPO / "fleet"
PLACEHOLDER = "https://sales-tools.invalid/mcp"
SERVER_NAME = "Sales tools (sandbox)"
APPROVAL_TOOLS = {"record_approval", "book_meeting", "reschedule_meeting", "cancel_meeting", "record_meeting_outcome",
                  "approve_quote", "record_contract", "record_deal_outcome", "record_payment", "decide_proposal",
                  "update_brief", "add_team_member", "update_team_member"}
SECRET_PATTERNS = [r"sk-ant-[A-Za-z0-9_-]{10,}", r"lsv2_[a-z]{2}_[A-Za-z0-9]{10,}", r"xox[baprs]-[A-Za-z0-9-]{10,}",
                   r"AKIA[0-9A-Z]{16}", r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"]


def frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def belongs(rel: str) -> bool:
    """Whether a file is part of Fleet's export layout (as in an export from Fleet itself)."""
    parts = rel.split("/")
    if len(parts) == 1:
        return rel in ("AGENTS.md", "config.json", "tools.json")
    if parts[0] == "subagents":
        return len(parts) == 3 and parts[2] in ("AGENTS.md", "tools.json")
    if parts[0] == "skills":
        return len(parts) >= 3  # SKILL.md and any files the skill keeps next to it
    return False


def server_tools() -> set[str]:
    src = "".join(p.read_text() for p in (REPO / "mcp/sales_tools/tools").glob("*.py"))
    return set(re.findall(r"@mcp\.tool\(\)\s*\ndef (\w+)", src))


def validate(root: Path) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    for f in ("AGENTS.md", "config.json", "tools.json"):
        if not (root / f).is_file():
            errors.append(f"missing {f}")
    if errors:
        return errors, warnings
    for p in sorted(root.rglob("*.json")):
        try:
            json.loads(p.read_text())
        except ValueError as e:
            errors.append(f"{p.relative_to(root)} is not valid JSON: {e}")
    subagents = sorted(d.name for d in (root / "subagents").iterdir() if d.is_dir()) if (root / "subagents").is_dir() else []
    skills = sorted(d.name for d in (root / "skills").iterdir() if d.is_dir()) if (root / "skills").is_dir() else []
    for name in subagents:
        d = root / "subagents" / name
        for f in ("AGENTS.md", "tools.json"):
            if not (d / f).is_file():
                errors.append(f"subagents/{name} has no {f}")
        fm = frontmatter((d / "AGENTS.md").read_text()) if (d / "AGENTS.md").is_file() else {}
        if not fm.get("description"):
            errors.append(f"subagents/{name}/AGENTS.md has no description in its frontmatter")
        elif ":" in fm["description"]:
            errors.append(f"subagents/{name}: the description contains a colon, which Fleet's import rejects")
    for name in skills:
        p = root / "skills" / name / "SKILL.md"
        if not p.is_file():
            errors.append(f"skills/{name} has no SKILL.md")
            continue
        fm = frontmatter(p.read_text())
        if fm.get("name") != name:
            errors.append(f"skills/{name}/SKILL.md: name in the frontmatter should be {name!r}")
        if not fm.get("description"):
            errors.append(f"skills/{name}/SKILL.md has no description")
    texts = [(root / "AGENTS.md").read_text()] + [(root / "skills" / s / "SKILL.md").read_text() for s in skills
                                                    if (root / "skills" / s / "SKILL.md").is_file()]
    for t in texts:
        for ref in set(re.findall(r"@([a-z][a-z-]+)", t)):
            if ref not in subagents and ref not in ("sales-desk", "sales-assistant"):  # Slack names, not subagents
                errors.append(f"@{ref} is routed to but there is no subagents/{ref}")
    ours = server_tools()
    tool_files = [root / "tools.json"] + [root / "subagents" / s / "tools.json" for s in subagents]
    for tf in tool_files:
        if not tf.is_file():
            continue
        cfg = json.loads(tf.read_text())
        rel = tf.relative_to(root)
        keys = cfg.get("interrupt_config", {})
        for t in cfg.get("tools", []):
            url, name = t.get("mcp_server_url", ""), t.get("name")
            if "tools.langchain.com" in url:
                continue  # Fleet's built-in tools (Slack)
            if name not in ours:
                errors.append(f"{rel}: {name} is not a tool on our server (mcp/sales_tools)")
            key = f"{url}::{name}::{t.get('mcp_server_name')}"
            if key not in keys:
                errors.append(f"{rel}: no approval entry for {name}")
            elif name in APPROVAL_TOOLS and keys[key] is not True:
                errors.append(f"{rel}: {name} must ask a person (interrupt_config true)")
        listed = {f"{t.get('mcp_server_url')}::{t.get('name')}::{t.get('mcp_server_name')}" for t in cfg.get("tools", [])}
        for k in keys:
            if k not in listed:
                warnings.append(f"{rel}: approval entry for a tool that isn't listed: {k}")
    for p in sorted(root.rglob("*")):
        if p.is_file() and not belongs(str(p.relative_to(root))):
            warnings.append(f"{p.relative_to(root)}: not part of Fleet's export layout, left out of the zip")
    for p in sorted(root.rglob("*")):
        if p.is_file():
            if p.name.startswith(".env"):
                errors.append(f"{p.relative_to(root)}: an .env file must never be uploaded")
            text = p.read_text(errors="ignore")
            for pat in SECRET_PATTERNS:
                if re.search(pat, text):
                    errors.append(f"{p.relative_to(root)} looks like it contains a secret ({pat[:20]}...)")
            if p.suffix == ".md":
                for m in re.finditer(r"[^\n]*\bTODO\b[^\n]*", text):
                    warnings.append(f"{p.relative_to(root)}: {m.group(0).strip()[:100]}")
    return errors, warnings


def set_server(root: Path, url: str, name: str) -> int:
    """Point every tools.json in the copy at the deployed tool server."""
    changed = 0
    for tf in root.rglob("tools.json"):
        cfg = json.loads(tf.read_text())
        for t in cfg.get("tools", []):
            if t.get("mcp_server_url") == PLACEHOLDER:
                t["mcp_server_url"], t["mcp_server_name"] = url, name
                changed += 1
        cfg["interrupt_config"] = {
            (k.replace(PLACEHOLDER, url, 1).rsplit("::", 1)[0] + "::" + name if k.startswith(PLACEHOLDER + "::") else k): v
            for k, v in cfg.get("interrupt_config", {}).items()}
        tf.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    return changed


def build(out: Path, url: str | None, name: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "fleet"
        shutil.copytree(FLEET, copy, ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        changed = set_server(copy, url, name) if url else 0
        errors, warnings = validate(copy)
        if errors:
            return {"ok": False, "errors": errors, "warnings": warnings}
        out.parent.mkdir(parents=True, exist_ok=True)
        files = sorted(p for p in copy.rglob("*") if p.is_file() and belongs(str(p.relative_to(copy))))
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in files:
                info = zipfile.ZipInfo(str(p.relative_to(copy)), date_time=(2026, 1, 1, 0, 0, 0))  # reproducible zips
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, p.read_bytes())
        root = json.loads((copy / "tools.json").read_text())
        asks = sorted(k.split("::")[1] for k, v in root.get("interrupt_config", {}).items() if v)
        return {"ok": True, "zip": str(out), "files": len(files), "urls_set": changed, "warnings": warnings,
                "subagents": sorted(d.name for d in (copy / "subagents").iterdir() if d.is_dir()),
                "skills": sorted(d.name for d in (copy / "skills").iterdir() if d.is_dir()), "asks_a_person": asks,
                "schedules": json.loads((copy / "config.json").read_text()).get("metadata", {}).get("schedules_local", [])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mcp-url", help="the deployed tool server, e.g. https://sales-tools-xyz.a.run.app/mcp")
    ap.add_argument("--server-name", default=SERVER_NAME, help="the MCP server's display name in Fleet")
    ap.add_argument("--sandbox", action="store_true", help="keep the placeholder URL (not for upload)")
    ap.add_argument("--check", action="store_true", help="validate fleet/ only, build nothing")
    ap.add_argument("--out", default=str(REPO / "dist/sales-assistant-fleet.zip"))
    args = ap.parse_args()
    if args.check:
        errors, warnings = validate(FLEET)
        for w in warnings:
            print(f"warning: {w}")
        for e in errors:
            print(f"ERROR: {e}")
        print("fleet/ is valid" if not errors else f"{len(errors)} error(s)")
        return 1 if errors else 0
    if not args.mcp_url and not args.sandbox:
        print("Pass --mcp-url with the deployed tool server (or --sandbox to keep the placeholder URL)")
        return 2
    if args.mcp_url and not re.match(r"^https://[^\s/]+(/\S*)?$", args.mcp_url):
        print("--mcp-url must be an https URL")
        return 2
    r = build(Path(args.out), args.mcp_url, args.server_name)
    for w in r["warnings"]:
        print(f"warning: {w}")
    if not r["ok"]:
        for e in r["errors"]:
            print(f"ERROR: {e}")
        print("Nothing built.")
        return 1
    if args.sandbox:
        print(f"warning: the zip keeps the placeholder {PLACEHOLDER}; Fleet can't reach it")
    print(f"Built {r['zip']}: {r['files']} files, {len(r['subagents'])} subagents, {len(r['skills'])} skills, "
          f"{r['urls_set']} tool URLs set")
    print(f"Asks a person before: {', '.join(r['asks_a_person'])}")
    print("Import: Fleet sidebar, + > Import. Then add these schedules by hand:")
    for s in r["schedules"]:
        print(f"  - {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
