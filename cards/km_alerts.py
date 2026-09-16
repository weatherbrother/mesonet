"""
Active NWS alerts by Purchase county, from api.weather.gov.

This is the layer that drives the card's tone. Numbers do not decide whether a
card looks calm. An active warning does. A quiet card during a warning is the
opposite of what the brand says.

Public domain federal source, already on the network allowlist, no permission
question anywhere in it. Runs today.

Usage:
    python km_alerts.py                 # print a table for all five
    from km_alerts import fetch_alerts  # -> dict keyed by station key
"""

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from km_sources import active_stations

UA = "WeatherBrother/1.0 (dallasjmckinney@gmail.com)"
BASE = "https://api.weather.gov/alerts/active"
TIMEOUT = 20

# Anything in this set means the card does not post as a calm card.
SUPPRESS_CALM = {
    "Tornado Warning",
    "Tornado Watch",
    "Severe Thunderstorm Warning",
    "Flash Flood Warning",
    "Flood Warning",
    "Ice Storm Warning",
    "Blizzard Warning",
    "Winter Storm Warning",
    "High Wind Warning",
}


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/geo+json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def fetch_zone_alerts(zone: str) -> dict:
    """Return a status record for one county zone. Never raises."""
    try:
        data = _get(f"{BASE}?zone={zone}")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError) as exc:
        # Unavailable is not the same as quiet. Downstream must not read this
        # as "no alerts."
        return {"status": "unavailable", "reason": str(exc), "active": []}

    active = []
    for feat in data.get("features", []):
        p = feat.get("properties", {})
        active.append({
            "event": p.get("event"),
            "severity": p.get("severity"),
            "onset": p.get("onset"),
            "ends": p.get("ends") or p.get("expires"),
            "headline": p.get("headline"),
        })

    return {
        "status": "ok",
        "checked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "active": active,
        "suppress_calm": any(a["event"] in SUPPRESS_CALM for a in active),
    }


def fetch_alerts(allow_unverified: bool = True) -> dict[str, dict]:
    """Alerts for every station in post order, keyed by station key."""
    out = {}
    for st in active_stations(allow_unverified=allow_unverified):
        out[st.key] = fetch_zone_alerts(st.zone)
    return out


if __name__ == "__main__":
    results = fetch_alerts()
    for key, rec in results.items():
        if rec["status"] != "ok":
            print(f"{key:10s} UNAVAILABLE  {rec['reason'][:60]}")
            continue
        if not rec["active"]:
            print(f"{key:10s} quiet")
            continue
        flag = "SUPPRESS CALM" if rec["suppress_calm"] else "advisory only"
        events = "; ".join(a["event"] for a in rec["active"])
        print(f"{key:10s} {flag:14s} {events}")
