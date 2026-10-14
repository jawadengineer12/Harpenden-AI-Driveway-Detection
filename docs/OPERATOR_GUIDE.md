# Feasibility operator guide

This milestone audits existing imagery access and property/address association. It does not classify resin or produce sales leads.

## 1. Environment

Python 3.12+ is required. Prefer uv sync --locked when package downloads are available.

The tested restricted-environment route uses existing local setuptools and wheel to build without fetching packages, then installs the wheel into the workspace virtual environment:

~~~powershell
python -m pip --disable-pip-version-check wheel . --no-deps --no-build-isolation --wheel-dir .cache/wheels
uv --cache-dir .cache/uv venv .venv
uv --cache-dir .cache/uv pip install --offline --python .venv/Scripts/python.exe --reinstall .cache/wheels/harpenden_driveway_poc-0.1.0-py3-none-any.whl
./.venv/Scripts/python.exe -m unittest discover -s tests -v
~~~

Skip the venv creation line if .venv already exists. That environment is already present here. The initial offline uv sync could not fetch its build dependency; the wheel route succeeded using preinstalled packaging tools. Rebuild/reinstall after source edits; the installed wheel is not an editable checkout.

uv --cache-dir .cache/uv lock --offline --check validates the dependency lock without accessing the protected global cache. Runtime currently has no external dependencies.

## 2. Record the actual scope and permission evidence

Copy config.example.json to data/config.json. Do not change unknown permissions to approved merely to get past an error. Each approval requires actual evidence, a reviewer and an ISO review date. These records document a human assessment; the application cannot verify a contract's meaning.

Set:
- bbox as [west longitude, south latitude, east longitude, north latitude] for the agreed small Harpenden street sample.
- client_owned_account only after ownership is confirmed.
- max_image_age_days only after agreeing the acceptable age.
- max_requests as the total HTTP-attempt ceiling per discovery run, including retries.
- max_pages_per_property as a bound on each search; each page requests up to 100 metadata records.
- search_radius_m as the nearby-camera search extent, clipped to the study bbox.

The configuration intentionally contains no credential. Put MAPILLARY_ACCESS_TOKEN in the project-root .env file, which is ignored by Git, or set it in the process environment. Do not share credentials in chat. The Python CLI does not load .env automatically; use uv's built-in loader:

~~~powershell
uv --cache-dir .cache/uv run --no-sync --env-file .env python -m driveway_poc --config config.example.json status
~~~

Use the same uv prefix with discover once data/config.json and the study are ready. GEMINI_API_KEY and OS_PLACES_API_KEY are reserved entries for later integrations and can stay empty. No Mapillary client secret or authorization URL is needed.

Permissions:
- address_use: importing/storing the candidate property records.
- metadata_access: querying, storing and reviewing the provider metadata.
- image_use: viewing images for manual feasibility review.
- image_storage, external_ai, derived_export, address_export: additional rights required before advancing to live classification/client delivery.

A metadata-only run needs approved metadata_access and address_use plus client-owned access; it does not send images to Gemini or download image files. Open provider links manually only under permitted viewing terms.

Check readiness:

~~~powershell
./.venv/Scripts/python.exe -m driveway_poc status
~~~

## 3. Prepare and freeze the candidate sample

Copy the header-only examples/candidates.csv to data/candidates.csv and populate the agreed properties from a permitted source. No fabricated addresses or coordinates are supplied.

Fields:
- property_id: a stable 1-64 character identifier starting with a letter/digit; letters, digits, dots, underscores and hyphens only.
- street: street label used to measure sample spread.
- latitude / longitude: property point in WGS84 decimal degrees, not camera position.
- property_address / postcode: source address if known; blank is allowed at discovery but cannot pass address verification.
- property_uprn: optional, kept as text (up to 12 digits).
- address_source: provenance of the record; required even if the full address is unknown.

This is an unlabelled candidate-property list, not a list of known resin driveways. The software must discover existing imagery for these properties.

~~~powershell
./.venv/Scripts/python.exe -m driveway_poc import-candidates data/candidates.csv
~~~

The import is all-or-nothing. The sample and search settings are then frozen so coverage denominators do not drift. Identical imports are harmless; changes require a new database using the global --db option. For a smoke test 1-9 candidates are allowed; the agreed POC is 10-20 attempted properties. Postal-address corrections also require a new sample database in this milestone; they cannot be silently edited through the review sheet.

## 4. Discover existing metadata

~~~powershell
./.venv/Scripts/python.exe -m driveway_poc discover
~~~

No image files are downloaded. Each candidate is searched in a small bounded area. Camera points are candidate evidence only. Flat and panoramic images are identified where metadata provides the flag; no reprojection is performed at this stage.

- Successful completed searches are skipped on rerun, even if they found no images.
- Interrupted pending searches restart with deduplication; completed work remains.
- Failed or page/request-limited searches are not automatically retried.
- Authentication rejection stops the run.
- Explicit retry starts another bounded run and may repeat provider requests:

~~~powershell
./.venv/Scripts/python.exe -m driveway_poc discover --retry-incomplete
~~~

Run records store counts, configuration and completion status, not tokens or raw pagination URLs. Limits apply per run, so repeated commands can accumulate additional usage. The adapter has been tested against synthetic HTTP responses, not a live client account; endpoint fields/account availability still need a smoke test.

## 5. Review image links and addresses

~~~powershell
./.venv/Scripts/python.exe -m driveway_poc review-template --output outputs/review.csv
~~~

Open the provider links in the CSV using authorized access. Check actual driveway visibility, date and property attribution against independent address evidence. The camera being nearby is not enough.

Edit only these columns:
- visibility: usable / obstructed / unclear / not_visible
- property_match: verified / ambiguous / wrong_property
- address_verified: yes / no
- reviewer
- reviewed_on: YYYY-MM-DD
- evidence: describe the driveway region and the independent address/property evidence.

Leave all six decision columns blank for pending rows. Partially completed reviews, unknown IDs, duplicate pairs and changes to read-only evidence columns are rejected. Existing review templates are never overwritten by the CLI; use a new filename when regenerating. Treat image IDs and UPRNs as text when opening CSVs in spreadsheets to avoid rounding large numeric identifiers.

~~~powershell
./.venv/Scripts/python.exe -m driveway_poc import-review outputs/review.csv
~~~

Review imports are atomic and revisions are preserved in review_events. They express visual usability/property association, not a resin material label. Do not reuse the same image for multiple properties unless each distinct driveway association is actually supported.

## 6. Generate the internal feasibility report

~~~powershell
./.venv/Scripts/python.exe -m driveway_poc report --output outputs/feasibility
~~~

- feasibility.csv: one row per attempted property, including missing/incomplete searches.
- summary.json: counts, request usage and remaining gate blockers.

A reviewed feasible example requires a visible usable image, verified property association, verified nonempty postal address/postcode, and known acceptable capture age. Five qualifying properties across at least two streets plus all recorded permissions/account/freshness checks can mark readiness for a live classification smoke test. This is not a coverage or accuracy guarantee. Incomplete searches remain visible even if some useful views were found.

No results are resin leads. No imagery is supplied by the operator as an alternative discovery route.

## Next implementation milestone

After actual provider clearance and a successful Harpenden metadata/view audit: permitted image retrieval/crops, client-owned Gemini integration, independent material evaluation, reviewed leads export and handover. If Mapillary is unsuitable, evaluate another existing licensed imagery source; otherwise report the POC blocked.

## OS Places connection check

Run with the existing client-owned .env:

```powershell
uv --cache-dir .cache/uv run --no-sync --env-file .env python -m driveway_poc check-os --longitude=-0.355 --latitude=51.815
```

Each invocation makes at most one request and asks for at most one address. It never retries, saves addresses, or verifies driveway ownership. Repeating the command consumes another request; it is not a cached check. The adapter has a per-instance request cap, not an account-wide billing cap.

Current live result: corrected EPSG:4326 input axis order to latitude,longitude. One subsequent live request succeeded and returned one validated address candidate; no address was stored and ownership remains unverified. See outputs/os_places_fix_test.json. The earlier two failures remain recorded in outputs/os_places_smoke_test.json. Total OS requests across these checks: three.

Reference: https://docs.os.uk/os-apis/accessing-os-apis/os-places-api/technical-specification/radius

Coordinate regression reference: OS official Python SDK swaps longitude/latitude for EPSG:4326 requests: https://github.com/OrdnanceSurvey/osdatahub/blob/master/src/osdatahub/PlacesAPI/places_api.py . Our public method and CLI still take named longitude/latitude; only the outbound OS point order changes.


## Gemini and existing-image verification (2026-10-14)

Client key authenticated. Historical generation failures are retained in outputs/gemini_smoke_test.json. Diagnosis returned HTTP 503 UNAVAILABLE for gemini-3.8-flash; gemini-3.1-flash-lite succeeded in 1.85 seconds (7 tokens). The latter is now the explicit CLI model, with bounded output, minimal thinking, 55-second timeout, sanitized failures and no automatic inference retries.

Commands: check-gemini; fetch-image IMAGE_ID --output DIRECTORY; classify-image IMAGE_PATH --output RESULT_JSON. Use uv run --no-sync --env-file .env python -m driveway_poc before the command. Read README for examples. Neither command exports client leads.

Real Mapillary image 1325329735432124 was downloaded with metadata, creator and reference. A provider compass value of -1 is now correctly treated as unknown. Signed thumbnail URLs and tokens are not written to provenance. Single JPEG/PNG inference is capped at 8 MB; downloaded panoramas still require view selection before useful assessment.

First image inference: 1,368 tokens; driveway partly hidden by car/shadows. Original model output other/limited was normalized to unclear by the visibility safeguard without rerunning inference. Raw result is preserved separately. No resin positive or property ownership has been established. OS address storage/export permissions remain unconfirmed in configuration. No OS requests were used for this change.

Sources: https://ai.google.dev/gemini-api/docs/image-understanding and https://mapillary.github.io/mapillary-python-sdk/docs/mapillary.config.api/mapillary.config.api.entities/ .
