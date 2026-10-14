# Research and verification status

Checked 2026-10-13. This is implementation research, not completed provider clearance or real-world model validation. Recommendations below are engineering judgments.

## Imagery

Mapillary's official help page says imagery is shared under CC BY-SA and gives attribution guidance. That supports investigating reuse; it does not settle every API/contract obligation of this particular workflow.
Source: https://help.mapillary.com/hc/en-us/articles/115001770409-CC-BY-SA-license-for-open-data

The official SDK documents bounding-box/nearby discovery, image IDs, capture dates, image geometry, original/computed bearings, projection types, creators and image URLs. These support a metadata-led discovery design. The SDK is supporting documentation, not proof the live endpoint/fields work for our account.
Source: https://mapillary.github.io/mapillary-python-sdk/docs/mapillary/mapillary.interface/

The full terms and primary API documentation returned "Not Logged In" via web access. No login barrier was bypassed and no token was supplied. Current commercial inference, Gemini transfer, retention and derived-export rights remain unresolved; so do live Harpenden visibility and coverage.
Sources:
- https://www.mapillary.com/terms
- https://www.mapillary.com/developer/api-documentation/

Google Maps Platform section 3.2.3 restricts content extraction, storage and creation of derived content, including a Street View-derived location index. Our proposed driveway lead dataset is sufficiently similar that standard Street View should not be the default ingestion route. A special contract/product would require its own assessment.
Source: https://cloud.google.com/maps-platform/terms

Cyclomedia publishes a Street Smart API for imagery/viewer integration. Its existence is not confirmation of downloadable Harpenden coverage, AI rights or suitable pricing. Investigate only if Mapillary fails and the user approves considering a commercial contract.
Source: https://developer.cyclomedia.com/our-apis/street-smart/

Scope decision: client-collected/original photographs are excluded as a fallback. The POC must discover evidence from an existing licensed imagery provider. Reference examples may support classification evaluation only. If no provider is viable, report blocked rather than substitute a photo-upload demonstration.

## Gemini

Image understanding supports image input and classification; inline images avoid adding a separate uploaded-file lifecycle for a small POC. Enforce supported formats and request-size limits when implementing. This capability description is not evidence of resin classification accuracy.
Source: https://ai.google.dev/gemini-api/docs/image-understanding

Structured output supports response schemas. We still need semantic validation and review of visual correctness.
Source: https://ai.google.dev/gemini-api/docs/structured-output

Current model documentation lists stable gemini-3.8-flash and notes access constraints on older 2.5 models for new projects. Proposed baseline: a current stable Flash model, tested on the client project before pinning it. Do not copy an old model name blindly.
Source: https://ai.google.dev/gemini-api/docs/models

Current key documentation describes new AI Studio authorization keys bound to service accounts, restricted to Gemini by default. Set up through the client's current project flow, rather than assuming the older unrestricted-key instructions are still correct.
Source: https://ai.google.dev/gemini-api/docs/api-key

Data-use terms differ for paid/unpaid services, with EEA/Switzerland/UK provisions. Paid-service terms say prompts/responses are not used for product improvement, but limited safety/legal logging remains. The terms also require Paid Services for API clients made available to users in those regions. Proposed setup uses a client-owned billing-enabled project; verify the actual applicable account/processing arrangements, including remote developer access, rather than inferring them from the properties being in the UK.
Source: https://ai.google.dev/gemini-api/terms

Rate limits are project-level. Query/check the client's actual quota; use sequential bounded calls and backoff rather than assumed public quotas.
Source: https://ai.google.dev/gemini-api/docs/rate-limits

Billing documentation includes project spend caps but warns of delayed billing signals and possible overages. Combine available provider controls with bounded application calls. Current pricing must be recorded for the selected model before live work; no fixed cost quote has been established.
Sources:
- https://ai.google.dev/gemini-api/docs/billing
- https://ai.google.dev/gemini-api/docs/pricing

## Addresses

OS Open UPRN supplies identifiers and locations and links to other address products. It is useful support data, not a full postal-address dataset or driveway-ownership proof. National ingestion is unnecessary for a tiny manually checked sample.
Source: https://www.ordnancesurvey.co.uk/products/os-open-uprn

OS Places provides address search including UPRN/postcode lookup. Its API distinguishes Delivery Point Address and Local Property Identifier records. We need deliberate address selection, not treating every alternative record as a separate home.
Sources:
- https://www.ordnancesurvey.co.uk/products/os-places-api
- https://docs.os.uk/os-apis/accessing-os-apis/os-places-api/datasets

OS licence terms must cover storage and the client's intended delivery/use. The official plans search result states development mode/partner trials cannot be used in live applications; the page itself is JavaScript-rendered and its full terms were not extracted. Do not present trial access as permission to distribute a commercial lead list.
Sources:
- https://www.ordnancesurvey.co.uk/licensing
- https://osdatahub.os.uk/support/plans

## Evidence ledger

| Question | Evidence obtained | Still required |
| --- | --- | --- |
| Is there an existing codebase? | Initial workspace contained chat.txt only; feasibility milestone now implemented | Later image/classification/export stages |
| Is a Python toolchain present? | Python 3.12.4, uv 0.8.3, Git 2.49.0 commands succeeded | Dependency install/lock and tests |
| Can documentation be researched? | Official public pages inspected | Logged-in Mapillary terms/docs |
| Does Mapillary support relevant discovery metadata? | Official SDK documentation | Client-token smoke test |
| Is imagery AI use/export permitted? | Partial public licensing information | Full applicable terms/clarification |
| Are Harpenden driveways usable? | No sample images inspected | Actual coverage and visibility audit |
| Can Gemini classify images into a schema? | Official capability documentation | Account and task-specific smoke test |
| Can it distinguish resin accurately? | No real model evaluation | Independent labelled examples |
| Can we deliver postal addresses? | Candidate provider documented | Source rights and verified mappings |
| Are the prior synthetic tests reproducible? | Descriptions in chat only | Actual new tests after approval |

Environment note: the ordinary shell tool failed to start. Read-only version checks and file operations worked through the available Node runtime. An earlier Python package download was denied by local network restrictions. Do not interpret web search access as proof that local package/API networking works.

## Implementation research and local checks

The first adapter uses the official Mapillary SDK source for the images endpoint, bounding-box ordering and metadata field names, and the official client documentation for OAuth header authentication. It reconstructs each next request from a cursor, rather than following potentially credential-bearing pagination URLs. Actual account responses remain untested.
Sources:
- https://raw.githubusercontent.com/mapillary/mapillary-python-sdk/main/src/mapillary/config/api/entities.py
- https://mapillary.github.io/mapillary-python-sdk/docs/mapillary.models/mapillary.models.client/

A standard-library runtime and local wheel build avoid downloading runtime packages for this milestone. An offline uv lock succeeded using a workspace cache; normal offline sync lacked a cached build dependency. The local wheel was built with preinstalled setuptools/wheel and installed into the workspace virtual environment. None of these checks verifies external API connectivity.
