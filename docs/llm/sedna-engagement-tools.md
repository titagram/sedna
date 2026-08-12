# Sedna engagement tools — Hades operating contract

Contract version: `sedna-engagement-tools-v1`

This guide is the operating contract for the M6A engagement journal surface exposed by the
Sedna plugin (version `0.2.0`): `sedna_manage_engagement`, `sedna_record_decision`,
`sedna_add_source`, and the observer hooks that retain host tool calls automatically.

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

## 7. `closing` is waiting for finalization, not a verified success

A `close` request moves the engagement to `closing` and records a closure barrier with the
in-flight calls at that moment. In M6A, `closing` means M6C finalization is pending; it does
not certify that the proof set was verified. If all in-flight calls are terminated or
completed, the state reports `closure_ready: true`:

```json
{
  "action": "close",
  "reason": "all expected proof observed"
}
```

## 8. Resolve orphaned calls and reopen after rejected evidence

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

If the collected proof is rejected (for example, the platform refuses it), reopen the
engagement instead of creating a new one:

```json
{
  "action": "reopen",
  "engagement_id": "c2a4f8e0-0000-4000-8000-000000000001",
  "reason": "platform rejected the submitted proof"
}
```

## 9. Global sources are optional

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

## 10. Respond to every typed error without inventing hidden causes

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
`interpretation_failed`, `settlement_unavailable`.

When you see a typed error, respond to that exact code with the documented behavior. Never
invent a hidden cause, retry a non-retryable error blindly, or guess an engagement where the
tool reports ambiguity.

## Scope notes

- M6A has **no planner yet**: `sedna_plan_next` is reserved for M6B and is not registered.
- Continue using the existing Sedna knowledge retrieval tools
  (`sedna_retrieve_knowledge` and friends) until M6B lands; engagement evidence does not
  flow into the knowledge index.
- `closing` state, proof verification, settlement composition, and richer lifecycle
  services arrive with M6B/M6C; this contract is versioned and stable at
  `sedna-engagement-tools-v1`.
