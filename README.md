# Harpenden driveway detection POC

Status: imagery discovery, bounded address lookup, image classification, human-review recording and leads/QA export are implemented. A live ten-property sample has completed metadata discovery and produced a QA export. Zero leads are eligible so far; human property/material review and a real positive example are pending.

Scope: 10-20 attempted Harpenden properties, resin-bound only, every result manually reviewed. Existing licensed street imagery is mandatory. Supplied or newly collected photos cannot replace discovery. If Mapillary fails, investigate another existing-imagery provider; if none is viable, report blocked.

## What runs now

- Explicit permission/account readiness checks, without exposing credential values.
- Validated, frozen property sample with optional UPRNs.
- Bounded Mapillary metadata search with safe pagination, retries, deduplication, saved progress and request accounting.
- Manual review template with provider links; validated review imports and revision history.
- Internal feasibility.csv and summary.json that distinguish unsearched, missing, incomplete, unreviewed and reviewed cases.

Also available: bounded single-image downloading with provenance, Gemini connectivity checks and image classification, and an OS Places connectivity/normalization check. Nearby camera coordinates never automatically identify a property's driveway. Lead export requires a usable, fresh, human-reviewed likely-positive with an unambiguous verified address.

Verification: 72 offline regression tests cover metadata, addresses, image download, Gemini and output safeguards. Tests make no API calls.

## Run in this workspace

A local virtual environment has been installed from a locally built wheel, without downloading dependencies. From PowerShell in this directory:

~~~powershell
./.venv/Scripts/python.exe -m driveway_poc --help
./.venv/Scripts/python.exe -m driveway_poc --config config.example.json status
./.venv/Scripts/python.exe -m unittest discover -s tests -v
~~~

The example configuration intentionally has no approved permissions, account, study boundary or freshness policy. Status works offline; live discovery is blocked.

For normal development with package-index access:

~~~powershell
uv sync --locked
uv run driveway-poc --config config.example.json status
uv run python -m unittest discover -s tests -v
~~~

Runtime uses only Python's standard library (Python 3.12+); setuptools is a build-time dependency. uv.lock is checked in. The current local wheel is a snapshot: source edits require reinstalling. See [operator guide](docs/OPERATOR_GUIDE.md) for the tested offline rebuild route and full workflow.

## Documents

- [Implementation plan and milestones](docs/IMPLEMENTATION_PLAN.md)
- [Research and verification status](docs/RESEARCH.md)
- [Client-owned setup checklist](docs/SETUP.md)
- [Operator guide](docs/OPERATOR_GUIDE.md)

## Structure

- src/driveway_poc/: config, SQLite storage, metadata discovery, review/report, CLI
- tests/: synthetic offline regression tests; no real property claims
- config.example.json: unapproved configuration template
- examples/candidates.csv: input headers only
- data/: ignored local configuration, licensed candidate records and SQLite state
- outputs/: ignored internal reports and review sheets
- chat.txt: original context, ignored by Git

Client-owned Mapillary, OS Places and Gemini credentials are configured locally; never commit .env or generated property evidence. The normal shell tool failed in this environment; local commands were run through the available Node runtime. The application does not depend on that runtime.

## Single-image integration

Run from the workspace root; uv loads client credentials without printing them:

~~~powershell
uv run --no-sync --env-file .env python -m driveway_poc check-gemini
uv run --no-sync --env-file .env python -m driveway_poc fetch-image 1325329735432124 --output outputs/images
uv run --no-sync --env-file .env python -m driveway_poc classify-image outputs/images/1325329735432124.jpg --output outputs/new_classification.json
~~~

Each explicit check/classification uses one inference request and does not retry automatically. Fetching uses one metadata request plus one CDN request. Existing artifacts are protected from overwrite; reuse downloaded images. Image/storage/external-AI permission records must be approved. The sample image is already downloaded, so do not repeat its fetch.

The working model is gemini-3.1-flash-lite. A listed older model returned 404 and gemini-3.8-flash returned 503 during diagnosis. The working inference used minimal thinking and a 55-second timeout. Responses must finish successfully and pass validation. Limited visibility normalizes positive/negative material suggestions to unclear. Every result requires manual review and remains address-unverified.

## Property sample, review and export

The CLI also provides lookup-addresses (one bounded OS request, no ownership inference), link-image (provisional evidence only), import-classification (saved result must match source image and prompt hash), review-material (actual human decision), review-gallery, and export-results. Use --help for arguments.

1. Retrieve permitted address candidates with lookup-addresses; freeze a 10–20 property CSV with import-candidates.
2. Run discover within the configured request budget. Use fetch-image for selected views, then link-image to attach downloaded provenance to an existing property.
3. Run classify-image once per selected image. Import its saved result with import-classification; this step makes no inference call. Unchanged successful artifacts can be reused.
4. Generate review-gallery and review-template. An actual reviewer checks visibility, property ownership and address, then import-review saves those decisions. Record the separate material decision with review-material --detection-id d1; select the actual box and describe the driveway in the evidence.
5. Run export-results --output outputs/NEW_DIRECTORY. It creates leads.csv, qa.csv and summary.json. It never overwrites a previous delivery. Unreviewed, ambiguous, stale, unsupported panorama and non-positive cases stay out of leads. Multiple approved views yield one property row; conflicting material reviews block that property.

SQLite schema 1/2 upgrades to 3 without discarding existing properties/reviews. Updated model/image evidence invalidates prior material approvals. Runtime remains standard-library only. No agent-generated visual observation is recorded as a human approval.


### Individual driveway results

Gemini now returns a driveways list with separate IDs, normalized boxes [ymin,xmin,ymax,xmax] on a 0–1000 scale, labels and limitations. Output is constrained by a JSON schema and checked locally. The top-level classification remains a scene summary for historical compatibility; it is never a property material decision. review-material requires --detection-id. One detection cannot be assigned to two properties, and legacy whole-image results cannot approve a lead.

The gallery displays each image once, overlays tentative boxes and lists nearby addresses separately. QA distinguishes context_classified_images from selected_driveway_classes and summaries report unique classified images. Historic gallery/CSV files are preserved; use the latest versioned artifacts. fetch-image --original requests the highest available API rendition; use a separate directory to preserve prior evidence. Rejected inference may leave a .rejected.txt diagnostic but no successful result JSON. These are still untrusted suggestions requiring review.
