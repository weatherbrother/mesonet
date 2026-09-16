#!/usr/bin/env python3
"""
Upload a run's output to the Mesonet Google shared drive.

Why a SHARED drive and not a My Drive folder: a service account has no storage
quota and cannot own files, so uploading into a personal folder fails with
403 storageQuotaExceeded however that folder is shared. Files in a shared drive
are owned by the drive, which is Google's documented fix. Every call below
passes supportsAllDrives=True; without it the API behaves as though shared
drives do not exist and returns a confusing 404 on a folder that is plainly
there.

Credentials never live in this repo, which is public. The service account JSON
arrives at runtime from one of:

    GOOGLE_SERVICE_ACCOUNT_JSON    the raw JSON, for a cloud run
    GOOGLE_APPLICATION_CREDENTIALS a path to the key file, for a local run

and the destination from DRIVE_FOLDER_ID. A missing credential or id is a GAP,
not a crash: the cards are already written to disk and the run still reports.
Failing the whole run because the copy step could not authenticate would turn a
delivery problem into a data problem.

    python km_drive.py --run-dir DIR --cards-dir DIR --prefix 20260916_1430_
    python km_drive.py ... --dry-run     list what would go up, no credentials
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

# Full drive scope rather than drive.file. drive.file limits the app to files
# it created, which makes find-or-create for the day folder fragile: a folder
# made by hand would be invisible and the run would silently create a second
# one beside it, every hour, forever. The service account is a member of
# exactly one shared drive, so its reach is bounded by that membership rather
# than by the scope string.
SCOPES = ["https://www.googleapis.com/auth/drive"]

# Text artifacts from the dated run folder. Small, and they are the record of
# what the run actually saw. summary.txt is deliberately NOT here: it is the
# last thing the orchestrator writes, after the drive line has already been
# emitted, so including it would mean either uploading a file that does not
# exist yet or reporting an upload the summary cannot describe. Its content
# reaches Dallas by notification, and manifest.md plus km_data.json carry the
# provenance and the numbers.
RUN_FILES = ("manifest.md", "km_data.json")

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".json": "application/json",
        ".md": "text/markdown", ".txt": "text/plain"}


class DriveUnavailable(RuntimeError):
    """Configuration is absent or unusable. Reported as a gap, never raised out."""


def _credentials():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    try:
        from google.oauth2 import service_account
    except ModuleNotFoundError as exc:
        raise DriveUnavailable(
            "google-auth is not installed (pip install google-auth "
            "google-api-python-client)") from exc

    if raw:
        try:
            info = json.loads(raw)
        except json.JSONDecodeError as exc:
            # Do not echo the value. A malformed key in a log is still a key.
            raise DriveUnavailable(
                f"GOOGLE_SERVICE_ACCOUNT_JSON is set but is not valid JSON "
                f"({exc.msg} at position {exc.pos})") from None
        return service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES)
    if path:
        if not Path(path).exists():
            raise DriveUnavailable(
                f"GOOGLE_APPLICATION_CREDENTIALS points at {path}, which does "
                f"not exist")
        return service_account.Credentials.from_service_account_file(
            path, scopes=SCOPES)
    # Running ON Google Cloud, the job assumes its own service account through
    # the metadata server and there is no key file anywhere. This is the
    # preferred path by a wide margin: a key that does not exist cannot leak,
    # cannot be committed to a public repo, and cannot expire unnoticed.
    try:
        import google.auth
        creds, _ = google.auth.default(scopes=SCOPES)
        return creds
    except Exception:                                 # noqa: BLE001
        pass

    raise DriveUnavailable(
        "no credentials: on Google Cloud the job's own service account is used "
        "automatically; elsewhere set GOOGLE_SERVICE_ACCOUNT_JSON or "
        "GOOGLE_APPLICATION_CREDENTIALS")


def _service():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_credentials(),
                 cache_discovery=False)


def collect(run_dir: Path, cards_dir: Path, prefix: str) -> list[Path]:
    """Exactly this run's output: the stamped cards plus the run's text record.

    The prefix is what makes this safe. The cards folder is flat and keeps
    every run, so selecting by prefix uploads today's set and cannot sweep up
    yesterday's.
    """
    out = [p for p in sorted(Path(cards_dir).glob(f"{prefix}*.png"))]
    out += [Path(run_dir) / n for n in RUN_FILES if (Path(run_dir) / n).exists()]
    return out


def day_from_run_dir(run_dir) -> str:
    """The run folder is named <YYYY-MM-DD>-purchase-cams. Take the day from
    there rather than re-deriving it from the clock: two sources for the same
    date is how a run lands in yesterday's folder at one minute past midnight."""
    return Path(run_dir).name[:10]


def _day_folder(svc, drive_id: str, day: str) -> str:
    """Find or create the day's folder at the root of the shared drive.

    Drive permits duplicate names, so a create that should have been a find
    does not error -- it quietly makes a second folder. At 24 runs a day that
    compounds fast, which is why this looks first and why the scope above is
    wide enough for the look to succeed.
    """
    q = (f"name = '{day}' and mimeType = 'application/vnd.google-apps.folder' "
         f"and '{drive_id}' in parents and trashed = false")
    found = svc.files().list(
        q=q, corpora="drive", driveId=drive_id,
        includeItemsFromAllDrives=True, supportsAllDrives=True,
        fields="files(id,name)", pageSize=2).execute().get("files", [])
    if found:
        return found[0]["id"]
    return svc.files().create(
        body={"name": day, "mimeType": "application/vnd.google-apps.folder",
              "parents": [drive_id]},
        fields="id", supportsAllDrives=True).execute()["id"]


def upload(paths: list[Path], folder_id: str | None = None,
           day: str | None = None) -> list[dict]:
    """Upload each file, into a day subfolder when `day` is given.

    Returns one record per file. Never raises: the cards are already on disk.
    """
    folder_id = folder_id or os.environ.get("DRIVE_FOLDER_ID", "").strip()
    if not folder_id:
        return [{"file": p.name, "status": "skipped",
                 "reason": "DRIVE_FOLDER_ID not set"} for p in paths]
    try:
        svc = _service()
    except DriveUnavailable as exc:
        return [{"file": p.name, "status": "skipped", "reason": str(exc)}
                for p in paths]

    parent = folder_id
    if day:
        try:
            parent = _day_folder(svc, folder_id, day)
        except Exception as exc:                      # noqa: BLE001
            # Fall back to the drive root. A card in the wrong folder is
            # recoverable; a card that was never uploaded is not.
            return [{"file": p.name, "status": "failed",
                     "reason": f"could not open day folder {day!r}: "
                               f"{type(exc).__name__}: {exc}"} for p in paths]

    from googleapiclient.http import MediaFileUpload
    records = []
    for p in paths:
        rec = {"file": p.name}
        try:
            media = MediaFileUpload(
                str(p), mimetype=MIME.get(p.suffix, "application/octet-stream"),
                resumable=True)
            got = svc.files().create(
                body={"name": p.name, "parents": [parent]},
                media_body=media, fields="id,name,webViewLink",
                supportsAllDrives=True).execute()
            rec.update(status="ok", id=got["id"], link=got.get("webViewLink"))
        except Exception as exc:                      # noqa: BLE001
            # One unusable file must not stop the other four.
            rec.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
        records.append(rec)
    return records


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--cards-dir", type=Path, required=True)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--folder-id")
    ap.add_argument("--day", help="subfolder name; default is the run dir's date")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    day = a.day or day_from_run_dir(a.run_dir)

    paths = collect(a.run_dir, a.cards_dir, a.prefix)
    if not paths:
        print(f"nothing matched prefix {a.prefix!r} in {a.cards_dir}")
        return 0
    if a.dry_run:
        print(f"would upload {len(paths)} file(s) to "
              f"{a.folder_id or os.environ.get('DRIVE_FOLDER_ID') or '<unset>'}"
              f" / {day}:")
        for p in paths:
            print(f"  {p.name:44s} {p.stat().st_size/1024:8.0f} KB")
        return 0
    for r in upload(paths, a.folder_id, day=day):
        note = r.get("link") or r.get("reason", "")
        print(f"{r['status']:8s} {r['file']:44s} {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
