# Purchase Camera Calm Cards — reference

Not loaded by the scheduled task. For me, when something needs attention.

## Status vocabulary

| status | meaning | action |
| --- | --- | --- |
| `clean` | live data, every station rendered, no gaps | post |
| `partial` | live data, some cards built, something went wrong | read the gaps, probably post |
| `fixture` | running on fixture data, cards watermarked | never post |
| `blocked` | live data, zero cards, nothing usable came back | needs me |

`pending` is work not done yet: modules not installed, site ids not filled in,
stations the layout has no state for. No action.

`no card` lists stations that could not be rendered, grouped by reason. When
all five share a cause it prints once as "all 5 stations".

`gap` is something that went wrong or drifted. Worth reading.

`cameras` reports the camera layer, which works independently of the
observation endpoint. It can be fine while obs are blocked.

## Output

Cards: `Nadocast\cards`, flat, nothing ever deleted. Filenames carry the run
time to the minute, `20260916_1430_01_ballard.png`, so runs never collide. Run
time sorts first, post order second. The summary's `prefix` line gives the set
from that run.

Run data: `Nadocast\<YYYY-MM-DD>-purchase-cams` — raw stills, `km_data.json`,
`manifest.md`, `summary.txt`. Kept.

## Network domains this task needs

Cowork runs behind an allowlist proxy. These must be in allowed domains:

- `api.weather.gov` — NWS active alerts. In.
- `d266k7wxhw6o23.cloudfront.net` — Mesonet camera stills. In, confirmed by a
  live frame on 2026-09-16.
- `www.kymesonet.org` — station observations, `/api/data/current/{SITE}`. In,
  confirmed by a 5/5 live obs run on 2026-09-16.

A `Tunnel connection failed: 403 Forbidden` in the `cameras` line means a
domain is missing from that list, not that the URL is wrong.

## Flags

Daily run needs none. `--root` is pinned in the task spec because the default
derives from the script's location, which is wrong under a session mount.

- `--fixture --offline` — preview cards from fixture data, watermarked
- `--strict` — non-zero exit if any gap was recorded
- `--offline` — no network, fixture alerts, deterministic

## Modules

| file | role |
| --- | --- |
| `km_sources.py` | station registry, refuses unverified constants |
| `km_fetch_run.py` | pulls obs and camera stills |
| `km_data.py` | normalizes to the contract, asserts units |
| `km_alerts.py` | NWS active alerts by county zone |
| `calm_card_km.py` | adapter to the card layout, plus validation |
| `wb_card.py` | card layout, frozen at 1.2 |
| `run_purchase_cams.py` | orchestrator, the only thing the task invokes |

## Open items

- `place` is DONE. All five carry the county name, so every card's second line
  reads `Station <ID>  .  <County>, KY`. The old station descriptors are kept
  in a comment in `km_sources.py` if the face ever wants the specific site.
- Wind and precip are mapped: `WSPD_AVG` mph, `WDIR_AVG` degrees, `PRCP_SUM`
  hundredths of an inch. Every sample so far still reads 0, so the plausibility
  check has never seen a non-zero value. On the first windy or wet day, compare
  one card against the station page — the precip check only catches the unit
  being wrong in the direction that inflates, not the one that shrinks.
- Site ids are DONE. All five confirmed 2026-09-16: ballard `BAND`, marshall
  `DRFN`, graves `PRYB`, calloway `MRRY`, fulton `HCKM`. The fifth county is
  Marshall, not McCracken -- McCracken, Carlisle and Hickman have no Mesonet
  station. `HCKM` is Fulton County, not Hickman County.
- Camera cadence unknown, so the frame scan steps one minute at a time. Once
  the `hit_minute` values in a few runs show a pattern, set `STEP_MINUTES`.
- Camera QC records luminance and spread but judges nothing. After a couple of
  weeks of real lens behaviour, set thresholds for fogged and dark frames.
