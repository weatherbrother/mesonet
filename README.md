# mesonet

Purchase-region calm cards for Weather Brother. Reads Kentucky Mesonet
observations and camera stills for the five Jackson Purchase stations, checks
NWS alerts for each county, and renders one card per station plus a contact
sheet.

    cd cards
    python run_purchase_cams.py --root <where output should land>

`--root` is where the flat `cards/` folder and the dated run folder are
written. It defaults to the parent of `cards/`, which is the repo root, and
the `.gitignore` keeps that output out of git.

Flags: `--strict` exits non-zero if any gap was recorded. `--fixture` and
`--offline` are for renderer work and watermark every card FIXTURE DATA; the
scheduled run never uses them.

## Stations

| key | site | county | zone |
| --- | --- | --- | --- |
| ballard | BAND | Ballard | KYC007 |
| marshall | DRFN | Marshall | KYC157 |
| graves | PRYB | Graves | KYC083 |
| calloway | MRRY | Calloway | KYC035 |
| fulton | HCKM | Fulton | KYC075 |

`HCKM` is Fulton County, not Hickman County. Kentucky has a Hickman County and
it has no Mesonet station. McCracken and Carlisle have none either -- the fifth
Purchase site is Marshall.

## Hosts it needs

- `api.weather.gov` -- NWS active alerts by county zone
- `www.kymesonet.org` -- observations, `/api/data/current/{SITE}`
- `d266k7wxhw6o23.cloudfront.net` -- camera stills

Behind an allowlist proxy, all three must be reachable or the run degrades in
ways the summary reports explicitly.

## Layout

`km_sources.py` is the station registry and refuses unconfirmed constants.
`km_fetch_run.py` pulls obs and stills. `km_data.py` normalizes and asserts
units. `km_alerts.py` reads NWS. `calm_card_km.py` adapts a record to the card
payload and validates it. `wb_card.py` is the layout, frozen at 1.2.
`run_purchase_cams.py` is the orchestrator and the only entry point.

Status vocabulary and open items are in `PURCHASE_CAMS_NOTES.md`. The
scheduled-task contract is in `TASK_purchase_cams.md`. Cloud Run
deployment, cost and output layout are in `DEPLOY.md`.
