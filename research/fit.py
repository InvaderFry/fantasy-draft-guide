"""S83's one-page rule, measured rather than remembered.

    "Everything in this document must compress to ONE PAGE usable during a live
    draft."

It is the sheet's hardest constraint and S78's acceptance criterion, and it is
the only thing on the draft-night path that nothing checked. `refresh-check`
refuses a blocked or thinned board, `archive-status` files an issue when the
price series stalls, `preseason-status` reds while a missed bundle is still worth
taking -- and the page count was a human running chromium over 26 files and
counting, when they remembered to.

It has broken twice, silently: at 24 players a position when TIERS and SURVIVAL
first carried content, and again at 16 the moment the ADP column arrived and five
columns in a 171px cell started wrapping names. Both times the page rendered, the
sections were filled, and every other gate passed. The board moves daily -- names
enter and leave the top twelve, the S31.3 arrow was added last week -- so the
next break is a longer name away, on a morning nobody is watching.

**The instrument is the one that set the constant.** This runs the invocation the
README documented for the hand sweep, unchanged, so the automated gate reproduces
that measurement instead of substituting a second one that disagrees with it:

    <browser> --headless --no-pdf-header-footer --print-to-pdf=<tmp> file://<sheet>

Nothing under `artifacts/` is written. Every render goes to a temporary
directory, including the probe renders that measure headroom.

**Fonts: the runner is measured, the drafter prints.** The sheet's stack is
`-apple-system, "Segoe UI", Helvetica, Arial, sans-serif`. On a Linux runner the
first three do not exist and `Arial` resolves to Liberation Sans, which is
metric-compatible with Arial and WIDER than SF Pro or Segoe UI. A page that fits
here therefore fits on the machine that prints it -- the measurement is a
conservative bound rather than a coincidence, which is why the workflow installs
`fonts-liberation` and why `resolved_font()` is reported: a runner image that
stops shipping it loosens the bound, and that has to be visible in the log rather
than silent.

**A check that cannot run must not report success.** With no browser found this
raises rather than passing, because a gate that quietly stops measuring is the
failure mode this repository keeps closing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from research import sheet as sheet_mod
from research.method import ARTIFACT_DIR

# In order. CHROME_BIN wins, because a runner image can carry the binary under a
# name none of these guesses covers and the workflow should be able to say so.
BROWSER_CANDIDATES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")

# The flags the README's hand sweep used, plus the three every containerised
# Chromium needs and none of which touch layout: no sandbox (no user namespaces
# in a CI container), no GPU, and /dev/shm off (it is 64 MB in most containers
# and Chromium will crash rather than fall back).
BASE_FLAGS = (
    "--headless",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--no-pdf-header-footer",
)

# How far past MAX_TIER_PLAYERS the headroom probe looks before it stops asking.
# Three is enough to answer the question it exists for -- "is this page one name
# from breaking, or four" -- and every probe is a render.
HEADROOM_CAP = 3

# Renders are subprocesses, so threads are the right pool. Four keeps the sweep
# under half a minute without putting 26 Chromiums on a two-core runner at once.
WORKERS = 4

RENDER_TIMEOUT_S = 120

# The chooser is a phone page, not a printable one: no @page rule, no print
# stylesheet, and it is meant to scroll. Measuring it would red the gate for a
# page nobody prints. `refresh.blocked_pages` skips it for the same reason.
NOT_A_PRINTABLE_PAGE = "index.html"


class FitError(RuntimeError):
    """The measurement could not be taken -- which is not the same as passing."""


def find_browser() -> str | None:
    """The browser to measure with, or None if there is not one."""
    override = os.environ.get("CHROME_BIN")
    if override:
        # An explicit path that is not there is a configuration error, not a
        # reason to fall through to a different browser than the one asked for.
        if not (Path(override).is_file() and os.access(override, os.X_OK)):
            raise FitError(f"CHROME_BIN is set to {override!r}, which is not an executable file")
        return override
    for name in BROWSER_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def require_browser() -> str:
    found = find_browser()
    if found is None:
        raise FitError(
            "no browser found to measure with -- looked at $CHROME_BIN and then "
            f"{', '.join(BROWSER_CANDIDATES)} on PATH. S83's one-page rule cannot be "
            "checked without one, and an unmeasured page is not a page that fits."
        )
    return found


def browser_version(browser: str) -> str:
    try:
        out = subprocess.run(
            [browser, "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def resolved_font(family: str = "Arial") -> str:
    """What the sheet's widest declared family actually resolves to here.

    Reported, never enforced. It is the one input to the measurement that the
    machine supplies rather than the repository, so a change in it is a change in
    what "fits" means.
    """
    fc = shutil.which("fc-match")
    if fc is None:
        return "unknown (no fc-match)"
    try:
        out = subprocess.run(
            [fc, "-f", "%{family}", family], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def _pdf_pages(pdf: Path) -> int:
    from pypdf import PdfReader  # lazy: nothing else in the CLI needs it

    return len(PdfReader(str(pdf)).pages)


def page_count(page: Path | str, browser: str, *, html: str | None = None) -> int:
    """Pages the sheet prints on.

    `page` is a path to read, or a name to give `html` when the markup is being
    measured before it is written anywhere -- which is what the headroom probe
    does, so a probe render never lands beside the real sheets.
    """
    with tempfile.TemporaryDirectory(prefix="s83-fit-") as tmp:
        work = Path(tmp)
        if html is not None:
            source = work / "sheet.html"
            source.write_text(html)
        else:
            source = Path(page).resolve()
            if not source.is_file():
                raise FitError(f"no such sheet to measure: {source}")
        out = work / "sheet.pdf"
        result = subprocess.run(
            [
                browser,
                *BASE_FLAGS,
                # Its own profile per render: the sweep runs several at once and
                # they would otherwise contend for one profile lock.
                f"--user-data-dir={work / 'profile'}",
                f"--print-to-pdf={out}",
                source.as_uri(),
            ],
            capture_output=True,
            text=True,
            timeout=RENDER_TIMEOUT_S,
            check=False,
        )
        # stderr is not the signal. Every containerised Chromium writes dbus and
        # GPU noise there on a successful run; the file is the signal.
        if not out.is_file():
            raise FitError(
                f"the browser wrote no PDF for {Path(page).name} (exit {result.returncode}): "
                f"{result.stderr.strip()[-600:] or 'no output'}"
            )
        return _pdf_pages(out)


def printable_sheets(edition: str, root: Path | None = None) -> list[Path]:
    """The rendered pages this gate is responsible for.

    What is missing is not this gate's business -- `refresh.missing_sheets`
    counts an edition against what S83's renderer would have written, and a
    second opinion on absence in a second place is one more thing to keep in
    agreement.
    """
    directory = (root or ARTIFACT_DIR) / edition / "sheets"
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.html") if p.name != NOT_A_PRINTABLE_PAGE)


def measure(edition: str, browser: str, *, root: Path | None = None) -> dict[str, int]:
    """Page count per rendered sheet, by filename."""
    pages = printable_sheets(edition, root)
    if not pages:
        return {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        counts = list(pool.map(lambda p: page_count(p, browser), pages))
    return {p.name: n for p, n in zip(pages, counts, strict=True)}


def headroom(
    edition: str,
    profile: dict[str, Any],
    slot: int | None,
    browser: str,
    *,
    root: Path | None = None,
    cap: int = HEADROOM_CAP,
    artifacts: dict[str, Any] | None = None,
) -> int | None:
    """How many more players a position this sheet would take before it breaks.

    The number MAX_TIER_PLAYERS encodes, asked by rendering rather than asserted.
    Zero means the constant is exactly right for this sheet -- not that something
    is wrong -- which is why nothing fails on it. It is the difference between
    "the page fits" and "the page fits and the next long name breaks it".

    None means it could not be measured, and that case is real rather than
    defensive: the probe re-renders from the method artifacts on disk, and a
    checkout that has the SHEETS but not the artifacts behind them -- which is
    every checkout of an edition rendered before those artifacts were committed
    -- re-renders a BLOCKED page. A BLOCKED page is short, so it would fit at any
    depth and report generous headroom for a board it is not measuring. Page
    counts are unaffected: those read the committed files themselves.
    """
    from research import refresh as refresh_mod

    arts = (
        artifacts
        if artifacts is not None
        else sheet_mod.load_artifacts(edition, root or ARTIFACT_DIR)
    )
    base = sheet_mod.render(edition, profile=profile, slot=slot, artifacts=arts)
    if refresh_mod.blocked_sections(base):
        return None
    for extra in range(1, cap + 1):
        markup = sheet_mod.render(
            edition,
            profile=profile,
            slot=slot,
            artifacts=arts,
            max_tier_players=sheet_mod.MAX_TIER_PLAYERS + extra,
        )
        if page_count(f"probe+{extra}", browser, html=markup) > 1:
            return extra - 1
    return cap


def check(
    edition: str,
    *,
    root: Path | None = None,
    browser: str | None = None,
    with_headroom: bool = True,
    profiles: list[dict[str, Any]] | None = None,
    cap: int = HEADROOM_CAP,
) -> dict[str, Any]:
    """Measure the edition. The caller decides what to do about it."""
    engine = browser or require_browser()
    root = root or ARTIFACT_DIR
    counts = measure(edition, engine, root=root)
    report: dict[str, Any] = {
        "edition": edition,
        "browser": engine,
        "browser_version": browser_version(engine),
        "font": resolved_font(),
        "pages": counts,
        "overflowing": sorted(name for name, n in counts.items() if n > 1),
        "headroom": {},
        "cap": cap,
    }
    if with_headroom and counts:
        arts = sheet_mod.load_artifacts(edition, root)
        targets = [
            (name, prof, slot)
            for name, prof, slot in sheet_mod.sheet_targets(profiles or sheet_mod.real_profiles())
            if name in counts
        ]
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            measured = list(
                pool.map(
                    lambda t: headroom(
                        edition, t[1], t[2], engine, root=root, cap=cap, artifacts=arts
                    ),
                    targets,
                )
            )
        report["headroom"] = {
            name: h for (name, _, _), h in zip(targets, measured, strict=True) if h is not None
        }
        report["headroom_unmeasured"] = sorted(
            name for (name, _, _), h in zip(targets, measured, strict=True) if h is None
        )
    return report


def tightest(report: dict[str, Any]) -> tuple[str, int] | None:
    """The sheet with the least room left, and how much."""
    room = report.get("headroom") or {}
    if not room:
        return None
    name = min(sorted(room), key=lambda n: room[n])
    return name, room[name]
