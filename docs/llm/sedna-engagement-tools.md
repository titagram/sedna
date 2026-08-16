# Sedna engagement tools — Hades operating contract

Contract version: `sedna-engagement-tools-v1`

This guide is the operating contract for the M6 engagement, planning, private-report, and
verified-case surface exposed by the Sedna plugin (version `0.2.0`):
`sedna_manage_engagement`, `sedna_record_decision`, `sedna_plan_next`, `sedna_add_source`, and
the observer hooks that retain host tool calls automatically.

## 1. Recognize an authorized machine task

Only create or resume an engagement for a task you are explicitly authorized to perform.
The authorization is a list of exact target identifiers (IP addresses, hostnames, CIDRs,
URL origins, or generic IDs). If the task is not authorized, do not call the engagement
tools and do not record evidence.

## 2. Name the engagement and declare expected proofs explicitly

Obtain or infer a human-readable display name for the task. If the name is missing, ask the
user instead of inventing one. Always declare the expected proofs explicitly through
`required_proofs`; standard HTB tasks use two separate proofs:

```json
{
  "action": "create",
  "display_name": "HTB-Orion",
  "objective": "Obtain the user and root flags",
  "authorization": ["192.0.2.44"],
  "required_proofs": [
    {"proof_id": "user-flag", "kind": "flag", "description": "User flag on HTB-Orion"},
    {"proof_id": "root-flag", "kind": "flag", "description": "Root flag on HTB-Orion"}
  ]
}
```

An empty `required_proofs` list means the task declares **no** proofs; it never means the
task is already complete:

```json
{
  "action": "create",
  "display_name": "Manual-close-only task",
  "objective": "Review configuration drift without flag collection",
  "authorization": ["192.0.2.45"],
  "required_proofs": []
}
```

## 3. Create or resume through `sedna_manage_engagement`

Start a new engagement with `create`, or continue a previous one with `resume`. Resume may
select the engagement by its display name or by an authorization target:

```json
{
  "action": "resume",
  "authorization": ["192.0.2.44"]
}
```

When several resumable engagements match, the tool returns a bounded candidate page with a
`total_count` and an ordered `omitted_candidates_sha256`; it never guesses for you. Inspect
the candidates and retry with an exact `engagement_id`:

```json
{
  "action": "resume",
  "engagement_id": "c2a4f8e0-0000-4000-8000-000000000001"
}
```

## 4. Record a decision before a material operational branch

Before every material branch of the work, call `sedna_record_decision` with the strategy and
the rationale. Use the custom branch for an original plan:

```json
{
  "custom_strategy": "Enumerate exposed services on 192.0.2.44",
  "rationale": "No services are known yet; start with a TCP discovery pass"
}
```

## 5. Validate later Sedna command suggestions through `/learn`

Sedna may suggest commands or sources. Validate any suggested command through the existing
`/learn` knowledge flow before running it; a suggestion is never an instruction.

## 6. Hooks retain commands and results automatically

The observer hooks capture every operational tool call (`pre_tool_call`/`post_tool_call`)
into the bound engagement: sanitized arguments and the original result are retained as
private evidence, and a session logbook is rendered for each host session. You do not need
to record tool calls manually. Provider or host secrets are redacted before persistence and
never appear in journals, logbooks, errors, or public tool responses.

## 7. Closing creates a private immutable report, not a verified success

A `close` request records a closure barrier with the in-flight calls at that moment. Once every
barrier call is terminal, Sedna commits canonical JSON and an inert Markdown rendering under
`engagements/<engagement-id>/reports/report-vN.{json,md}`, then moves the lifecycle to
`closed_unverified`. The report is private: it can retain raw evidence, credentials, flags, and
host-adapted commands. Its JSON is the future SysReptor integration boundary; the Markdown is
derived from that same immutable JSON. Closing does not verify the proof set:

```json
{
  "action": "close",
  "reason": "all expected proof observed"
}
```

## 8. Verify explicitly; sanitized promotion is automatic

After an external platform verifies the private report, bind that durable decision to its kind
and opaque reference:

```json
{
  "action": "verify",
  "engagement_id": "c2a4f8e0-0000-4000-8000-000000000001",
  "verification_kind": "platform",
  "verification_reference": "submission-1234"
}
```

Verification automatically starts or resumes a durable promotion saga. Only a sanitized,
provenance-bound strategic case crosses into canonical knowledge and retrieval. Private report
content, raw proof values, credentials, flags, and host-adapted commands never cross that
boundary, and there is no public `learn` or `promote` bypass. `inspect` and `resume` recover an
interrupted saga; an exact repeated `verify` is idempotent.

## 9. Resolve orphaned calls and revoke before reopening rejected evidence

If a post hook is missing (for example, the host process exited), the tool call stays
in-flight. Resolve it explicitly with `resolve_call`, using the journal `call_id` shown by
`inspect` (raw provider `tool_call_id` values are correlation inputs only and are never
accepted as journal call IDs):

```json
{
  "action": "resolve_call",
  "call_id": "call-<64 hex journal call id>",
  "resolution": "abandoned",
  "reason": "host process exited before the post hook"
}
```

If the platform rejects a recorded proof, use `reject` with the exact journal flag event. Sedna
revokes any published lineage before atomically recording the rejection and reopening:

```json
{
  "action": "reject",
  "engagement_id": "c2a4f8e0-0000-4000-8000-000000000001",
  "flag_event_id": "00000000-0000-4000-8000-000000000099",
  "reason": "platform rejected the submitted proof"
}
```

For a non-rejection correction, use explicit `reopen`; it still revokes or proves the absence of
the current publication before making the engagement active:

```json
{
  "action": "reopen",
  "engagement_id": "c2a4f8e0-0000-4000-8000-000000000001",
  "reason": "platform rejected the submitted proof"
}
```

Later report revisions preserve bounded `case_promoted`, `case_promotion_revoked`, and
`case_promotion_superseded` transitions in their timeline, but never include candidate or public
case payloads. A report watermark never projects future saga events.

## 10. Global sources are optional

`sedna_add_source` records a shared source suggestion. It is optional and never mandatory;
the task does not depend on it:

```json
{
  "name": "Orion service documentation",
  "locator": "https://docs.example.test/orion/services",
  "topics": ["http", "enumeration"],
  "notes": "Vendor reference for the Orion appliance"
}
```

## 11. Respond to every typed error without inventing hidden causes

Every control tool returns the same closed envelope on failure:

```json
{
  "ok": false,
  "error": {"code": "engagement_not_found", "retryable": false}
}
```

Settlement failures additionally carry the exact non-complete outcome and pending range and
never a stale engagement snapshot:

```json
{
  "ok": false,
  "error": {"code": "evidence_budget_exhausted", "retryable": true},
  "settlement": {
    "status": "incomplete",
    "pending_range_count": 2,
    "next_pending_offset": 2097153,
    "next_pending_subject": "pending-<64 hex>",
    "pending_inventory_sha256": "<64 hex>"
  }
}
```

Known closed codes: `invalid_input`, `host_context_required`, `invalid_target`,
`unauthorized_scope`, `engagement_not_found`, `engagement_ambiguous`,
`engagement_conflict`, `invalid_transition`, `proposal_not_found`, `call_not_found`,
`lane_unbound`, `journal_unavailable`, `journal_corrupt`, `evidence_capture_failed`,
`in_flight_limit_exceeded`, `result_too_large`, `source_registry_failed`,
`unsupported_platform`, `evidence_budget_exhausted`, `interpretation_incomplete`,
`interpretation_failed`, `settlement_unavailable`, `promotion_saga_in_progress`,
`promotion_recovery_failed`.

`promotion_saga_in_progress` is retryable. It means promotion, cancellation, or revocation owns
the engagement. Do not mutate, generate a report, write evidence, or call planning operations
until `inspect`/`resume` finishes recovery. Reads remain safe; the error exposes no saga payload,
snapshot, frontier, or raw exception.

`promotion_recovery_failed` is retryable. It means `inspect` or `resume` found a durable promotion,
cancellation, or revocation intent but could not finish its recovery pass. Preserve the private
journal and canonical store, correct the reported operational fault, then call `inspect`/`resume`
again; do not reopen, reject verification, plan, or publish a replacement while recovery remains
incomplete. The public error remains bounded and never includes case payloads, private evidence,
credentials, flags, source identifiers, canonical revisions, or raw exceptions.

When you see a typed error, respond to that exact code with the documented behavior. Never
invent a hidden cause, retry a non-retryable error blindly, or guess an engagement where the
tool reports ambiguity.

## Scope notes

- `sedna_plan_next` plans from settled private engagement evidence; it does not publish evidence.
- Continue using the existing Sedna knowledge retrieval tools (`sedna_retrieve_knowledge` and
  friends) for canonical knowledge. Only verification-gated sanitized cases enter that surface.
- Report artifacts remain under the configured absolute private knowledge root. Public tool
  responses expose bounded references and status, not report bytes or arbitrary filesystem paths.
- This contract remains versioned as `sedna-engagement-tools-v1`.
