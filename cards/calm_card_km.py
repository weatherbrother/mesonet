#!/usr/bin/env python3
"""
Adapter: km_data.json station record -> wb_card.py payload.

wb_card.py is FROZEN at layout 1.0. This file does not import-and-patch it,
does not edit it, and does not reach into its internals except to read
constants that must not be duplicated. Every translation, derivation and guard
lives here.

Why an adapter and not a rewrite: the freeze is the thing that stopped the
Nadocast pipeline from re-improvising itself every run. Two files with one
clear boundary beats one file that both layers keep touching.

    python calm_card_km.py --check                  payload for every fixture state
    python calm_card_km.py --check --run-dir DIR    check against a real run
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import wb_card

CT = ZoneInfo("America/Chicago")

# Read the threshold from the card rather than restating it. Two staleness
# numbers in two files is how a card looks fresh while the data says stale.
STALE_MIN = wb_card.STALE_MIN

# Fields the frozen layout actually puts on the card face. A record missing
# only fields outside this set still renders correctly.
DISPLAYED = {"air_temp_f", "dewpoint_f"}

# Read straight from the layout so this is a real check and not two copies of
# the same string agreeing with each other. wb_card 1.2+ exposes these; older
# layouts do not, and the fallback makes that visible rather than silent.
LAYOUT = getattr(wb_card, "VERSION", "pre-1.2")
CARD_ATTRIBUTION = getattr(wb_card, "ATTRIBUTION", None)
CARD_FOOTER = getattr(wb_card, "FOOTER_URL", None)


class CardDeclined(RuntimeError):
    """This station cannot be rendered by layout 1.0. Not a crash, a gap."""


# ---------------------------------------------------------------- derivations


def relative_humidity(air_f: float, dew_f: float) -> float:
    """Magnus formula. Dew point above air temperature is clamped, not trusted."""
    t = (air_f - 32) / 1.8
    td = min((dew_f - 32) / 1.8, t)
    a, b = 17.625, 243.04
    return max(0.0, min(100.0,
        100 * math.exp(a * td / (b + td)) / math.exp(a * t / (b + t))))


def heat_index(air_f: float, rh: float) -> float | None:
    """NWS Rothfusz regression. None below the range where it means anything.

    Returning None is correct here, but note that wb_card.hi_color(None)
    paints the accent GREEN, so 'no heat index' and 'comfortable' look
    identical on the card face.
    """
    simple = 0.5 * (air_f + 61.0 + (air_f - 68.0) * 1.2 + rh * 0.094)
    if (simple + air_f) / 2 < 80:
        return None

    t, r = air_f, rh
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * r - 0.22475541 * t * r
          - 0.00683783 * t * t - 0.05481717 * r * r + 0.00122874 * t * t * r
          + 0.00085282 * t * r * r - 0.00000199 * t * t * r * r)
    if r < 13 and 80 <= t <= 112:
        hi -= ((13 - r) / 4) * math.sqrt((17 - abs(t - 95)) / 17)
    elif r > 85 and 80 <= t <= 87:
        hi += ((r - 85) / 10) * ((87 - t) / 5)
    return hi


# ---------------------------------------------------------------- conversion


def _ct(stamp: str | None) -> tuple[str | None, str | None]:
    if not stamp:
        return None, None
    dt = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(CT)
    return f"{dt:%-I:%M %p %Z}", f"{dt:%a %b %-d}"


def payload_from_record(rec: dict, run_dir: Path | None = None) -> dict:
    """Build a wb_card payload, or decline the station with a reason."""
    key = rec.get("key", "?")
    obs = rec.get("obs") or {}
    cam = rec.get("camera") or {}

    if obs.get("status") in (None, "unavailable"):
        raise CardDeclined(
            f"{key}: obs {obs.get('status') or 'missing'}"
            f"{' (' + obs['reason'] + ')' if obs.get('reason') else ''}; "
            f"layout {LAYOUT} has no data-unavailable state"
        )

    blocking = sorted(set(obs.get("missing", [])) & DISPLAYED)
    if blocking:
        raise CardDeclined(f"{key}: missing displayed field(s) {', '.join(blocking)}")

    air, dew = obs.get("air_temp_f"), obs.get("dewpoint_f")
    if air is None or dew is None:
        raise CardDeclined(f"{key}: air_temp_f or dewpoint_f is null")

    rh = relative_humidity(air, dew)
    obs_time, obs_date = _ct(obs.get("valid_utc"))
    cam_time, _ = _ct(cam.get("capture_utc"))

    cam_path = None
    if cam.get("status") in ("ok", "stale") and cam.get("file") and run_dir:
        p = Path(run_dir) / cam["file"]
        # Let the card draw "Camera offline" rather than handing it a dead path.
        cam_path = str(p) if p.exists() else None

    return {
        "county": f"{rec['county']} County",
        "site": rec.get("place") or "location TBD",
        "station_id": rec.get("site_id") or "----",
        "air_f": float(air),
        "dewpoint_f": float(dew),
        "humidity_pct": rh,
        "heat_index_f": heat_index(air, rh),
        "obs_time": obs_time,
        "obs_date": obs_date,
        "obs_age_min": (round(obs["age_minutes"]) if obs.get("age_minutes") is not None
                        else None),
        "camera_path": cam_path,
        "camera_time": cam_time,
    }


# ---------------------------------------------------------------- validation


def validate(payload: dict, rec: dict, expect_attribution: str | None = None,
             expect_footer: str | None = None,
             live: bool = True) -> tuple[list[str], list[str]]:
    """Return (blocking, warnings).

    Blocking means the card would be wrong or misleading, so it must not be
    written. Warnings mean the card is correct but something needs attention.
    Conflating the two is how a stale footer string ends up preventing every
    card in the run from rendering.
    """
    block, warn = [], []
    key = rec.get("key", "?")

    # --- blocking: the card would state something false -------------------
    for field in ("county", "site", "obs_time", "obs_date"):
        if not payload.get(field):
            block.append(f"{key}: {field} is empty")

    # The guard against good constants landing on the wrong source.
    if not payload["county"].startswith(rec.get("county", "\0")):
        block.append(f"{key}: county label {payload['county']!r} disagrees with record")

    for field in ("air_f", "dewpoint_f", "humidity_pct"):
        v = payload.get(field)
        if not isinstance(v, (int, float)) or math.isnan(v):
            block.append(f"{key}: {field} is not a number")

    if isinstance(payload.get("dewpoint_f"), (int, float)) and \
       isinstance(payload.get("air_f"), (int, float)) and \
       payload["dewpoint_f"] > payload["air_f"] + 0.5:
        block.append(f"{key}: dew point above air temperature")

    # A placeholder id on a card you might actually post is misleading, so it
    # blocks on live runs. It does not warn on fixture runs: km_sources owns
    # confirmation status and the orchestrator reports it once for the whole
    # run instead of once per card.
    if payload.get("station_id") in ("----", "XXXX") and live:
        block.append(f"{key}: site_id is a placeholder")

    # --- warnings: card is fine, something else needs fixing --------------
    if CARD_ATTRIBUTION is None or CARD_FOOTER is None:
        warn.append("layout predates 1.2: it does not expose ATTRIBUTION/FOOTER_URL, "
                    "so what it stamps cannot be verified")
    else:
        if expect_attribution and expect_attribution != CARD_ATTRIBUTION:
            warn.append(f"attribution drift: orchestrator {expect_attribution!r} "
                        f"vs layout {CARD_ATTRIBUTION!r}")
        if expect_footer and expect_footer not in CARD_FOOTER:
            warn.append(f"footer drift: orchestrator {expect_footer!r} "
                        f"vs layout {CARD_FOOTER!r}")

    return block, warn


# ---------------------------------------------------------------- render


def _stamp_fixture(path: Path):
    """Band a rendered card as fixture data. Post-processing, not a layout edit."""
    from PIL import Image, ImageDraw
    img = Image.open(path).convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle([0, img.height // 2 - 70, img.width, img.height // 2 + 70],
                fill=(224, 0, 0, 205))
    wb_card.ctext(d, img.width // 2, img.height // 2 - 42,
                  "FIXTURE DATA  NOT FOR POSTING",
                  wb_card.f(wb_card.BOLD, 54), (255, 255, 255))
    img.save(path, "PNG")


def render(rec: dict, out_path, attribution: str | None = None,
           footer: str | None = None, watermark_fixture: bool = False,
           run_dir: Path | None = None) -> tuple[str, list[str]]:
    """Render one card. Returns (path, warnings). Raises CardDeclined only if
    the card would be wrong."""
    out_path = Path(out_path)
    run_dir = Path(run_dir) if run_dir else out_path.parent.parent

    payload = payload_from_record(rec, run_dir)
    block, warn = validate(payload, rec, attribution, footer,
                           live=not watermark_fixture)
    if block:
        raise CardDeclined("; ".join(block))

    wb_card.render(payload, str(out_path))
    if watermark_fixture:
        _stamp_fixture(out_path)
    return str(out_path), warn


def contact_sheet(paths: list[Path], out_path) -> str:
    """One image per review pass instead of five. Cuts the layout loop."""
    from PIL import Image
    cards = [Image.open(p).convert("RGB") for p in paths]
    h = 620
    scaled = [c.resize((round(c.width * h / c.height), h), Image.LANCZOS) for c in cards]
    gap = 14
    sheet = Image.new("RGB", (sum(c.width for c in scaled) + gap * (len(scaled) - 1), h),
                      (230, 241, 244))
    x = 0
    for c in scaled:
        sheet.paste(c, (x, 0))
        x += c.width + gap
    sheet.save(out_path, "PNG")
    return str(out_path)


# ---------------------------------------------------------------- check mode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--run-dir", type=Path)
    args = ap.parse_args()

    if args.run_dir:
        src = json.loads((args.run_dir / "km_data.json").read_text(encoding="utf-8"))
    else:
        src = json.loads((Path(__file__).parent / "km_data_fixture.json")
                         .read_text(encoding="utf-8"))
        # The raw fixture stores relative offsets; only the orchestrator
        # normally resolves them. Do it here so check mode exercises the
        # timestamp and staleness paths instead of reporting empty fields.
        from datetime import timezone
        import run_purchase_cams
        now = datetime.now(timezone.utc)
        run_purchase_cams._resolve_offsets(src["stations"], now)
        run_purchase_cams.apply_staleness(src["stations"], now)

    print(f"stale threshold from wb_card: {STALE_MIN} min\n")
    for rec in src["stations"]:
        key = rec.get("key")
        try:
            p = payload_from_record(rec, args.run_dir)
        except CardDeclined as exc:
            print(f"{key:10s} DECLINED  {exc}")
            continue
        block, warn = validate(p, rec,
                               "Data and camera image: Kentucky Mesonet at WKU",
                               "weatherbrother.com", live=False)
        hi = f"{p['heat_index_f']:.1f}" if p["heat_index_f"] is not None else "none"
        age = p["obs_age_min"]
        pill = " STALE-PILL" if age is not None and age > STALE_MIN else ""
        print(f"{key:10s} {p['air_f']:.1f}F dp{p['dewpoint_f']:.1f} "
              f"rh{p['humidity_pct']:.0f}% hi{hi:>5s} age{age}{pill} "
              f"cam={'yes' if p['camera_path'] else 'offline'}")
        for b in block:
            print(f"{'':10s}   BLOCK {b}")
        for w in warn:
            print(f"{'':10s}   warn  {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
