# Client-owned setup checklist

Status: user authorized implementation; local feasibility tooling is ready and client-owned access is next. No accounts created, messages sent, terms accepted or purchases made.

## Responsibility

We handle technical configuration and testing. Client retains ownership, recovery methods and billing; completes required identity/phone checks, service acceptance and payment entry. Use delegated access where supported. Do not use developer-owned paid accounts, share passwords or paste keys into chat/source control.

## Ordered setup

1. Agree scope and the spending ceiling; identify the client owner email and a guided setup time.
2. Investigate Mapillary first: inspect full current terms using authorized access, establish whether the exact workflow is permitted, then register/configure client-controlled API access as supported. Record the ownership/transfer arrangement before relying on an account tied to an individual login.
3. Run limited authorized metadata and view checks on the agreed streets. Record missing coverage and failed property associations as well as successes.
4. Resolve the address source and delivery rights. Start with a permitted small address sample if feasible; obtain client-owned OS/provider access only if necessary. An API subscription is not automatic permission to resell/distribute its records.
5. Configure a dedicated client-owned Google project for the POC and Gemini through the current AI Studio flow. Client enables approved billing and any required payment verification. Grant only the project permissions needed for setup/operation.
6. Create the current supported Gemini credential; restrict it to the required service. Keep credentials in a local environment/secret mechanism excluded from Git. Record owner, project ID and revocation procedure, not secret values.
7. Confirm processing/data-use terms with the selected imagery source, model account and retention policy. Configure provider spend controls and a bounded run budget.
8. Test one permitted image; check model access, JSON output, usage and failures before batch work.
9. On handover, deliver the runbook and outputs; remove temporary developer access and rotate/revoke setup credentials as agreed.

No paid address/imagery purchase until the actual product, terms and cost are presented for approval.

## Clarification text, ready for review but not sent

Imagery provider:
"We are preparing a small commercial proof of concept in Harpenden, UK. We intend to retrieve a limited number of street-level images and camera metadata through your authorized API, create driveway crops where needed, send them to a client-owned Google Gemini paid-service project for inference (not training), manually verify the associated property, and deliver an address/classification CSV to that client. Please confirm whether the applicable terms allow this workflow, local image/crop retention, the external model transfer and its service logging, and delivery/use of the derived CSV. Please identify required attribution, retention/deletion rules and any additional agreement."

Address provider:
"We need a small number of UK postal addresses/UPRNs to verify property matches and deliver a driveway-classification CSV to our client for the agreed commercial use. Which licence permits retrieval, local storage, inclusion in the client CSV and continued client use? Please distinguish trial/development access from permitted client delivery."

## Readiness record

Before live implementation record:
- Client owner and delegated access confirmed.
- Provider rights evidence location and date.
- Agreed streets and attempted-property count; existing licensed imagery is mandatory.
- Address source and export rights.
- Model/project and confirmed account configuration.
- Third-party spending ceiling.
- Image freshness and retention rules.
- Reviewer and independent material-label source, or explicit lack of ground truth.
- Local dependency/API networking verified.

Authoritative setup sources are linked in RESEARCH.md. Recheck account screens and terms at setup time.
