#!/usr/bin/env python3
"""
Purchase camera calm cards: one invocation, all rules in code.

The task prompt calls this and nothing else. Every threshold, label, fallback
and gap decision lives here or in the modules it imports. Nothing is decided in
the prompt, because the prompt is where retired rules go to hide.

Runs today with only km_sources and km_alerts installed. Each missing layer is
reported as a gap and skipped, not treated as a failure. As you add
km_fetch_run, km_data and calm_card_km, this picks them up with no edit here.

    python run_purchase_cams.py
    python run_purchase_cams.py --strict          # non-zero exit on any gap
    python run_purchase_cams.py --allow-fixture   # permit postable cards from fixture data
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import km_alerts
import km_sources

CT = ZoneInfo("America/Chicago")
SLUG = "purchase-cams"
OBS_STALE_MINUTES = 15          # Mesonet transmits every 5 min; 3 missed cycles
CAMERA_STALE_MINUTES = 60
ATTRIBUTION = "Data and camera image: Kentucky Mesonet at WKU"
FOOTER_URL = "weatherbrother.com"

# Root of the Weather Brother working tree. Override with --root.
DEFAULT_ROOT = Path(__file__).resolve().parent.parent


def optional(name: str):
    """Import a module if it exists yet, else None. Missing is a gap, not an error."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return None


# ---------------------------------------------------------------- run dir


def make_dirs(root: Path, day: datetime) -> tuple[Path, Path, str]:
    """Cards go in a flat folder you open. Run data goes in a dated folder
    next to the other Nadocast run folders.

    Nothing is ever deleted. Every card filename carries the run time to the
    minute, so no two runs can collide and no run can destroy another's
    output. The prefix sorts first and the post-order number second, so a
    directory listing groups each run together in the order it posts.
    """
    run_dir = root / f"{day:%Y-%m-%d}-{SLUG}"
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    cards_dir = root / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    return run_dir, cards_dir, f"{day:%Y%m%d_%H%M}_"


def write_manifest(run_dir: Path, records: list[dict], source: str,
                   gaps: list[str], cards: list[Path] | None = None):
    """Manifest in the Weather Brother asset convention.

    The public-domain column is the point of this file. Mesonet is WKU, not a
    federal agency, so every asset from it reads 'no'. NWS alert text is
    federal and reads 'yes'.
    """
    lines = [
        f"# Purchase camera cards, {run_dir.name}",
        "",
        f"- Data source: {source}",
        f"- Attribution required on every card: {ATTRIBUTION}",
        f"- Retrieved: {datetime.now(CT):%Y-%m-%d %H:%M %Z}",
        "",
        "| file | source | retrieved | description | public domain |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in records:
        cam = r.get("camera", {})
        if cam.get("file"):
            lines.append(
                f"| {cam['file']} | Kentucky Mesonet at WKU, site {r.get('site_id')} "
                f"| {cam.get('capture_utc')} | station camera still, {r.get('county')} County | no |"
            )
    lines += [
        f"| alerts.json | api.weather.gov | {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} "
        "| active NWS alerts by county zone | yes |",
        "",
        "## Cards produced by this run",
        "",
    ]
    lines += [f"- `{c.name}`" for c in (cards or [])] or ["- none"]
    lines += [
        "",
        "## Gaps this run",
        "",
    ]
    lines += [f"- {g}" for g in gaps] or ["- none"]
    (run_dir / "manifest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- data


def _resolve_offsets(records: list[dict], now: datetime):
    """Turn fixture offset_min values into absolute stamps anchored at run time.

    The fixture stores offsets rather than absolute times so its states hold
    whenever you run it. Absolute stamps go stale, or worse, land in the future
    and trip the future-stamp guard, which makes every station unavailable and
    the fixture useless.
    """
    for r in records:
        for part, stamp_key in (("obs", "valid_utc"), ("camera", "capture_utc")):
            block = r.get(part) or {}
            off = block.pop("offset_min", None)
            if off is None:
                block.setdefault(stamp_key, None)
                continue
            block[stamp_key] = (now + timedelta(minutes=off)).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_obs(run_dir: Path, now: datetime, gaps: list[str],
             pending: list[str], force_fixture: bool = False
             ) -> tuple[list[dict], str]:
    """Real obs if the fetch layer is installed, else the fixture.

    Fixture data is tagged so nothing downstream can mistake it for live
    conditions. Posting fixture numbers as real observations is the worst
    outcome available here, so it takes an explicit flag to allow.
    """
    fetch = None if force_fixture else optional("km_fetch_run")
    data_mod = None if force_fixture else optional("km_data")

    if fetch and data_mod:
        raw = fetch.fetch_all(run_dir / "raw")
        return data_mod.normalize(raw), "live"

    pending.extend(n for n, m in (("km_fetch_run", fetch), ("km_data", data_mod)) if not m)

    return _read_fixture(now, gaps)


def _read_fixture(now: datetime, gaps: list[str]) -> tuple[list[dict], str]:
    fixture = Path(__file__).resolve().parent / "km_data_fixture.json"
    if not fixture.exists():
        gaps.append("km_data_fixture.json missing, no obs at all")
        return [], "none"
    records = json.loads(fixture.read_text(encoding="utf-8"))["stations"]
    _resolve_offsets(records, now)
    return records, "FIXTURE"


def apply_staleness(records: list[dict], now: datetime):
    """Demote fresh-looking records that are actually old.

    Unresolved is never calm. A record that cannot be aged is marked
    unavailable rather than passed through as ok.
    """
    for r in records:
        for part, limit in (("obs", OBS_STALE_MINUTES), ("camera", CAMERA_STALE_MINUTES)):
            block = r.get(part) or {}
            # partial is included: a record missing a sensor can also be old,
            # and stale is the more serious condition for a calm card. The
            # missing[] list survives the downgrade so the renderer still knows
            # which fields are absent.
            if block.get("status") not in ("ok", "stale", "partial"):
                continue
            stamp = block.get("valid_utc") or block.get("capture_utc")
            if not stamp:
                block["status"] = "unavailable"
                block["reason"] = "no timestamp"
                continue
            try:
                age = (now - datetime.fromisoformat(stamp.replace("Z", "+00:00")))
            except ValueError:
                block["status"] = "unavailable"
                block["reason"] = f"unparseable timestamp {stamp!r}"
                continue
            minutes = age.total_seconds() / 60
            block["age_minutes"] = round(minutes, 1)
            if minutes < -5:
                # A stamp in the future means a timezone or parsing error
                # upstream, not a fresh observation. Negative age would
                # otherwise sail through the freshness check.
                block["status"] = "unavailable"
                block["reason"] = f"timestamp {minutes:.0f} min in the future"
            elif minutes > limit:
                block["status"] = "stale"


def validate_run(records: list[dict], gaps: list[str]) -> bool:
    """Data-contract checks that must pass before anything renders.

    The county-label check is the guard against the class of bug where the
    right constants get applied to the wrong source.
    """
    ok = True
    for r in records:
        key = r.get("key")
        st = km_sources.STATIONS.get(key)
        if not st:
            gaps.append(f"{key}: not in registry")
            ok = False
            continue
        if r.get("county") != st.county:
            gaps.append(f"{key}: county label {r.get('county')!r} != registry {st.county!r}")
            ok = False
        for part in ("obs", "camera", "alerts"):
            if not (r.get(part) or {}).get("status"):
                gaps.append(f"{key}: {part} has no status")
                ok = False
    return ok


# ---------------------------------------------------------------- render


def render(run_dir: Path, cards_dir: Path, stamp: str, records: list[dict],
           source: str, gaps: list[str], pending: list[str],
           declines: list[tuple[str, str]]) -> list[Path]:
    renderer = optional("calm_card_km")
    if not renderer:
        pending.append("calm_card_km")
        return []
    # Fixture data renders. The watermark is the guard, and it is enough. A
    # second refusal on top of it only made the daily report read as broken.

    out = []
    for i, r in enumerate(records, start=1):
        name = f"{stamp}{i:02d}_{r['key']}.png"
        try:
            p, warn = renderer.render(
                r,
                out_path=cards_dir / name,
                attribution=ATTRIBUTION,
                footer=FOOTER_URL,
                watermark_fixture=(source == "FIXTURE"),
                run_dir=run_dir,   # camera stills live here, not beside the cards
            )
            out.append(Path(p))
            # Warnings do not stop a card. They surface so a stale constant
            # gets noticed instead of silently shipping on every card.
            gaps.extend(w for w in warn if w not in gaps)
        except Exception as exc:
            # One bad card does not kill the run. Build the rest, note it.
            # A declined card is a known limitation of the layout; anything
            # else is a real failure and belongs in gaps.
            if type(exc).__name__ == "CardDeclined":
                # Collected, not printed per station: when every site fails
                # for one reason, five identical lines are worse than one.
                reason = str(exc).split(": ", 1)[-1] if ": " in str(exc) else str(exc)
                declines.append((r["key"], reason))
            else:
                gaps.append(f"{r['key']}: render failed ({type(exc).__name__}: {exc})")

    if hasattr(renderer, "contact_sheet") and out:
        try:
            renderer.contact_sheet(out, cards_dir / f"{stamp}00_contact_sheet.png")
        except Exception as exc:
            gaps.append(f"contact sheet failed ({exc})")
    return out


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if any gap was recorded")
    ap.add_argument("--fixture", action="store_true",
                    help="ignore the live fetch layer and use the fixture. "
                         "Cards are watermarked FIXTURE DATA.")
    ap.add_argument("--offline", action="store_true",
                    help="no network at all: use the fixture's own alerts blocks. "
                         "Use this for renderer work so every state, including "
                         "suppress_calm, is reachable and deterministic.")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    gaps: list[str] = []       # something went wrong
    pending: list[str] = []    # layer not built yet, expected
    declines: list[tuple[str, str]] = []   # station could not be rendered
    drive_records: list[dict] = []          # one per file offered to Drive

    try:
        run_dir, cards_dir, stamp = make_dirs(args.root, now.astimezone(CT))
    except OSError as exc:
        print(f"FATAL cannot create directories under {args.root}: {exc}")
        return 1

    log = []
    try:
        records, source = load_obs(run_dir, now, gaps, pending, args.fixture)
        apply_staleness(records, now)

        if args.offline:
            # Keep whatever alerts the fixture declared. Overwriting them with
            # live data is what made suppress_calm untestable.
            for r in records:
                r.setdefault("alerts", {"status": "unavailable", "active": []})
        else:
            alerts = km_alerts.fetch_alerts(allow_unverified=True)
            for r in records:
                r["alerts"] = alerts.get(r["key"], {"status": "unavailable", "active": []})

        contract_ok = validate_run(records, gaps)

        unconfirmed = [r["key"] for r in records
                       if "site_id" in getattr(km_sources.STATIONS.get(r["key"]),
                                               "unverified", set())]
        if unconfirmed:
            pending.append("site_id for " + "/".join(unconfirmed))

        payload = {
            "run_utc": now.isoformat(timespec="seconds"),
            "source": source,
            "attribution": ATTRIBUTION,
            "contract_ok": contract_ok,
            "stations": records,
        }
        (run_dir / "km_data.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

        cards = render(run_dir, cards_dir, stamp, records, source, gaps,
                       pending, declines) if contract_ok else []
        if not contract_ok:
            gaps.append("data contract failed, render skipped")

    except Exception:
        log.append(traceback.format_exc())
        gaps.append("unhandled error, see run.log")
        records, source, cards = [], "none", []
        declines = []
        drive_records = []
        stamp = f"{now.astimezone(CT):%Y%m%d_%H%M}_"

    write_manifest(run_dir, records, source, gaps, cards)
    if log:
        (run_dir / "run.log").write_text("\n".join(log), encoding="utf-8")

    # Copy to Drive. Same optional-module pattern as the fetch layer: a missing
    # uploader is pending, a refused upload is a gap, and neither is allowed to
    # invalidate cards that are already on disk. This sits after the manifest
    # is written so every artifact it offers exists, and before the summary is
    # composed so an upload failure reaches `status` instead of being announced
    # under a line that already said clean.
    drive = optional("km_drive")
    if not drive:
        pending.append("km_drive")
    elif cards and source != "FIXTURE":
        # Fixture cards are watermarked and must never reach the folder Dallas
        # posts from.
        drive_records = drive.upload(
            drive.collect(run_dir, cards_dir, stamp),
            day=drive.day_from_run_dir(run_dir))
        for r in drive_records:
            if r["status"] == "failed":
                gaps.append(f"drive upload failed, {r['file']}: {r['reason']}")

    # ---- summary: fixed format, short. Verbose detail stays on disk.
    # partial and stale are renderable states, not failures: apply_staleness
    # deliberately keeps them and calm_card_km only blocks on the fields the
    # layout actually prints. Counting bare "ok" made a run that built five
    # cards report "0/5 ok" next to "status clean", which reads as broken. The
    # camera line below has always counted stale as working; this now matches
    # it, and names what is partial or stale so the number cannot hide either.
    ok_obs = sum(1 for r in records
                 if (r.get("obs") or {}).get("status") in ("ok", "partial", "stale"))
    obs_stale = [r["key"] for r in records
                 if (r.get("obs") or {}).get("status") == "stale"]
    obs_missing = sorted({m for r in records
                          for m in ((r.get("obs") or {}).get("missing") or [])})
    suppress = [r["key"] for r in records
                if (r.get("alerts") or {}).get("suppress_calm")]
    alert_names = sorted({a["event"] for r in records
                          for a in (r.get("alerts") or {}).get("active", [])})

    if source == "FIXTURE":
        status = "fixture"          # building, not broken
    elif not cards:
        # Live path taken and nothing usable came back. This must never read
        # as clean: zero cards is categorically different from four of five.
        status = "blocked"
    elif gaps:
        status = "partial"          # live, something went wrong
    else:
        status = "clean"

    lines_out: list[str] = []

    def emit(line: str):
        lines_out.append(line)
        print(line)

    emit(f"PURCHASE CAMS {datetime.now(CT):%Y-%m-%d %H:%M %Z}")
    emit(f"cards    {cards_dir}")
    emit(f"run data {run_dir}")
    obsline = f"{source}, {ok_obs}/{len(records) or 5} ok"
    if obs_stale:
        obsline += " | stale: " + "/".join(obs_stale)
    if obs_missing:
        obsline += " | partial: " + ", ".join(obs_missing)
    emit(f"obs      {obsline}")

    # The camera layer is wired independently of OBS_URL, so it can be working
    # while obs are blocked. Report it or that fact stays invisible.
    cam_ok, cam_notes, cam_reasons = 0, [], {}
    for r in records:
        c = r.get("camera") or {}
        if c.get("status") in ("ok", "stale"):
            cam_ok += 1
            qc, sc = c.get("qc", {}), c.get("scan", {})
            bits = [str(c.get("capture_utc", "?"))[5:16].replace("T", " ") + "Z"]
            if sc.get("age_minutes") is not None:
                bits.append(f"{sc['age_minutes']}m old")
            if sc.get("scanned"):
                bits.append(f"{sc['scanned']} scans")
            if qc.get("mean_luminance") is not None:
                bits.append(f"lum {qc['mean_luminance']:.0f}")
            if qc.get("verdict") == "unchanged":
                bits.append("UNCHANGED")
            cam_notes.append(f"{r['key']} ({', '.join(bits)})")
        else:
            cam_reasons.setdefault(c.get("reason", "unavailable"), []).append(r["key"])
    camline = f"{cam_ok}/{len(records) or 5} ok"
    if cam_notes:
        camline += " | " + " | ".join(cam_notes)
    for reason, keys in cam_reasons.items():
        who = "all 5" if len(keys) >= 5 else "/".join(keys)
        camline += f" | {who}: {reason}"
    emit(f"cameras  {camline}")
    emit(f"alerts   {'(offline) ' if args.offline else ''}"
          f"{', '.join(alert_names) or 'quiet'}")
    emit(f"suppress {', '.join(suppress) if suppress else 'none'}")
    cardline = str(len(cards))
    if cards and source == "FIXTURE":
        cardline += ", WATERMARKED FIXTURE, do not post"
    emit(f"built    {cardline}")

    if drive_records:
        up_ok = sum(1 for r in drive_records if r["status"] == "ok")
        driveline = f"{up_ok}/{len(drive_records)} uploaded"
        # One shared reason prints once rather than nine times.
        for why in sorted({r["reason"] for r in drive_records
                           if r["status"] != "ok" and r.get("reason")}):
            driveline += f" | {why}"
        emit(f"drive    {driveline}")
    if cards:
        emit(f"prefix   {stamp}")

    # Group declines by reason so one shared cause prints once, not five times.
    by_reason: dict[str, list[str]] = {}
    for key, reason in declines:
        by_reason.setdefault(reason, []).append(key)
    for reason, keys in by_reason.items():
        who = "all 5 stations" if len(keys) >= 5 else "/".join(keys)
        emit(f"no card  {who}: {reason}")

    if pending:
        emit(f"pending  {', '.join(pending)}")
    for g in gaps[:5]:
        emit(f"gap      {g}")
    if len(gaps) > 5:
        emit(f"gap      ... {len(gaps) - 5} more in manifest.md")
    emit(f"status   {status}")

    try:
        (run_dir / "summary.txt").write_text("\n".join(lines_out) + "\n",
                                             encoding="utf-8")
    except OSError:
        pass
    return 1 if (args.strict and gaps) else 0


if __name__ == "__main__":
    sys.exit(main())
