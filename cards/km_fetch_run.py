#!/usr/bin/env python3
"""
Fetch Kentucky Mesonet camera stills and observations for the Purchase sites.

CAMERAS: wired. Stills come from the Mesonet CloudFront distribution at
    /camera/{SITE}/{SITE}_{YYYYMMDD}_{HHMM}.jpg
with the stamp in UTC. There is no "latest" alias, so the current frame is
found by walking backward a minute at a time from now until one exists. The
capture time comes from the filename, which is authoritative in a way that a
CDN Last-Modified header is not.

If DevTools shows a latest-frame alias, put it in LATEST_URL and the walk is
skipped entirely.

OBSERVATIONS: wired. /api/data/current/{SITE} on www.kymesonet.org returns the
station's current record as flat JSON. Confirmed 2026-09-16 by watching the
station page's own XHR for PRYB, not by guessing at a URL shape.

    python km_fetch_run.py --out /tmp/run          live
    python km_fetch_run.py --out /tmp/run --mock mockdata
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import km_sources

# ---------------------------------------------------------------- endpoints

CAMERA_URL = ("https://d266k7wxhw6o23.cloudfront.net"
              "/camera/{site}/{site}_{stamp}.jpg")
STAMP_FMT = "%Y%m%d_%H%M"          # UTC, confirmed against a known frame
LATEST_URL: str | None = None      # set if a latest-frame alias exists
OBS_URL: str | None = ("https://www.kymesonet.org"
                       "/api/data/current/{site}")

LOOKBACK_MINUTES = 90              # give up past this and report unavailable
STEP_MINUTES = 1                   # until the cadence is known, check every minute

UA = "WeatherBrother/1.0 (+https://weatherbrother.com; dallasjmckinney@gmail.com)"
TIMEOUT = 25
RETRIES = 3
BACKOFF = 2.0

MIN_IMAGE_BYTES = 5_000
MIN_IMAGE_WIDTH = 320


# ---------------------------------------------------------------- transport


class Transport:
    def _req(self, url, method, since=None):
        headers = {"User-Agent": UA}
        if since:
            headers["If-Modified-Since"] = since
        return urllib.request.Request(url, headers=headers, method=method)

    def head(self, url: str) -> bool:
        """True if the object exists, False on a 404.

        Transport failures RAISE rather than returning False. An unreachable
        host and an absent object are different problems with different fixes,
        and collapsing them means scanning 90 timeouts before reporting the
        wrong conclusion.
        """
        try:
            with urllib.request.urlopen(self._req(url, "HEAD"), timeout=TIMEOUT) as r:
                return r.status == 200
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                # CloudFront returns 403 for a missing object when the bucket
                # disallows listing, so 403 cannot simply raise. The scan
                # counts them separately instead: an all-403 result is more
                # likely a blocked host than 91 absent frames.
                self.last_code = exc.code
                return False
            raise

    def get(self, url: str, since: str | None = None):
        with urllib.request.urlopen(self._req(url, "GET", since),
                                    timeout=TIMEOUT) as r:
            return r.status, r.read(), dict(r.headers)


class MockTransport:
    """<dir>/obs_<SITE>.json and <dir>/cam_<SITE>.jpg. Stamp is ignored, so the
    backward walk resolves on its first try."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _resolve(self, url: str) -> Path | None:
        name = url.rsplit("/", 1)[-1]
        if name.endswith(".json"):
            return self.root / name
        site = name.split("_")[0]
        p = self.root / f"cam_{site}.jpg"
        return p if p.exists() else None

    def head(self, url: str) -> bool:
        p = self._resolve(url)
        return bool(p and p.exists())

    def get(self, url: str, since: str | None = None):
        p = self._resolve(url)
        if not p or not p.exists():
            raise urllib.error.HTTPError(url, 404, "not in mock dir", {}, None)
        return 200, p.read_bytes(), {}


def _get_with_retry(tp, url: str, since: str | None = None):
    last = None
    for attempt in range(RETRIES):
        try:
            return tp.get(url, since)
        except urllib.error.HTTPError as exc:
            if exc.code in (304, 404):
                raise
            last = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        if attempt < RETRIES - 1:
            time.sleep(BACKOFF ** attempt)
    raise last


# ---------------------------------------------------------------- frame search


def find_latest_frame(tp, site: str, now: datetime) -> dict:
    """Walk backward from now until a frame exists.

    Returns {'url', 'capture_utc', 'scanned'} or {'scanned', 'reason'}.
    The scan count and the hit's minute-of-hour are recorded so the publish
    cadence becomes visible from real runs instead of being guessed at.
    """
    if LATEST_URL:
        url = LATEST_URL.format(site=site)
        if tp.head(url):
            return {"url": url, "capture_utc": None, "scanned": 1,
                    "via": "latest-alias"}

    host = CAMERA_URL.split("/")[2]
    base = now.replace(second=0, microsecond=0)
    scanned = 0
    codes: dict[int, int] = {}
    for m in range(0, LOOKBACK_MINUTES + 1, STEP_MINUTES):
        dt = base - timedelta(minutes=m)
        url = CAMERA_URL.format(site=site, stamp=dt.strftime(STAMP_FMT))
        scanned += 1
        try:
            hit = tp.head(url)
            code = getattr(tp, "last_code", None)
            if code:
                codes[code] = codes.get(code, 0) + 1
        except Exception as exc:
            # Cannot reach the host at all. Stop now: the remaining probes
            # would fail identically and "no frame" would be a wrong answer.
            text = str(exc)
            if "Tunnel connection failed" in text or "ProxyError" in text:
                why = (f"proxy refused a tunnel to {host}. Add {host} to the "
                       f"allowed domains in your network settings")
            else:
                why = f"cannot reach {host} ({type(exc).__name__}: {text})"
            return {"scanned": scanned, "reason": why, "host": host}
        if hit:
            return {"url": url, "scanned": scanned, "via": "scan",
                    "capture_utc": dt.strftime("%Y-%m-%dT%H:%M:00Z"),
                    "hit_minute": dt.minute, "age_minutes": m}
    if codes.get(403, 0) == scanned and scanned > 1:
        # Every single probe forbidden and not one 404. A live CDN serving a
        # real prefix would return 404 for at least some absent stamps.
        return {"scanned": scanned, "host": host,
                "reason": f"all {scanned} probes returned 403 from {host} and "
                          f"none returned 404, which usually means the host is "
                          f"blocked rather than the frames being absent. Check "
                          f"that {host} is in your allowed domains"}
    seen = ", ".join(f"{n}x{c}" for c, n in sorted(codes.items()))
    return {"scanned": scanned, "host": host,
            "reason": f"no frame in the last {LOOKBACK_MINUTES} min "
                      f"({host} reachable, {seen or 'no responses'}) -- check "
                      f"the site_id and the CAMERA_URL filename pattern"}


# ---------------------------------------------------------------- image QC


def inspect_image(path: Path, prev: Path | None) -> dict:
    """Diagnostics, not verdicts. Only unambiguous failures are flagged;
    luminance and spread are recorded so thresholds can be set from real lens
    behaviour rather than a guess about what fog looks like."""
    out: dict = {"bytes": path.stat().st_size}
    if out["bytes"] < MIN_IMAGE_BYTES:
        out["verdict"] = "too_small"
        return out
    try:
        from PIL import Image, ImageStat
        with Image.open(path) as im:
            im.load()
            out["size"] = list(im.size)
            if im.width < MIN_IMAGE_WIDTH:
                out["verdict"] = "too_narrow"
                return out
            st = ImageStat.Stat(im.convert("L"))
            out["mean_luminance"] = round(st.mean[0], 1)
            out["stddev"] = round(st.stddev[0], 1)
    except Exception as exc:
        out["verdict"] = "undecodable"
        out["reason"] = f"{type(exc).__name__}: {exc}"
        return out

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    out["sha256"] = digest[:16]
    if prev and prev.exists() and \
       hashlib.sha256(prev.read_bytes()).hexdigest() == digest:
        out["verdict"] = "unchanged"
        return out
    out["verdict"] = "ok"
    return out


# ---------------------------------------------------------------- fetch


def fetch_station(tp, st, raw_dir: Path, prev_dir: Path | None,
                  now: datetime) -> dict:
    """Never raises. Returns one station's raw record."""
    site = st.site_id
    rec: dict = {"key": st.key, "site_id": site, "county": st.county,
                 "place": st.place}

    if not site:
        rec["obs_raw"] = {"status": "unavailable", "reason": "site_id unknown"}
        rec["camera_raw"] = {"status": "unavailable", "reason": "site_id unknown"}
        return rec

    # --- observations -----------------------------------------------------
    if not OBS_URL:
        rec["obs_raw"] = {"status": "unavailable", "reason": "OBS_URL not configured"}
    else:
        try:
            _, body, _ = _get_with_retry(tp, OBS_URL.format(site=site))
            rec["obs_raw"] = {"status": "ok", "payload": json.loads(body)}
        except Exception as exc:
            rec["obs_raw"] = {"status": "unavailable",
                              "reason": f"{type(exc).__name__}: {exc}"}

    # --- camera -----------------------------------------------------------
    found = find_latest_frame(tp, site, now)
    if "url" not in found:
        rec["camera_raw"] = {"status": "unavailable", "reason": found["reason"],
                             "scan": {"scanned": found["scanned"]}}
        return rec

    dest = raw_dir / f"{st.key}.jpg"
    try:
        _, body, headers = _get_with_retry(tp, found["url"])
        dest.write_bytes(body)
        qc = inspect_image(dest, (prev_dir / f"{st.key}.jpg") if prev_dir else None)
        rec["camera_raw"] = {
            "status": "ok" if qc["verdict"] in ("ok", "unchanged") else "unavailable",
            "file": f"raw/{dest.name}",
            "capture_utc": found.get("capture_utc"),
            "source_url": found["url"],
            "scan": {k: found[k] for k in
                     ("scanned", "via", "hit_minute", "age_minutes") if k in found},
            "qc": qc,
        }
        if qc["verdict"] not in ("ok", "unchanged"):
            rec["camera_raw"]["reason"] = qc["verdict"]
    except Exception as exc:
        rec["camera_raw"] = {"status": "unavailable",
                             "reason": f"{type(exc).__name__}: {exc}"}
    return rec


def fetch_all(raw_dir: Path, tp=None, prev_dir: Path | None = None,
              allow_unverified: bool = True) -> list[dict]:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    tp = tp or Transport()
    now = datetime.now(timezone.utc)
    return [fetch_station(tp, st, raw_dir, prev_dir, now)
            for st in km_sources.active_stations(allow_unverified=allow_unverified)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--mock", type=Path)
    ap.add_argument("--prev", type=Path, help="previous raw dir, for frame-diff")
    args = ap.parse_args()

    global OBS_URL
    tp = None
    if args.mock:
        tp = MockTransport(args.mock)
        OBS_URL = "mock://obs_{site}.json"

    raw = fetch_all(args.out / "raw", tp=tp, prev_dir=args.prev)
    (args.out / "fetch_raw.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")
    for r in raw:
        c = r["camera_raw"]
        scan = c.get("scan", {})
        note = (f"scanned {scan.get('scanned')} -> :{scan.get('hit_minute'):02d} "
                f"({scan.get('age_minutes')}m old) lum={c['qc'].get('mean_luminance')}"
                if "qc" in c and "hit_minute" in scan else c.get("reason", ""))
        print(f"{r['key']:10s} obs={r['obs_raw']['status']:12s} "
              f"cam={c['status']:12s} {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
