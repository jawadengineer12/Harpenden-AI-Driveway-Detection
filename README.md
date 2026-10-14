# Harpenden driveway detection POC

Status: Mapillary authentication and single-image retrieval, OS Places lookup, and Gemini generation/image inference have passed live checks. The first real street image was obscured and recorded as unclear. Property attribution, representative coverage, detection accuracy and final lead export remain unverified.

Scope: 10-20 attempted Harpenden properties, resin-bound only, every result manually reviewed. Existing licensed street imagery is mandatory. Supplied or newly collected photos cannot replace discovery. If Mapillary fails, investigate another existing-imagery provider; if none is viable, report blocked.

## What runs now

- Explicit permission/account readiness checks, without exposing credential values.
- Validated, frozen property sample with optional UPRNs.
- Bounded Mapillary metadata search with safe pagination, retries, deduplication, saved progress and request accounting.
- Manual review template with provider links; validated review imports and revision history.
- Internal feasibility.csv and summary.json that distinguish unsearched, missing, incomplete, unreviewed and reviewed cases.

Also available: bounded single-image downloading with provenance, Gemini connectivity checks and image classification, and an OS Places connectivity/normalization check. Nearby camera coordinates never automatically identify a property's driveway. Client lead export and an integrated property sample run are still pending.

Verification: 48 offline regression tests cover metadata, addresses, image download, Gemini and output safeguards. Tests make no API calls.

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
