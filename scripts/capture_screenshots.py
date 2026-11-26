"""Capture above-the-fold screenshots of a few real public company homepages into data/screenshots/real/.

They make research_website_visual's results differ from the synthetic fixtures. The folder is git-ignored:
the repo is public and these pages are other companies' work, so each teammate captures their own copy.
One page per company, the homepage only, and only if robots.txt allows it. Cookie banners are left as they
are (we don't accept cookies on anyone's behalf), so some screenshots show one; that is a useful hard case.

    cd mcp && uv run --extra screenshots python ../scripts/capture_screenshots.py            # the list below
    cd mcp && uv run --extra screenshots python ../scripts/capture_screenshots.py pleo.io    # just these
    (first time: uv run playwright install chromium)

To use one in a test, give a lead (or a Fleet case's setup.source candidate) that company's real domain.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp"))
from sales_tools import vision  # noqa: E402

# Nordic B2B software companies with public homepages. Real companies, used only as research input in the
# sandbox: the leads that point at them have fictional contacts on .sandbox.test addresses.
DOMAINS = ["upsales.com", "superoffice.com", "pleo.io", "sympa.com"]


def main(domains):
    ok = 0
    for d in domains:
        why = vision.robots_block(d)
        if why:
            print(f"skip {d}: {why}")
            continue
        try:
            path = vision.capture(d)
            print(f"wrote {path}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"fail {d}: {type(e).__name__}: {e}"[:300])
    print(f"{ok}/{len(domains)} captured into {vision.REAL}")


if __name__ == "__main__":
    main(sys.argv[1:] or DOMAINS)
