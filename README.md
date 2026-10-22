# Harpenden driveway detection PoC

A local command-line tool for finding existing street imagery, suggesting driveway materials, reviewing the correct property/address, and exporting reviewed likely resin-bound leads. There is no hosted dashboard.

**Ready for client testing.** All 97 offline tests pass. Real-world resin accuracy remains unproven: the saved studies have no human approvals or eligible leads. Manual verification is part of this evaluation.

## 1. Install

Use Windows PowerShell with Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/) installed. Obtain the repository as a ZIP or clone it with your own authorized GitHub account:

```powershell
git clone https://github.com/jawadengineer12/Harpenden-AI-Driveway-Detection.git
cd Harpenden-AI-Driveway-Detection
uv sync --locked
uv run --no-sync python -m driveway_poc --help
uv run --no-sync python -m unittest discover -s tests -v
```

For a ZIP, extract it and open PowerShell in the folder containing `pyproject.toml` instead of running the first two commands. Expected test result: `Ran 97 tests` and `OK`. Run all following commands from this folder.

## 2. Choose your starting point

**Review the prepared pilot first (recommended).** Ask us for the permitted handover data. A repository clone/ZIP does not include `data/`, `outputs/`, or `.env`: these are deliberately Git-ignored. The pilot handover needs:

- `data/config.json` and `data/poc.sqlite3`: the frozen ten-property pilot.
- `outputs/images/`: downloaded JPEGs and matching provenance JSON files.
- Saved classification JSON files and `outputs/pilot_delivery_v2/` for reference.

For the separate Hillside exploration, also obtain `data/hillside_config.json`, `data/hillside.sqlite3`, and the exact classified images in `outputs/hillside_evidence/`. Its five address candidates are exploratory, not five completed property discovery searches.

Keep the folder structure. Regenerate galleries after moving the project: old galleries refer to image locations on the original computer. Transfer credentials separately through an agreed secure channel, never Git or chat.

**For a new property sample**, complete section 3, then follow section 5 before reviewing results.

## 3. Local setup

Preserve handed-over files. Create a local credentials file only if missing:

```powershell
New-Item -ItemType Directory -Force data, outputs | Out-Null
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

For live requests, edit `.env` locally with client-controlled credentials:

```dotenv
MAPILLARY_ACCESS_TOKEN=your_mapillary_access_token
OS_PLACES_API_KEY=your_os_places_key
GEMINI_API_KEY=your_gemini_key
STREET_VIEW_API=your_google_street_view_static_api_key
```

Mapillary or Google Street View supplies imagery (two interchangeable providers, see section 6), OS Places supplies address candidates, and Gemini classifies images. Keys can stay empty for review/export of existing downloaded and classified evidence. Python does not load `.env` itself; uv loads it in the commands below.

Paste this helper once per PowerShell session to shorten the commands:

```powershell
function poc { uv run --no-sync --env-file .env python -m driveway_poc @args }
poc status
```

By default, `poc` uses `data/config.json` and `data/poc.sqlite3`. `status` reports readiness and key presence, never key values, and makes no API calls. Missing keys are expected for offline review. If configuration has not been supplied, create a fresh study in section 5. To inspect the unapproved example instead, use `poc --config config.example.json status`.

Configuration records the agreed area, client account ownership, image age, request budget and source-use permissions. The handed-over pilot uses a 730-day maximum image age. Approved permission records require actual evidence, reviewer and date; a working API key does not establish permission. Complete account setup together before the first live run; see [setup responsibilities](docs/SETUP.md).

## 4. Review the prepared pilot and export

These commands use saved evidence and make no paid API calls. Use new output names on each run; existing galleries, review sheets and delivery directories are protected against overwrite.

```powershell
poc review-gallery --images outputs/images --output outputs/client_pilot_review.html
poc review-template --output outputs/client_pilot_review.csv
```

Open `outputs/client_pilot_review.html` in your browser. Each image appears once, with tentative driveway boxes (`d1`, `d2`, etc.) and nearby address candidates. Assess the image before expanding the optional model suggestions. Nearby addresses are not verified house-to-driveway matches.

Open `outputs/client_pilot_review.csv` in a CSV editor. In Excel, import image IDs and UPRNs as **text** so long identifiers are not rounded. Edit only these six columns:

| Column | What to enter |
|---|---|
| `visibility` | `usable`, `obstructed`, `unclear`, or `not_visible` |
| `property_match` | `verified`, `ambiguous`, or `wrong_property` |
| `address_verified` | `yes` or `no` |
| `reviewer` | Your name |
| `reviewed_on` | Actual review date, `YYYY-MM-DD` |
| `evidence` | Which driveway/property you checked and how you verified the address |

Leave all six blank for pending rows; partial reviews are rejected. Preserve every other column and identifier. Save as UTF-8 CSV, then import:

```powershell
poc import-review outputs/client_pilot_review.csv
```

Record the **separate material decision** for each classified driveway you actually reviewed. Replace every uppercase placeholder below, including the detection ID. Do not enter a positive label unless the review supports it:

```powershell
poc review-material PROPERTY_ID IMAGE_ID --detection-id DETECTION_ID --class REVIEWED_CLASS --reviewer "YOUR NAME" --date YYYY-MM-DD --evidence "Describe the selected surface, material cues and uncertainty"
```

`REVIEWED_CLASS` is `likely_resin_bound`, `other`, `unclear`, or `no_visible_driveway`. Select a detection that exists in the saved classification. With no detection, there is no region to approve; leave the case excluded. One detection cannot be assigned to two properties. The scene-wide model summary never substitutes for selecting the correct driveway.

Export after review, or earlier to inspect exclusion reasons:

```powershell
poc export-results --output outputs/client_delivery_01
```

| File | Meaning |
|---|---|
| `leads.csv` | Eligible reviewed likely resin-bound properties, one row each, with verified addresses |
| `qa.csv` | Every candidate property, including missing evidence and exclusion reasons |
| `summary.json` | Attempted, excluded and exported counts; accuracy limitations |

**A header-only `leads.csv` is valid.** Unreviewed, ambiguous, stale, unknown-date, unsupported panorama and non-positive cases stay excluded. Current samples are expected to produce zero leads until adequate evidence and actual reviews exist. Visual assessment does not physically confirm the installed material.

For Hillside, put its configuration/database options **before** each command:

```powershell
poc --config data/hillside_config.json --db data/hillside.sqlite3 review-gallery --images outputs/hillside_evidence --output outputs/client_hillside_review.html
```

Use those same options for its review-template, import-review, review-material and export-results commands. Keep the studies separate.

## 5. Optional: run a new property sample

This uses live services and may consume provider credits. Reuse successful artifacts rather than repeating requests. Existing licensed imagery is required; supplied/newly taken photos do not replace discovery in this PoC.

Create a separate configuration so the original pilot stays intact:

```powershell
if (!(Test-Path data/client_config.json)) { Copy-Item config.example.json data/client_config.json }
```

Edit `data/client_config.json`: set the agreed `bbox` as `[west, south, east, north]`, `client_owned_account`, `max_image_age_days`, bounded `max_requests`, and the relevant permission records. Each approved record needs `evidence`, `reviewed_by` and `reviewed_on`. Lead export requires all seven permissions. Scope is 10-20 attempted properties; a 1-9 property smoke test is also accepted.

Replace the helper so all following commands use a separate database:

```powershell
function poc { uv run --no-sync --env-file .env python -m driveway_poc --config data/client_config.json --db data/client.sqlite3 @args }
poc status
```

Create `data/client_candidates.csv` using the headers in `examples/candidates.csv`:

```csv
property_id,street,latitude,longitude,property_address,postcode,property_uprn,address_source
```

Use permitted address records, unique property IDs and property coordinates within the configured area. UPRN is optional. Leads need a verified nonempty address, postcode and source. Detailed field rules are in the [operator guide](docs/OPERATOR_GUIDE.md#3-prepare-and-freeze-the-candidate-sample).

If needed, retrieve address candidates with one bounded OS request (replace the coordinates with the agreed area):

```powershell
poc lookup-addresses --longitude=-0.35 --latitude=51.82 --radius=100 --maxresults=10 --output outputs/client_addresses.json
```

This saves JSON, not the import CSV. Select the agreed candidates and copy their fields into `data/client_candidates.csv`, adding stable property IDs. OS results do not verify driveway ownership.

```powershell
poc import-candidates data/client_candidates.csv
poc discover
poc review-template --output outputs/client_discovered_views.csv
```

The sample and search settings are now frozen. Use another database for a different sample/area. Discovery saves metadata and provider links, not JPEGs. Inspect permitted links and select useful views; do not classify every nearby road image.

For each selected view, replace `IMAGE_ID` and `PROPERTY_ID` with actual IDs:

```powershell
poc fetch-image IMAGE_ID --output outputs/client_images
poc link-image PROPERTY_ID outputs/client_images/IMAGE_ID.json
poc classify-image outputs/client_images/IMAGE_ID.jpg --output outputs/client_IMAGE_ID_result.json
poc import-classification IMAGE_ID outputs/client_IMAGE_ID_result.json outputs/client_images/IMAGE_ID.jpg
poc review-gallery --images outputs/client_images --output outputs/client_new_sample_review.html
poc review-template --output outputs/client_new_sample_review.csv
```

Fetch uses two HTTP requests; classification makes one inference request with no automatic retry. The configured model is `gemini-3.1-flash-lite`. Importing a saved result makes no new inference call. A timeout may still be billed. `max_requests` bounds discovery attempts per run, not total downloads, OS requests or Gemini spending.

Continue with section 4's review/import/material/export steps using the new sample's CSV. Review all cases, including negatives and uncertainty. Missing imagery is not a negative material classification. If no useful view exists, keep the property in QA with its limitations.

## 6. Street View provider (second imagery source)

Google Street View is available as an alternative to Mapillary when Mapillary has no coverage near a property. It is a **separate config/database pair** (`data/streetview_config.json`), so it never mixes with or overwrites the Mapillary pilot.

**Known accepted risk:** Google's Street View Static API policy generally prohibits pre-fetching, storing or caching panorama content, and does not explicitly cover sending imagery to a third-party AI model. Using it this way, for this non-commercial PoC, was an explicit owner decision to proceed and defer a full licensing review — recorded in `data/streetview_config.json`'s permission evidence. **Revisit this before any client-facing or commercial scale-up.**

```powershell
function poc-sv { uv run --no-sync --env-file .env python -m driveway_poc --config data/streetview_config.json --db data/streetview_poc.sqlite3 @args }
poc-sv status
```

**Resolving an address to coordinates.** Street View needs a point, not a street name. `resolve-address` tries the address text first; if Google's geocoder can't place it (a common, often transient `UNKNOWN_ERROR`/`ZERO_RESULTS`), it automatically falls back to the postcode's centroid (via the free, open `postcodes.io` dataset) and retries there. It makes no database changes — copy the printed `latitude`/`longitude` into your candidates CSV yourself.

```powershell
poc-sv resolve-address --address "33 The Uplands" --postcode "AL5 2PA"
```

If it still fails, retry once or twice — Google's own geocoder is sometimes flakier than the underlying imagery coverage.

**Running the sample**, same `property_id`/`street`/`latitude`/`longitude`/`property_address`/`postcode`/`property_uprn`/`address_source` CSV shape as section 5:

```powershell
poc-sv import-candidates data/streetview_candidates.csv
poc-sv discover-streetview
poc-sv classify-streetview PROPERTY_ID --heading 180
```

`discover-streetview` only checks panorama availability (one small metadata request per property; no pixels fetched). A single angle frequently misses the actual driveway (facing a hedge, the road, or a neighbour's drive), so prefer:

```powershell
poc-sv scan-streetview PROPERTY_ID
```

This classifies the property from **four** headings — toward it (computed from the camera's bearing to the property coordinates), the opposite side (the camera may be across the road), and both perpendiculars (corner plots, side-on driveways) — rather than guessing. It costs 4 Gemini calls instead of 1. A failure on one heading (timeout, transient 503) is recorded, not fatal to the others; rerun just that heading with `classify-streetview PROPERTY_ID --heading N` to fill a gap (it is safe to rerun — it won't overwrite an image already saved for that heading).

For a single specific angle instead: `classify-streetview PROPERTY_ID --heading 0-359`. Either command **saves the image** under `outputs/streetview_images/<address>_h<heading>.jpg`; pass `--no-save-image` to skip saving for a given call. Neither will silently overwrite an existing file for the same address/heading.

Even with four angles, treat every result as a lead to manually verify, not a settled answer — the model has given different answers for the same scene on different calls.

Continue with section 4's review/import/material/export steps, pointed at `data/streetview_config.json`/`data/streetview_poc.sqlite3`, to get `leads.csv`/`qa.csv`. The model is not perfectly consistent between calls on the same scene — treat every positive as a lead to manually verify, never as settled.

## Troubleshooting and feedback

| Message/symptom | What to do |
|---|---|
| `uv` or Python not found | Install prerequisites, reopen PowerShell, rerun setup |
| Missing config/database | Obtain handover data or create a fresh study in section 5 |
| Missing key | Edit `.env` locally and use the `poc` helper to load it |
| Permission not established | Resolve the named permission and record genuine approval evidence |
| Existing output | Reuse the successful artifact or choose a new filename/directory |
| Study settings changed | Restore frozen settings or use a new database for a new sample |
| Empty gallery/hash mismatch | Use the exact classified rendition and regenerate after moving files |
| HTTP/authentication/timeout error | Save the sanitized error and contact us before repeating potentially billed calls |
| Empty leads | Read `qa.csv` exclusions; keep review requirements intact |

Send us the command, sanitized error, expected versus actual result, and relevant property/image IDs through the agreed channel. Keep keys out of feedback. Review findings should identify the selected region and say whether material/address is verified or uncertain.

## Technical reference

Runtime uses Python's standard library, SQLite and CSV/JSON; `uv.lock` is included. The first client-machine installation still needs a guided check. Automated tests simulate providers and do not measure material accuracy.

- [Operator guide](docs/OPERATOR_GUIDE.md): advanced details and historical progress. This README is the current client run sequence.
- [Account setup checklist](docs/SETUP.md): account ownership and responsibilities, including historical planning notes.
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md) and [research](docs/RESEARCH.md): scope, acceptance criteria and earlier decisions.

No hosting, model training or wider-area expansion is included. A successful evaluation needs usable existing imagery, human review and honest reporting of remaining gaps.
