#!/usr/bin/env python3
"""
Normalize km_fetch_run output into the km_data.json contract.

The unit contract is the whole point of this module. Mesonet's field names and
units are unknown until you look at one real response, so both live in FIELD_MAP
and SOURCE_UNITS below, in one place, asserted on every value. A silent unit
error is the bug that renders 22F on a July afternoon and looks plausible
enough to post.

FIELD_MAP was filled in on 2026-09-16 from a live PRYB response. The
temperature unit was confirmed against the source itself; the wind and precip
units were supplied by Dallas, because every sample taken so far reads zero for
both and a zero is identical under every unit.
"""

from __future__ import annotations

from datetime import datetime, timezone

# ---------------------------------------------------------------- contract
#
# Left side: our contract field. Right side: the key in Mesonet's payload.
# Set from one real response, then never guessed at again.
FIELD_MAP: dict[str, str | None] = {
    "air_temp": "TAIR_AVG",
    "dewpoint": "DWPT_AVG",
    "rel_humidity": "RELH_AVG",
    # Units given by Dallas 2026-09-16: wind in mph, precip in hundredths of
    # an inch. WSMX_AVG (max wind) has no field in the contract and stays
    # unmapped rather than being folded into wind_speed.
    "wind_dir": "WDIR_AVG",
    "wind_speed": "WSPD_AVG",
    "precip_today": "PRCP_SUM",
    "valid_time": "timestamp",
}

# "C" or "F" for temperature, "mps"/"kph"/"mph" for wind,
# "mm"/"in"/"hundredths_in" for precip.
#
# temp is F, confirmed two independent ways on 2026-09-16 rather than assumed:
#   1. the site's own header read 76F while TAIR_AVG read 75.95978;
#   2. FARM's DWPT_AVG 70.63286 is exactly the Magnus dew point of TAIR_AVG
#      76.58852 at RELH_AVG 81.8, which only holds if both are Fahrenheit.
# wind and precip were given by Dallas on 2026-09-16. Every sample to date
# reads 0 for both, so PLAUSIBLE below has never had a non-zero value to test
# them against. The precip check is one-sided and worth knowing about: reading
# hundredths AS inches inflates by 100x and trips the 20in ceiling, but reading
# inches as hundredths shrinks by 100x and lands inside the range unnoticed. On
# the first real rain, compare a card against the station page before posting.
SOURCE_UNITS = {"temp": "F", "wind": "mph", "precip": "hundredths_in"}

# The payload stamps observations "202609160325" -- compact, no separators, no
# zone. Confirmed UTC on 2026-09-16: a record read at 03:28Z carried 0325.
# datetime.fromisoformat cannot parse this, so the format is declared here once
# instead of being re-derived at each call site.
SOURCE_TIME_FORMAT: str | None = "%Y%m%d%H%M"

# Plausible monthly temperature range for western Kentucky, in F, padded well
# beyond the Paducah record extremes. Used for the comparative unit check
# below, not just as a bounds test.
MONTHLY_F = {
    1: (-25, 82),  2: (-20, 86),  3: (-10, 95),  4: (12, 100),
    5: (25, 103),  6: (35, 110),  7: (40, 115),  8: (38, 115),
    9: (34, 110), 10: (14, 100), 11: (0, 92),   12: (-20, 82),
}

# Plausible ranges for western Kentucky, after conversion. A value outside
# these is a unit or parsing error, not weather.
PLAUSIBLE = {
    "air_temp_f": (-30.0, 125.0),
    "dewpoint_f": (-40.0, 90.0),
    "wind_speed_mph": (0.0, 120.0),
    "wind_dir_deg": (0.0, 360.0),
    "precip_since_midnight_in": (0.0, 20.0),
}


class UnitError(ValueError):
    """A converted value landed outside anything western Kentucky can produce."""


def _to_f(v, unit):
    return v * 9 / 5 + 32 if unit == "C" else v


def _temp_to_f_checked(raw: float, unit: str, month: int, what: str) -> float:
    """Convert, and refuse when the OTHER unit is the only plausible reading.

    A pure bounds test cannot catch a C/F swap, because a metric 32.1 read as
    Fahrenheit is 32.1F, which is inside any sane absolute range. So compare
    both interpretations against the month: if the configured one is impossible
    for this month and the alternative is fine, the configuration is wrong.
    """
    lo, hi = MONTHLY_F[month]
    as_configured = _to_f(raw, unit)
    other = "F" if unit == "C" else "C"
    as_other = _to_f(raw, other)

    fits_configured = lo <= as_configured <= hi
    fits_other = lo <= as_other <= hi

    if not fits_configured and fits_other:
        raise UnitError(
            f"{what} raw {raw} as {unit} gives {as_configured:.1f}F, outside the "
            f"month {month} range {lo}..{hi}, but as {other} gives "
            f"{as_other:.1f}F which fits. SOURCE_UNITS['temp'] is probably wrong")
    if not fits_configured and not fits_other:
        raise UnitError(
            f"{what} raw {raw} is implausible under either unit "
            f"({as_configured:.1f}F / {as_other:.1f}F) for month {month} "
            f"range {lo}..{hi}")
    return as_configured


def _to_mph(v, unit):
    try:
        return {"mps": 2.236936, "kph": 0.621371, "mph": 1.0}[unit] * v
    except KeyError:
        raise UnitError(
            f"SOURCE_UNITS['wind'] is {unit!r}; expected 'mps', 'kph' or "
            f"'mph'.") from None


def _to_in(v, unit):
    if unit == "mm":
        return v / 25.4
    if unit == "hundredths_in":
        return v / 100.0
    if unit == "in":
        return v
    raise UnitError(
        f"SOURCE_UNITS['precip'] is {unit!r}; expected 'mm', 'in' or "
        f"'hundredths_in'.")


def _parse_valid_time(stamp) -> datetime | None:
    """Payload stamp -> aware UTC datetime, or None when absent.

    Declared-format first, ISO as the fallback, so a source that changes its
    stamp shape fails here with a readable error instead of silently landing
    the observation on the wrong day.
    """
    if stamp in (None, ""):
        return None
    s = str(stamp)
    dt = (datetime.strptime(s, SOURCE_TIME_FORMAT) if SOURCE_TIME_FORMAT
          else datetime.fromisoformat(s.replace("Z", "+00:00")))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dewpoint_from_rh(air_f: float, rh: float) -> float:
    import math
    t = (air_f - 32) / 1.8
    a, b = 17.625, 243.04
    g = math.log(max(rh, 0.1) / 100) + a * t / (b + t)
    return (b * g / (a - g)) * 1.8 + 32


def _check(name: str, value):
    lo, hi = PLAUSIBLE[name]
    if not (lo <= value <= hi):
        raise UnitError(f"{name}={value:.2f} outside plausible {lo}..{hi}; "
                        f"check SOURCE_UNITS and FIELD_MAP")
    return value


def normalize(raw: list[dict]) -> list[dict]:
    """Raw fetch records -> contract records. Never raises; per-station status."""
    out = []
    for r in raw:
        rec = {"key": r["key"], "site_id": r.get("site_id"),
               "county": r.get("county"), "place": r.get("place")}

        # --- camera passes straight through, it is already in contract shape
        cam = r.get("camera_raw", {})
        rec["camera"] = {
            "status": cam.get("status", "unavailable"),
            # Written by km_fetch_run from the frame's own filename, which is
            # authoritative in a way a CDN header is not.
            "capture_utc": cam.get("capture_utc"),
            "file": cam.get("file"),
        }
        for k in ("reason", "qc", "scan", "source_url"):
            if cam.get(k):
                rec["camera"][k] = cam[k]

        # --- observations
        ob = r.get("obs_raw", {})
        if ob.get("status") != "ok":
            rec["obs"] = {"status": "unavailable",
                          "reason": ob.get("reason", "no payload")}
            out.append(rec)
            continue
        if not FIELD_MAP.get("air_temp"):
            rec["obs"] = {"status": "unavailable",
                          "reason": "FIELD_MAP not configured"}
            out.append(rec)
            continue

        p = ob["payload"]
        obs: dict = {"status": "ok"}
        missing = []
        try:
            air_raw = p.get(FIELD_MAP["air_temp"])
            if air_raw is None:
                raise UnitError("air_temp absent from payload")
            vt_dt = _parse_valid_time(p.get(FIELD_MAP.get("valid_time")))
            month = vt_dt.month if vt_dt else datetime.now(timezone.utc).month
            obs["air_temp_f"] = _check("air_temp_f", _temp_to_f_checked(
                float(air_raw), SOURCE_UNITS["temp"], month, "air_temp"))

            if FIELD_MAP.get("dewpoint") and p.get(FIELD_MAP["dewpoint"]) is not None:
                obs["dewpoint_f"] = _check("dewpoint_f", _temp_to_f_checked(
                    float(p[FIELD_MAP["dewpoint"]]), SOURCE_UNITS["temp"],
                    month, "dewpoint"))
            elif FIELD_MAP.get("rel_humidity") and p.get(FIELD_MAP["rel_humidity"]) is not None:
                obs["dewpoint_f"] = _check("dewpoint_f", _dewpoint_from_rh(
                    obs["air_temp_f"], float(p[FIELD_MAP["rel_humidity"]])))
            else:
                raise UnitError("neither dewpoint nor rel_humidity in payload")

            for cf, key, conv, unit in (
                ("wind_speed_mph", "wind_speed", _to_mph, "wind"),
                ("precip_since_midnight_in", "precip_today", _to_in, "precip"),
            ):
                src = FIELD_MAP.get(key)
                if src and p.get(src) is not None:
                    obs[cf] = _check(cf, conv(float(p[src]), SOURCE_UNITS[unit]))
                else:
                    obs[cf] = None
                    missing.append(cf)

            wd = FIELD_MAP.get("wind_dir")
            if wd and p.get(wd) is not None:
                obs["wind_dir_deg"] = _check("wind_dir_deg", float(p[wd]))
            else:
                obs["wind_dir_deg"] = None
                missing.append("wind_dir_deg")

            if vt_dt:
                obs["valid_utc"] = vt_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                raise UnitError("valid_time absent; refusing to stamp an "
                                "observation with the current clock")

            if missing:
                obs["status"] = "partial"
                obs["missing"] = missing
        except (UnitError, ValueError, TypeError) as exc:
            obs = {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}

        rec["obs"] = obs
        out.append(rec)
    return out
