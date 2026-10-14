# End-to-end implementation plan

Status: Implementation authorized. Existing licensed street imagery is mandatory; start with the runnable feasibility milestone. Live processing remains gated by documented source permissions and client-owned access. Purchases require separate cost approval.
Research date: 2026-10-13.
Source context: supplied brief, chat.txt, subsequent discussion, and primary documentation linked in RESEARCH.md.

## 1. Outcome and decisions to settle

Deliver a reproducible local workflow that attempts an agreed Harpenden property sample, assesses visible driveways, verifies the associated addresses, and produces:
- leads.csv: human-reviewed likely resin-bound properties with verified addresses and permitted evidence.
- qa.csv: every attempted property, including missing imagery, uncertainty, failures and reviewer disagreements.
- findings.md: coverage, observed classification errors, unresolved cases, cost/review time and limitations.
- Source code, locked dependencies and a short operator guide.

A technically working pipeline and a commercially useful lead list are different outcomes. Zero eligible leads must be reported honestly, not padded with uncertain matches.

Confirmed:
- Client owns the service accounts and pays approved third-party costs; developer personal accounts are not a fallback.
- Client has asked us to handle setup and currently has none of the requested services/data.
- User accepted the recommendations after reviewing the proposed plan.
- Imagery rights, correct address attribution and honest claims remain essential.
- No production hosting/dashboard or 120-mile expansion in this POC.

Accepted scope:
- 10-20 attempted Harpenden properties, not a guaranteed positive-lead count.
- Resin-bound only.
- Manual review of every result and property/address association.
- Discovery from existing licensed street imagery is mandatory. Client-supplied/newly collected photos are not an alternative POC route. If Mapillary fails, assess another existing-imagery provider; if none is viable, report the POC blocked.
- Local Python pipeline, SQLite and CSV outputs as described below.

Before live work also settle:
- Exact streets/property list, rather than assuming all Harpenden or an arbitrary radius.
- Client account owner/contact and setup session arrangements.
- Third-party spending ceiling and who approves provider purchases.
- Image-age policy and permitted retention period. Until agreed, unknown age blocks client lead export; dated images remain QA-only pending the age decision.
- Who can independently verify material labels. Neither user nor client is assumed to have expert-labelled examples.
- Whether delivered records are an evaluation artifact or may immediately be used commercially; source licences must cover the intended use.

## 2. Proposed architecture

Python 3.12, local command line, SQLite and ordinary CSV/JSON. One process, sequential requests, small bounded batches.

The first metadata-feasibility milestone uses only the Python standard library, including urllib for bounded metadata requests. Later external dependencies proposed: google-genai for Gemini and Pillow for safe image decoding/orientation/crops. Confirm the current compatible versions and lock them after approval. Use Python's argparse, sqlite3, csv, json, pathlib, hashlib and unittest for everything else. If validated imagery needs panorama reprojection, add the smallest proven solution then, rather than treating an equirectangular image as a flat camera view.

No training, custom detector, agent framework, FastAPI, GeoPandas, PostGIS, queues or provider plugin framework initially. Use manual property/crop verification instead of building unreliable automatic ownership inference.

Proposed source modules (create only as their stage is implemented):
- __main__.py: command-line steps and validation of required configuration.
- imagery.py: the one approved provider, discovery, metadata and controlled downloads.
- classify.py: prompt, response schema, Gemini call and semantic validation.
- store.py: small SQLite schema, transactions and resumable processing.
- review.py: static local HTML contact sheet and validated review-CSV import.
- export.py: eligibility rules, safe CSVs and aggregate QA counts.

A local HTML contact sheet is a generated review artifact, not a hosted application. Reviewers use their normal browser and edit the review CSV in their existing editor. Local image display/copying is conditional on source rights.

## 3. Delivery stages and completion evidence

### Stage 0: agree the POC and organize access

Finalize the decisions above. Follow SETUP.md to establish client-owned accounts and delegated access. Client handles identity verification, service acceptance and payment entry where required. Do not request passwords or put credentials in chat.

Output: agreed scope, account ownership, spending limit and dependency status. No paid purchase inferred from plan approval.

### Stage 1: resolve imagery and address feasibility

Read the full current imagery/API terms under authorized access. Record evidence covering commercial automated inference, external Gemini transfer, local storage/crops, derived address/classification export, attribution and retention. Seek a provider clarification if material wording remains unresolved; do not invent permission from a successful request.

Use Mapillary first as a candidate, not an approved provider. Check an agreed small street sample with authorized metadata access. Inspect image date, resolution, viewpoint, obstructions and source attribution. Test one or two real property-to-image-to-address links manually before a batch.

Address source order: permitted small manually assembled/verified records where available; otherwise client-owned access to an authoritative source such as OS Places, conditional on the required storage/export rights. A nearby address result is only a candidate. OS Open UPRN can help locate identifiers but cannot supply full postal addresses or establish driveway ownership.

Proposed operational gate: at least five usable, attributable real property examples across more than one selected street to justify implementing the provider integration. This is a workflow smoke test, not an accuracy benchmark. If too few exist, report the counts and discuss geography or source changes.

Alternative: another existing-imagery provider under suitable terms (Cyclomedia is an investigation candidate only). Do not substitute newly collected/client-supplied photographs or scrape installation websites. Reference examples can assist validation but cannot establish discovery success.

Output: feasibility table for attempted properties, approved source evidence, and a go/no-go recommendation. Failed imagery/rights gate stops real-image ingestion/inference. The user has authorized local implementation and offline tests while access is being established. Metadata discovery requires its own approved access scope.

### Stage 2: establish the reproducible local project

Create pyproject.toml, uv.lock, one command entry point, SQLite tables and synthetic fixtures. Keep input/output roots under this workspace. Credentials come from environment variables and are never logged. Document Windows PowerShell commands.

Resolve the local package-network restriction before claiming the environment is ready. Run an approved client-account smoke test on one permitted image, with the requested response schema. Confirm the actual model ID, account quota and pricing.

Use a stable Flash-family image-understanding model available to the client's account as the first baseline; current public docs list gemini-3.8-flash, but actual access is untested. Pin the tested model ID in run metadata. Do not rely on old model availability or a moving "latest" alias.

Output: reproducible install, offline tests passing and a recorded successful live smoke test.

### Stage 3: acquire evidence and link the correct driveway

Freeze the candidate list before discovery to preserve an honest coverage denominator. Keep any deliberately selected known-material examples in a separately identified evaluation set.

Query bounded areas, paginate within the approved request limit, deduplicate image IDs and obtain metadata. Record provider image/sequence IDs, capture date, camera coordinates, projection type, bearing when available, creator/licence references and acquisition date.

Keep camera coordinates separate from property coordinates. Rank nearby views only to assist a human. Reviewer selects the actual driveway region and verifies its property association from supporting evidence. Shared driveways, adjoining houses and unresolved ownership remain ambiguous. A bearing alone never approves a match.

Preserve crop coordinates and parent image ID; an unchanged context view accompanies a crop where permitted. For panoramas, first validate an appropriate perspective view or mark unsupported pending a small reprojection step. Do not shrink the whole panorama until driveway texture disappears.

Output: usable evidence linked to a property, or an explicit reason why it is unsuitable.

### Stage 4: classify and record uncertainty

Send only the approved view/crop and the visual rubric to Gemini. Do not send an address or known label as a clue and never ask the model to supply the postal address.

Request a small structured response: class, visibility/quality, brief visible cues and uncertainty reason. Classes: likely_resin_bound, likely_other, unclear, not_visible. Keep fetch/API failures separate from visual classes. Optional model_score is explicitly uncalibrated and is not the export gate.

Validate labels/types and contradictory combinations. A refusal, malformed response, low-quality image or unresolved multi-driveway view produces an error/unclear review record, never a positive by default. Use bounded retries for transient failures, stop on authentication errors and retain successful work. Avoid SDK plus application retries multiplying calls.

Freeze prompt/model settings before evaluation. Record model ID, prompt version/hash, image hash, usage, time and result. Reuse successful results only when those inputs match. A timeout may have been billed; record the uncertain request instead of promising exact-once billing.

Output: saved per-case classifications and failures; rerunning does not automatically reclassify completed unchanged cases.

### Stage 5: independent review and address verification

For this small sample, review every case, including negatives and abstentions. Where practical, assign the reference label without showing the AI answer first.

Record separately:
- model label;
- reviewer visual label;
- material ground-truth label and basis, if independently established;
- address verification and evidence;
- source rights/freshness checks.

An installer record or other reliable material evidence is stronger than another visual opinion. If no independently confirmed material labels can be obtained, report visual agreement only, not verified material accuracy.

Resolve multi-view conflicts manually; never use an unexplained majority vote. Deduplicate properties while retaining every supporting view. Do not split different views of the same driveway between prompt development and final evaluation.

Output: reviewed decisions with reviewer/date/evidence and unresolved items excluded from leads.

### Stage 6: export, test, report and hand over

A leads.csv row requires: permission for this use/export, usable evidence, accepted freshness, reviewer-confirmed likely resin classification, unambiguous property link and verified permitted postal address. UPRN is nullable if a verified address is sufficient.

Preserve the brief's fields: property_uprn, property_address, postcode, latitude, longitude, driveway_class, model_score, human_review, address_match_status, imagery_provider, imagery_date, imagery_reference, notes. Empty score/UPRN/coordinates are preferable to fabrication; coordinates, if supplied, describe the property. QA retains detailed provenance, statuses and reasons.

Write UTF-8 spreadsheet-compatible CSVs safely, protect free-text cells against spreadsheet formula execution, retain identifiers as strings internally and document text import for UPRNs. Write exports atomically. QA row counts reconcile to attempted properties; evidence records may be many-to-one.

Report coverage at each step and denominator; model performance and final reviewed-export performance are distinct. Report confusion counts/precision/recall only against adequate independent labels, otherwise reviewer agreement. Include abstention rate, address errors, duplicate count, API costs and review minutes. Zero denominators are "not estimable", not 0% or 100%.

Handover: working source, locked environment, actual run commands, sample run ID, leads/QA/findings, permissions/attribution notes and credential-revocation instructions. Back up only data the licence permits retaining.

## 4. Small but meaningful verification suite

Use unittest with synthetic responses and images, no live credentials in tests:
- Failed/unknown permissions, uncertain address, stale/unknown-date policy or unreviewed positives cannot enter leads.
- Adjacent/shared properties do not get silently merged; duplicates and multiple views preserve provenance.
- Missing imagery, API errors, unclear and actual negatives remain distinct.
- Malformed model output, unknown labels, timeouts, 429/5xx and authentication failure have bounded, recorded outcomes.
- Resume preserves prior successful work and invalidates results when image/prompt/model changes.
- Review imports reject unknown IDs, duplicate/conflicting rows and invalid decisions.
- CSV formula safety, Unicode addresses, identifier preservation and atomic output.
- A small mocked end-to-end run reconciles attempted, excluded and exported records.

Then one controlled live integration run after access approval, followed by the agreed reviewed property sample. Existing chat claims of "4/4" and "7/7" tests are not available test artifacts.

## 5. Cost, effort and boundaries

Use an explicit maximum property count, views per property and total paid-request count. Track actual usage and retries; configure the client's provider spend controls. No monetary amount is approved yet.

Cost estimate after smoke test:
imagery/licence cost + address cost + model input/output usage + review/verification time.
Cost per eligible lead is undefined if no eligible leads are produced. Human review and sourcing may dominate inference.

Planning estimate: approximately 5-8 focused engineering days after usable access, data and scope decisions; licensing replies, client verification and access/network delays are additional. This is an estimate, not a promised accuracy or lead count.

Defer production hosting, unattended discovery, custom model training, national address ingestion and wider-area scaling until the POC evidence warrants them.

## 6. Implementation progress

First milestone implemented: Python packaging and lockfile, explicit permission configuration, frozen candidate import, SQLite provenance, bounded Mapillary metadata discovery, manual feasibility review and internal reports. The runtime is standard-library only; the provider adapter is validated with mocked responses. See OPERATOR_GUIDE.md for actual commands.

Next milestone remains gated: actual metadata access, imagery/view audit and permitted address evidence, then image retrieval, Gemini classification and leads export. No photo-upload fallback will be added. A header-only candidate template is provided; no real properties, permissions or provider availability were invented.

Verification: locally built and installed package; 32 offline tests passed against the installed package, including an end-to-end mocked CLI workflow. Offline lock check passed. The unapproved readiness template correctly reports missing provider access, permissions, study boundary and freshness policy. No live API call or coverage claim was made.


### Verified integration progress — 2026-10-14

Gemini generation and one real Mapillary street-image inference now pass. Reusable fetch-image, check-gemini and classify-image commands are implemented. Unknown Mapillary compass bearing (-1) is handled. 48 offline tests pass. First image is obstructed and recorded as unclear; no positive lead is claimed. Next: select usable existing views for the small Harpenden sample, establish address-use/export rights, verify driveway-to-property ownership, then complete reviewed lead export.


### Reviewed export implementation — 2026-10-14

The later progress entries supersede the historical initial-milestone status above. Address candidate retrieval, downloaded-image SHA-256 provenance, saved classification import, separate human material review, offline gallery and conservative leads/QA export are implemented. Runtime still uses the standard library; no SDK/Pillow dependency was needed for ordinary JPEG views. Schema 1 upgrades to 2 while preserving the sample and review history.

User-confirmed: OS local retention/client CSV export; 730-day maximum image age. The live ten-property, single-street convenience sample completed metadata discovery using ten requests. One newly classified frame showed non-resin block paving. A QA export and header-only leads.csv were generated. Human property/material reviews, a real verified resin-positive, a second street and independent accuracy evidence remain pending. The full POC is not yet accepted as complete.

63 offline tests cover the earlier integrations plus image/result binding, missing reviews, stale/future dates, every permission gate, changed-result invalidation, material conflicts, deduplication, safe CSV/gallery output and schema migration.


### Per-driveway correction — 2026-10-14

Whole-image material summaries no longer approve properties. Each detection has an ID and normalized box. Property material review must select a specific detection; duplicates across properties and legacy whole-frame approvals are blocked. The image-first gallery removes repeated photographs, verifies source hashes and shows tentative boxes. QA distinguishes shared context from selected per-property evidence. Schema 3 preserves historical records; 72 offline tests cover the updated behavior.

Further live work: 14 additional street frames plus one highest-available rendition were retrieved through bounded exploratory checks. Three successful per-driveway inferences and one rejected response were initially attempted; a further entrance inference succeeded (four successful, one rejected total). Only one explicitly diagnosed retry was made, after adding JSON-schema constraints; there are no automatic inference retries. One OS request supplied five separate Hillside address candidates. No known resin-bound reference addresses are available from the user. The core code now represents the intended property-specific decision, but no real reviewed resin-positive has been established; the POC remains unaccepted. Continue discovery from existing imagery; do not substitute supplied/new photographs.
