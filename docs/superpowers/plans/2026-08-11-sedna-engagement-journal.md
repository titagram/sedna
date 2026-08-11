# Sedna M6A Engagement Journal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a durable, private, append-only engagement journal that Hades can create, resume, update across sessions, and populate automatically through observer hooks, while leaving semantic planning to M6B and report-backed terminal closure to M6C.

**Architecture:** Add a host-neutral `sedna.engagement` package with strict Pydantic contracts, a pure replay reducer, descriptor-confined JSON/JSONL and evidence persistence, reproducible Markdown logbooks, and an `EngagementJournalService` facade consumed by later milestones. A thin Hades adapter binds host session/task lanes to engagements, exposes three control tools, and records operational tool calls through fail-open host hooks without invoking an LLM.

**Tech Stack:** Python 3.11–3.13, Pydantic 2.13.4, POSIX `dir_fd`/`O_NOFOLLOW`/`flock`/`fsync`, JSON/JSONL, Markdown, pytest 8, Ruff 0.15.10.

## Global Constraints

- Keep the zero-configuration root exactly `<active-host-home>/knowledge/sedna`; do not add an environment variable or move canonical M1–M5 data.
- Engagement tools use the single root selected by `ctx.sedna_knowledge_root` or the zero-configuration resolver. They do not accept a per-call `knowledge_root`, because hooks must write to the same store.
- `engagement.json`, `events.jsonl`, its crash-safe authoritative `journal-head.json`, and original
  evidence are authoritative. `engagement-state.json` and session logbooks are rebuildable M6A
  projections; the filename `state.json` is reserved for the M6B situation projection.
- Store engagement directories as mode `0700` and authoritative files, evidence, lock files, and projections as mode `0600`.
- Preserve flags, target credentials, commands, and original tool results in the private engagement store. Never index them in canonical retrieval or FTS. Provider credentials and host-runtime secrets unrelated to the engagement are the exception: structurally recognized values are removed before *any* event or argument sidecar is persisted, without retaining a value digest.
- Treat every hook payload, tool argument, result, source entry, and captured Markdown/HTML fragment as untrusted data.
- Do not invoke the host LLM from pre/post-tool hooks or any M6A repository operation.
- Sedna remains guided and non-coercive: an unplanned bound action is recorded but not blocked.
- Use `CONTROL_TOOL_POLICY_VERSION = "sedna.control-tools.v1"` and the exact set `sedna_manage_engagement`, `sedna_plan_next`, `sedna_record_decision`, `sedna_add_source`, `sedna_learn_local`, `sedna_retrieve_knowledge`, `sedna_get_knowledge_artifact`, and `sedna_knowledge_maintenance`. These control calls emit typed control/semantic events when a unique engagement exists and are excluded from ordinary operational-call capture. The legacy `sedna_nmap_tcp_discovery` and `sedna_nmap_service_scan` names are deliberately outside the set and remain operational calls.
- M6A stops terminal closure at `closing`. It records the closure barrier and exposes a `ClosureFinalizer` seam; M6C alone emits `engagement_closed`, `closed_unverified`, and the required report snapshot.
- A new operational call while `closing` atomically appends `closure_cancelled` before `tool_call_started` and returns the engagement to `active`.
- Prefer `tool_call_id` for stable correlation. A fallback is stable only when a host supplies a
  true per-response `tool_call_ordinal`, together with session/task/turn/request identity, tool name,
  and the canonical sanitized-argument digest. Hades currently supplies neither that ordinal nor a
  substitute—`api_call_count` is a request-attempt counter—so a missing `tool_call_id` is typed
  `uncertain_correlation` and is never deduplicated heuristically.
- Child-task inheritance is allowed only from an exact parent lane or from a provably unique parent-session binding. Ambiguous children remain unbound.
- Proof requirements are explicit manifest data, never inferred from an objective string. A normal HTB engagement supplies separate `user-flag` and `root-flag` requirements. An empty requirement list permits manual close but can never trigger proof-driven automatic close in M6C.
- Evidence defaults are exactly 64 MiB per item and 4 GiB per engagement. M6A allows bounded reads up to 64 KiB; M6B settlement will consume 32 KiB slices and at most 64 slices (2 MiB) per settlement pass before returning `incomplete`.
- `sources.md` contains machine-owned marked blocks while preserving all user-authored bytes outside those blocks.
- The first implementation has the same POSIX safety boundary as `CanonicalKnowledgeRepository`; unsupported platforms fail closed with a typed journal-unavailable result.
- Do not migrate or reuse legacy `sedna.models.Engagement` or `SednaStore` records.
- Do not add an SQLite engagement database, semantic scorer, planner prompt, execution examples, report renderer, promotion adapter, SysReptor integration, or real security-tool execution in M6A.
- All tests use simulated hook payloads and temporary local files. No test invokes Nmap, a network target, or a provider LLM.

## Resolved Milestone Boundaries

M6A may parse future lifecycle values `closed_unverified` and `closed_verified` so a newer journal can still be replayed, but its service never creates them. `request_close()` appends a `closure_requested(origin="manual")` event with a terminal watermark and exact in-flight call IDs. The closed payload also permits `origin="proof_settlement"` for M6C's sealed terminal coordinator, so a later proof contradiction cancels only an automatically requested closure. When every captured call is terminal, the reducer exposes `closure_ready=True` while status remains `closing`. M6C supplies the `ClosureFinalizer`, atomically commits a report, and appends the terminal close event with compare-and-swap semantics.

M6A captures original host-delivered result bytes and engagement-relevant arguments. Before argument normalization, `sanitize_host_arguments()` recursively replaces values under the closed provider/host-secret key policy with a constant redaction marker; it stores neither the original value nor a value digest in events, logs, sidecars, errors, or correlation material. The sanitized argument sidecar carries `capture_limitations=(CaptureLimitation.PROVIDER_OR_HOST_SECRET_REDACTED,)`. Result bytes remain private original evidence because arbitrary terminal output cannot be reliably classified at this boundary; logbooks label it untrusted and later promotion must sanitize it.

The public compatibility surface consumed by M6B and M6C is:

```python
from sedna.engagement import (
    ClosureFinalizer,
    EngagementSettlementPort,
    EngagementJournalService,
    EngagementSnapshot,
    EvidenceId,
    ExecutionLaneKey,
    JournalEventDraft,
    JournalRevision,
    ProofRequirement,
    SettlementReason,
    ScopeReference,
)
```

`EngagementJournalService` must retain these methods and their semantics:

```python
load_snapshot(engagement_id)
load_events(engagement_id, after_sequence=..., through_revision=..., limit=...)
list_evidence_descriptors(engagement_id, after_sequence=..., through_revision=..., limit=...)
append_events(engagement_id, drafts, expected_revision=...)
read_evidence_slice(engagement_id, evidence_id, offset=..., limit=...)
load_projection(engagement_id, name, model_type)
commit_projection(engagement_id, name, projection, expected_revision=...)
resolve_lane_binding(lane)
load_active_decision(engagement_id, lane)
terminate_tool_call(engagement_id, call_id, resolution=..., reason=..., lane=...)
```

`load_snapshot()` is the stable replay boundary: it strictly loads the manifest and the complete bounded event chain, validates hashes, reduces it, and returns the uniform `EngagementSnapshot(engagement_id, revision, manifest, events, state)`. M6B/M6C perform reads through that public snapshot and bounded evidence API. M6C may receive a dedicated sealed atomic report/promotion commit capability, but it never imports generic filesystem helpers. `EngagementSettlementPort`, `SettlementReason`, and `ClosureFinalizer` are dependency-inversion seams declared by M6A. Implementations may depend on planning/reporting, while M6A never imports those packages and never calls either seam while a journal/source/projection lock is held.

## File Map

Create:

```text
src/sedna/engagement/__init__.py
src/sedna/engagement/models.py
src/sedna/engagement/normalization.py
src/sedna/engagement/events.py
src/sedna/engagement/reducer.py
src/sedna/engagement/repository.py
src/sedna/engagement/evidence.py
src/sedna/engagement/logbook.py
src/sedna/engagement/sources.py
src/sedna/engagement/service.py
src/sedna/engagement/hades_adapter.py
tests/engagement/__init__.py
tests/engagement/conftest.py
tests/engagement/test_models.py
tests/engagement/test_normalization.py
tests/engagement/test_reducer.py
tests/engagement/test_repository.py
tests/engagement/test_evidence_logbook.py
tests/engagement/test_sources.py
tests/engagement/test_service.py
tests/engagement/test_hades_adapter.py
tests/engagement/simulated_hades.py
tests/engagement/test_m6a_replay.py
tests/test_plugin_engagement.py
docs/llm/sedna-engagement-tools.md
```

Modify:

```text
src/sedna/plugin.py
src/sedna/__init__.py
plugin.yaml
pyproject.toml
tests/test_plugin.py
tests/test_plugin_knowledge.py
tests/test_plugin_knowledge_root.py
README.md
```

---

### Task 1: Versioned Engagement, Lane, Scope, and Event Contracts

**Files:**
- Create: `src/sedna/engagement/models.py`
- Create: `src/sedna/engagement/normalization.py`
- Create: `src/sedna/engagement/events.py`
- Create: `src/sedna/engagement/__init__.py`
- Create: `tests/engagement/__init__.py`
- Create: `tests/engagement/conftest.py`
- Create: `tests/engagement/test_models.py`
- Create: `tests/engagement/test_normalization.py`

**Interfaces:**
- Consumes: `sedna.knowledge.retrieval.AuthorizationScope`, `AuthorizationState`, and `ValidatedTarget`.
- Produces: dependency-neutral bounded `sanitize_host_arguments()`/`normalize_host_payload()`,
  `ExecutionLaneKey`, `ScopeReference`, `ProofRequirement`, content-addressed `EvidenceId`,
  `EngagementManifest`, `JournalRevision`, `JournalEventDraft`, `JournalEvent`,
  `EvidenceReference`, `EngagementState`, `EngagementSnapshot`, `ActiveDecision`, and all closed
  M6A payload models.

- [ ] **Step 1: Add deterministic test factories**

Create `tests/engagement/conftest.py` with fixed UTC time, UUIDs, authorized scope, lane, manifest, and draft factories. Do not use wall-clock time in contract tests. Export `fixed_clock`, `fixed_uuid_factory`, `new_lane`, `opened_draft`, `lane_bound_draft`, `initial_drafts`, `decision_draft`, `user_note_draft`, `evidence_attached_draft`, `tool_started`, `tool_completed`, `tool_terminated`, `closure_requested`, `event_chain`, and `next_event`; `initial_drafts(manifest, lane)` returns the opening and exact initial lane binding in that order, while `closure_requested(..., origin="manual")` permits the explicit proof-settlement fixture override. Every later repository creation test uses this pair instead of assembling a partially initialized engagement.

```python
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from sedna.engagement import (
    EngagementManifest,
    ExecutionLaneKey,
    HostKind,
    ProofRequirement,
)
from sedna.knowledge.retrieval import AuthorizationScope, AuthorizationState, ValidatedTarget


FIXED_TIME = datetime(2026, 8, 11, 12, 30, tzinfo=UTC)
ENGAGEMENT_ID = UUID("11111111-1111-4111-8111-111111111111")


@pytest.fixture
def authorized_scope() -> AuthorizationScope:
    return AuthorizationScope(
        state=AuthorizationState.AUTHORIZED,
        exact_targets=(ValidatedTarget.parse("192.0.2.44"),),
    )


@pytest.fixture
def lane() -> ExecutionLaneKey:
    return ExecutionLaneKey(
        host_kind=HostKind.HADES,
        session_id="session-orion",
        task_id="task-root",
    )


@pytest.fixture
def manifest(authorized_scope: AuthorizationScope) -> EngagementManifest:
    return EngagementManifest(
        engagement_id=ENGAGEMENT_ID,
        display_name="HTB-Orion",
        initial_objective="Obtain the user and root flags",
        initial_scope=authorized_scope,
        required_proofs=(
            ProofRequirement(
                proof_id="user-flag",
                kind="flag",
                description="A valid HTB user flag",
            ),
            ProofRequirement(
                proof_id="root-flag",
                kind="flag",
                description="A valid HTB root flag",
            ),
        ),
        created_at=FIXED_TIME,
        created_by_host={"kind": "hades", "adapter_version": "1"},
    )
```

- [ ] **Step 2: Write failing strict-model and scope-reference tests**

Create `tests/engagement/test_models.py` with these initial tests:

```python
from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from sedna.engagement import (
    ENGAGEMENT_MANIFEST_SCHEMA_VERSION,
    EngagementManifest,
    ExecutionLaneKey,
    HostKind,
    scope_references,
)
from sedna.knowledge.retrieval import AuthorizationScope, AuthorizationState


def test_manifest_requires_name_objective_utc_and_authorized_scope(manifest) -> None:
    assert manifest.schema_version == ENGAGEMENT_MANIFEST_SCHEMA_VERSION
    assert manifest.display_name == "HTB-Orion"
    assert manifest.created_at.utcoffset().total_seconds() == 0

    with pytest.raises(ValidationError):
        EngagementManifest.model_validate(
            {
                **manifest.model_dump(mode="json"),
                "display_name": "   ",
            }
        )
    with pytest.raises(ValidationError):
        EngagementManifest.model_validate(
            {
                **manifest.model_dump(mode="json"),
                "initial_scope": AuthorizationScope(state=AuthorizationState.UNKNOWN),
            }
        )
    with pytest.raises(ValidationError):
        EngagementManifest.model_validate(
            {
                **manifest.model_dump(mode="json"),
                "created_at": datetime(2026, 8, 11, 12, 30).isoformat(),
            }
        )


def test_execution_lane_uses_explicit_root_task_fallback() -> None:
    lane = ExecutionLaneKey.from_host(
        host_kind=HostKind.HADES,
        session_id="session-a",
        task_id="",
    )
    assert lane.task_id == "root:session-a"
    assert lane.stable_key.startswith("lane-")


def test_scope_references_are_stable_and_normalized(authorized_scope) -> None:
    first = scope_references(authorized_scope)
    second = scope_references(authorized_scope)

    assert first == second
    assert [(item.kind, item.value) for item in first] == [("exact_target", "192.0.2.44")]
    assert first[0].reference_id.startswith("scope-")


def test_proof_requirements_are_explicit_unique_and_may_be_empty(manifest) -> None:
    assert [item.proof_id for item in manifest.required_proofs] == [
        "user-flag",
        "root-flag",
    ]

    no_automatic_close = manifest.model_copy(update={"required_proofs": ()})
    assert no_automatic_close.required_proofs == ()

    duplicate = manifest.model_dump(mode="json")
    duplicate["required_proofs"].append(duplicate["required_proofs"][0])
    with pytest.raises(ValidationError, match="proof_id"):
        EngagementManifest.model_validate(duplicate)
```

- [ ] **Step 3: Write failing event-envelope and correlation tests**

Append tests that prove payload closure, event/payload matching, dependency-neutral sanitizer
behavior, and the three correlation outcomes. `ToolCorrelation.from_hook()` accepts only the
already sanitized/bounded `SanitizedHostValue`; it has no raw-arguments parameter:

```python
from sedna.engagement import (
    CorrelationKind,
    EngagementOpenedPayload,
    JournalEventDraft,
    ToolCorrelation,
    ToolCallStartedPayload,
)


def test_event_type_must_match_closed_payload(lane) -> None:
    with pytest.raises(ValidationError):
        JournalEventDraft(
            lane=lane,
            actor="host_agent",
            type="engagement_opened",
            payload=ToolCallStartedPayload(
                call_id="call-1",
                tool_name="terminal",
                correlation=ToolCorrelation.uncertain("missing_stable_identity"),
                safe_arguments={},
            ),
        )


def test_tool_correlation_prefers_host_tool_call_id(lane) -> None:
    correlation = ToolCorrelation.from_hook(
        lane=lane,
        tool_name="terminal",
        sanitized_arguments=sanitize_host_arguments({"command": "id"}),
        tool_call_id="provider-call-7",
        turn_id="turn-1",
        api_request_id="request-1",
        api_call_count=2,
    )
    assert correlation.kind is CorrelationKind.TOOL_CALL_ID
    assert correlation.deduplication_allowed is True


def test_tool_correlation_uses_true_host_tool_ordinal_when_supplied(lane) -> None:
    correlation = ToolCorrelation.from_hook(
        lane=lane,
        tool_name="terminal",
        sanitized_arguments=sanitize_host_arguments({"command": "id"}),
        tool_call_id="",
        turn_id="turn-1",
        api_request_id="request-1",
        api_call_count=2,
        tool_call_ordinal=1,
    )
    assert correlation.kind is CorrelationKind.API_ATTEMPT
    assert correlation.deduplication_allowed is True


def test_hades_attempt_counter_is_not_a_tool_ordinal(lane) -> None:
    correlation = ToolCorrelation.from_hook(
        lane=lane,
        tool_name="terminal",
        sanitized_arguments=sanitize_host_arguments({"command": "id"}),
        tool_call_id="",
        turn_id="turn-1",
        api_request_id="request-1",
        api_call_count=2,
    )
    assert correlation.kind is CorrelationKind.UNCERTAIN
    assert correlation.deduplication_allowed is False


def test_incomplete_host_identity_is_typed_uncertain_without_deduplication(lane) -> None:
    correlation = ToolCorrelation.from_hook(
        lane=lane,
        tool_name="terminal",
        sanitized_arguments=sanitize_host_arguments({"command": "id"}),
        tool_call_id="",
        turn_id="",
        api_request_id="",
        api_call_count=None,
    )
    assert correlation.kind is CorrelationKind.UNCERTAIN
    assert correlation.reason == "missing_stable_identity"
    assert correlation.deduplication_allowed is False
```

- [ ] **Step 4: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/pytest tests/engagement/test_models.py -v
```

Expected: collection fails because `sedna.engagement` does not exist.

- [ ] **Step 5: Implement strict shared models**

Create `models.py` with version constants, bounded types, UTC validation, normalized display text, stable hashes, and these public contracts:

```python
ENGAGEMENT_MANIFEST_SCHEMA_VERSION = "sedna.engagement-manifest.v1"
EVENT_ENVELOPE_SCHEMA_VERSION = "sedna.journal-event.v1"
ENGAGEMENT_STATE_PROJECTION_SCHEMA_VERSION = "sedna.engagement-state.v1"
LANE_POLICY_VERSION = "sedna.execution-lane.v1"
CORRELATION_POLICY_VERSION = "sedna.tool-correlation.v1"
MAX_JOURNAL_EVENT_BYTES = 65_536
MAX_JOURNAL_BATCH_EVENTS = 512
MAX_MANIFEST_BYTES = 1 * 1024 * 1024
MAX_JOURNAL_BYTES = 256 * 1024 * 1024
MAX_JOURNAL_EVENTS = 100_000
MAX_ENGAGEMENTS = 10_000
MAX_ENGAGEMENT_DIRECTORY_ENTRIES = 11_000
MAX_EVIDENCE_OBJECTS = 110_000
MAX_EVIDENCE_DIRECTORY_ENTRIES = 120_000
MAX_EVIDENCE_ITEM_BYTES = 64 * 1024 * 1024
MAX_EVIDENCE_ENGAGEMENT_BYTES = 4 * 1024 * 1024 * 1024
MAX_JOURNAL_HEAD_BYTES = 16 * 1024
MAX_CREATE_INTENT_BYTES = (
    4 * ((MAX_MANIFEST_BYTES + 2 * (MAX_JOURNAL_EVENT_BYTES + 1) + 2) // 3)
    + 128 * 1024
)
MAX_PENDING_APPEND_BYTES = 4 * (
    (MAX_JOURNAL_BATCH_EVENTS * (MAX_JOURNAL_EVENT_BYTES + 1) + 2) // 3
) + 128 * 1024
MAX_TAIL_RECOVERY_INTENT_BYTES = 256 * 1024
MAX_CAPTURE_INTENT_BYTES = 64 * 1024
MAX_RECOVERABLE_TAIL_BYTES = MAX_JOURNAL_EVENT_BYTES
MAX_DERIVED_PROJECTION_BYTES = 64 * 1024 * 1024
MAX_TOOL_NAME_CHARS = 256
MAX_HOST_CORRELATION_ID_CHARS = 512
MAX_TOOL_CALL_ORDINAL = 65_535
MAX_API_CALL_COUNT = 1_000_000
MAX_TOOL_DURATION_MS = 86_400_000
MAX_REQUIRED_PROOFS = 64
MAX_SCOPE_EVENT_BYTES = 60 * 1024
MAX_IN_FLIGHT_CALLS = 512
MAX_SETTLEMENT_PENDING_RANGES = 2_147_483_647
MAX_HOST_RESULT_BYTES = 256 * 1024
MAX_PUBLIC_INVENTORY_ITEMS = 64
MAX_HEALTH_ENTRIES_PER_STORE = 512
MAX_HEALTH_ENTRIES_TOTAL = 4_096
MAX_HEALTH_OCCURRENCES = 2_147_483_647


class HostKind(StrEnum):
    HADES = "hades"
    HERMES = "hermes"
    OTHER = "other"


class EngagementStatus(StrEnum):
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED_UNVERIFIED = "closed_unverified"
    CLOSED_VERIFIED = "closed_verified"
    ABANDONED = "abandoned"


class ExecutionLaneKey(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    host_kind: HostKind
    session_id: Annotated[str, Field(min_length=1, max_length=512)]
    task_id: Annotated[str, Field(min_length=1, max_length=512)]

    @classmethod
    def from_host(
        cls,
        *,
        host_kind: HostKind,
        session_id: str,
        task_id: str | None,
    ) -> "ExecutionLaneKey":
        clean_session = session_id.strip()
        if not clean_session:
            raise ValueError("session_id is required")
        clean_task = (task_id or "").strip() or f"root:{clean_session}"
        return cls(host_kind=host_kind, session_id=clean_session, task_id=clean_task)

    @property
    def stable_key(self) -> str:
        payload = f"{self.host_kind.value}\0{self.session_id}\0{self.task_id}".encode()
        return f"lane-{sha256(payload).hexdigest()[:32]}"


class ScopeReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    reference_id: Annotated[str, Field(pattern=r"^scope-[0-9a-f]{32}$")]
    kind: Literal["exact_target", "cidr", "hostname", "url_origin", "generic_id"]
    value: Annotated[str, Field(min_length=1, max_length=2048)]


EvidenceId = Annotated[
    str,
    Field(pattern=r"^evidence-sha256-[0-9a-f]{64}$"),
]

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PendingSubjectCursor = Annotated[str, Field(pattern=r"^pending-[0-9a-f]{64}$")]


def validate_confined_relative_path(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("confined relative path must be a string")
    if not value or len(value) > 4096 or "\0" in value or "\\" in value:
        raise ValueError("invalid confined relative path")
    if value.startswith("/") or value != unicodedata.normalize("NFC", value):
        raise ValueError("invalid confined relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("invalid confined relative path")
    if re.match(r"^[A-Za-z]:", parts[0]):
        raise ValueError("invalid confined relative path")
    if PurePosixPath(value).as_posix() != value:
        raise ValueError("invalid confined relative path")
    return value


ConfinedRelativePath = Annotated[
    str,
    BeforeValidator(validate_confined_relative_path),
    Field(min_length=1, max_length=4096),
]


class ProofRequirement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    proof_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
    kind: Literal["flag", "access", "custom"]
    description: Annotated[str, Field(min_length=1, max_length=512)]


class CaptureLimitation(StrEnum):
    PROVIDER_OR_HOST_SECRET_REDACTED = (
        "provider_or_host_secret_redacted_before_persistence"
    )
    HOST_REPORTED_TRUNCATION = "host_reported_truncation"
    EXTERNAL_ARTIFACT_NOT_CAPTURED = "external_artifact_not_captured"


SettlementSafeCode = Literal[
    "evidence_budget_exhausted",
    "interpretation_incomplete",
    "interpretation_failed",
    "journal_unavailable",
    "journal_corrupt",
    "settlement_unavailable",
]


class JournalRevision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    sequence: int = Field(ge=0)
    event_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
```

`MAX_JOURNAL_EVENT_BYTES` is the maximum UTF-8 byte length of one fully materialized canonical
event line excluding its trailing newline; `MAX_JOURNAL_BATCH_EVENTS` is the maximum number of
drafts accepted by one atomic batch. `MAX_MANIFEST_BYTES`, `MAX_JOURNAL_BYTES`,
`MAX_JOURNAL_EVENTS`, `MAX_ENGAGEMENTS`, `MAX_ENGAGEMENT_DIRECTORY_ENTRIES`,
`MAX_EVIDENCE_OBJECTS`, and the remaining constants above are hard
per-file/per-root/per-engagement inventory limits checked by stat plus bounded incremental parsing
before allocation. `MAX_EVIDENCE_OBJECTS` counts canonical/recoverable orphan blobs plus every
payload-bearing capture temp or quarantine entry, inode-deduplicated under the evidence lock;
`MAX_EVIDENCE_DIRECTORY_ENTRIES` bounds the complete mixed directory scan including logbooks,
locks/intents, and temps. Export the journal/event/evidence/projection constants as the single authoritative limits
consumed by M6B/M6C—downstream plans must import them rather than redefine magic values. Hitting a
limit returns a closed `journal_unavailable`/typed repository limit error without partial mutation.
Add strict under/over manifest, full-chain byte/event, registry-inventory, event-line, and batch
boundary fixtures. Pre-stat and incrementally read `journal-head.json`, `.pending-append.json`,
`.tail-recovery.json`, `.capture-*.json`, and every derived projection against their exact maxima before JSON
allocation; limit+1 recovery files fail closed without mutation.

Pending/create intents store canonical manifest/event bytes as base64 fields, never JSON-escaped
raw lines; the maxima above include worst-case base64 expansion plus fixed metadata overhead.
Under/one-byte-over fixtures use quote/backslash-heavy valid events and prove every admissible
512-event transaction is representable. A recoverable incomplete journal suffix is at most
`MAX_RECOVERABLE_TAIL_BYTES`; a larger no-newline tail is corruption rather than impossible
evidence capture.

`validate_confined_relative_path` accepts only canonical POSIX root-relative strings: no leading
slash, empty/`.`/`..` component, NUL, backslash, drive-letter prefix, or normalization change.
This type validates durable references before any descriptor IO; repositories still derive paths
internally and never trust a caller-supplied location. Export it for M6C report/promotion refs and
add malicious absolute, traversal, `C:/x` and drive-relative `C:foo/bar`, backslash,
empty-component, and NUL model tests.

`MAX_HOST_RESULT_BYTES` applies to the complete compact UTF-8 JSON returned by every M6A
engagement-adapter tool and by later `sedna_plan_next`/manage extensions, including its envelope;
pre-M6 learn/retrieve tools remain under their existing contracts. `MAX_PUBLIC_INVENTORY_ITEMS` bounds every page of lane, decision, and
in-flight-call identifiers; the authoritative snapshot remains internal and is never serialized
whole by the adapter. The two health limits bound the in-process map independently per store and
globally.

Implement `scope_references(scope)` by expanding the already-normalized `AuthorizationScope`, sorting `(kind, value)`, and hashing `kind + NUL + value`. Define `HostIdentity`, `EngagementManifest`, `EvidenceReference`, `EvidenceSlice`, `ActiveDecision`, `ClosureBarrier`, `LaneBinding`, and `EngagementState` in `models.py` with `frozen=True`, `extra="forbid"`, and `revalidate_instances="always"`. Every evidence-bearing field uses `EvidenceId`; the ID is exactly `evidence-sha256-<content digest>` and is never a random UUID or path-derived identity. `EvidenceReference.capture_limitations` is a sorted unique tuple of `CaptureLimitation`, empty for a byte-exact capture. Map only explicit typed host metadata to `HOST_REPORTED_TRUNCATION` or `EXTERNAL_ARTIFACT_NOT_CAPTURED`; never infer either from result prose. Preserve these limitations through attachment descriptors, logbooks, reports, and replay, with exact host-metadata mapping tests.

`EngagementManifest` must enforce authorized non-empty initial scope, nonblank name/objective, UTC-aware `created_at`, immutable UUID identity, and at most `MAX_REQUIRED_PROOFS` unique `required_proofs` IDs in the supplied order. It defaults `required_proofs` to an empty tuple; this means “no proof-driven automatic close,” not “objective already satisfied.” Export the constant as the one M6B/M6C proof-list bound and add 64/65 schema tests. `EngagementState` must expose `revision`, `status`, `scope_references`, `bound_lanes`, `active_decisions`, `in_flight_call_ids`, `closure`, `closure_ready`, `projection_version`, and `journal_healthy`.

Bound `EngagementState.in_flight_call_ids` and `ClosureRequestedPayload.in_flight_call_ids` to
`MAX_IN_FLIGHT_CALLS`. Before argument evidence capture, closure cancellation, or any pre-tool
append, prospectively reject a genuinely new 513th start with typed
`in_flight_limit_exceeded`/bounded health; exact idempotent retries remain no-ops. Materialize the
maximal 512-ID closure envelope and assert it remains below `MAX_JOURNAL_EVENT_BYTES`; add 512/513
concurrent-start mutation-spy and close tests.
`request_close` admits at most `MAX_IN_FLIGHT_CALLS - 1` current calls, reserving one slot so the
first genuinely new call observed while closing can atomically cancel the barrier and start at 512.
At active 512, close is rejected with no barrier; a fail-open host call can therefore never execute
while an uncancellable closure remains. Add 511-close -> cancel+start and 512-close rejection tests.

Before create-root IO or a scope-change append, canonicalize the exact prospective scope-bearing
payload/envelope and require it `<= MAX_SCOPE_EVENT_BYTES` (leaving M6A envelope/hash headroom below
`MAX_JOURNAL_EVENT_BYTES`). The same helper handles opening and changed scope; a schema-valid but
aggregate-oversized authorization returns `invalid_input` with no root/file/event mutation. Add
exact-under/one-byte-over fixtures using the maximum-count authorization shape.

- [ ] **Step 6: Implement closed event payloads and envelope validation**

Create `events.py`. Define `CONTROL_TOOL_POLICY_VERSION` and `CONTROL_TOOL_NAMES` there beside `ControlToolInvokedPayload`, then export them from `sedna.engagement`; the adapter imports/re-exports rather than redefining them, avoiding an events-to-adapter cycle. Every payload carries a literal `kind`; `JournalEventDraft.type` and `JournalEvent.type` must equal `payload.kind`. Define the following exact M6A payload classes and fields:

```python
EngagementOpenedPayload(kind, scope_references)
EngagementResumedPayload(kind, reason)
LaneBoundPayload(kind, lane, binding_reason)
LaneUnboundPayload(kind, lane, reason)
ChildLaneLinkedPayload(kind, parent_session_id, child_session_id, child_subagent_id)
SessionStartedPayload(kind, model, platform)
SessionCheckpointedPayload(kind, completed, interrupted, reason)
SessionFinalizedPayload(kind, reason, settlement_status, pending_range_count, next_pending_offset, next_pending_subject, pending_inventory_sha256, safe_code)
ObjectiveChangedPayload(kind, objective, authorization_basis)
ScopeChangedPayload(kind, scope, scope_references, authorization_basis)
DecisionRecordedPayload(kind, decision_id, proposal_id, strategy, rationale, host_adapted_command: HostAdaptedCommandRecord | None)
AgentDeviationRecordedPayload(kind, decision_id, strategy, rationale)
ToolCallStartedPayload(kind, call_id, tool_name, correlation, safe_arguments, argument_evidence_id: EvidenceId | None, argument_attachment_event_id: UUID | None, decision_id)
ToolCallCompletedPayload(kind, call_id, correlation, technical_status, duration_ms, evidence_id: EvidenceId | None, evidence_attachment_event_id: UUID | None, error_type, possible_terminal_evidence)
ToolCallTerminatedPayload(kind, call_id, resolution, reason)
EvidenceAttachedPayload(kind, evidence)
EvidenceCaptureFailedPayload(kind, call_id, capture_role, reason_code, observed_size, observed_sha256)
UnmatchedToolCompletionPayload(kind, correlation, technical_status, duration_ms, evidence_id: EvidenceId | None, evidence_attachment_event_id: UUID | None, reason_code)
UnplannedActionPayload(kind, call_id, reason)
ControlToolInvokedPayload(kind, control_tool, policy_version, correlation)
ClosureRequestedPayload(kind, terminal_watermark, in_flight_call_ids, reason, origin: Literal["manual", "proof_settlement"] = "manual")
ClosureCancelledPayload(kind, closure_event_id, reason)
EngagementReopenedPayload(kind, reason)
EngagementAbandonedPayload(kind, reason)
SourceSuggestedPayload(kind, source_id, locator)
RecoveryWarningPayload(kind, reason_code, evidence_id: EvidenceId)
UncertainCorrelationPayload(kind, call_id, reason_code)
UserNotePayload(kind, note)
```

Use a discriminated `EventPayload` union and a closed `EventType` enum. `JournalEventDraft`
contains `event_id: UUID | None = None`, `lane: ExecutionLaneKey | None = None`, optional
turn/actor IDs, actor, type, payload, optional typed system correlation, and
optional idempotency key. The repository preserves a supplied preallocated event ID or assigns one
when absent; it rejects duplicate IDs inside the batch/chain. An idempotent retry with a supplied
ID must match the existing event ID and complete canonical draft, while either an ID or key
collision with different bytes fails. `JournalEvent` makes `event_id` mandatory and adds envelope
version, sequence, UTC timestamp, engagement UUID, previous hash, and event hash. Hash fields accept
only lowercase SHA-256. Define `EngagementSnapshot` after `JournalEvent` in `events.py` so there is
no models/events import cycle. It contains `engagement_id`, `revision`, the deeply validated
manifest, complete ordered event tuple, and replayed state, and validates identity/revision
agreement across all fields.

Define dependency-neutral `HostAdaptedCommandRecord` as a frozen, extra-forbid private journal
record with `origin: Literal["host_adapted"]`, `command_template` (1..8192 chars), unique ordered
`placeholder_names` (at most 32 items, each 1..64 chars), optional `adaptation_note` (at most 2048
chars), and `requires_validation: Literal[True] = True`. Its canonical JSON is at most 16 KiB; it
contains no rendered preview, executed output, provider/host secret, or universal-instruction flag.
It is private decision context and promotion may consume it only through later sanitization.

Lane/tool/decision/session payloads require a non-null exact lane. A system-owned recovery,
proof-settlement, lifecycle, planning, reporting, or promotion event instead requires
`SystemCorrelation(kind="system", source="recovery|proof_settlement|lifecycle|planning|reporting|promotion",
operation_id=UUID)` and forbids a fabricated host lane; ordinary user/host events forbid system
correlation. Tests deep-validate both sides of this XOR.

Each evidence ID and attachment-event ID pair is all-or-none and cross-validates against the exact
preceding `evidence_attached` draft in the same atomic batch. The adapter preallocates that event
UUID before constructing the tool event. Content-addressed `EvidenceId` deduplicates bytes only;
the attachment event is the occurrence identity used by interpretation, outcome, and report replay.
Add a regression where two distinct calls return byte-identical output: one sidecar is reused, but
two attachment events and two independent outcome subjects remain.

`SessionFinalizedPayload.settlement_status` is exactly
`not_configured|complete|incomplete|failed|unavailable`; its remaining fields mirror the closed
`EngagementSettlementOutcome`. `not_configured|complete` requires zero pending ranges, no next
offset/subject/digest, and no safe code; `incomplete` requires positive true pending count, an
inventory digest, the optional first-page byte offset and next-subject cursor, and an
incomplete/budget safe code. `failed` requires `safe_code="interpretation_failed"` and either a
complete zero/none pending shape or the exact positive true pending count, inventory digest, and
optional cursor/offset observed before failure. `unavailable` requires a zero/none pending shape
and exactly `journal_unavailable|journal_corrupt|settlement_unavailable`. Neither permits raw
exception text.
Both `SessionFinalizedPayload` and `EngagementSettlementOutcome` use the exported
`MAX_SETTLEMENT_PENDING_RANGES` upper bound and cap `next_pending_offset` at
`MAX_EVIDENCE_ITEM_BYTES`; strict validation rejects booleans and oversized integers before event
serialization. `EvidenceCaptureFailedPayload.capture_role` is `arguments|result`; its
closed reason codes are
`item_quota_exceeded|engagement_quota_exceeded|evidence_object_limit_exceeded|normalization_limit_exceeded|unsupported_value|serialization_failed|external_artifact_unavailable`.
`observed_size` and `observed_sha256` are optional together: they are required only after safe byte
normalization (including quota failures) and absent for unsupported/serialization failures, where
neither `repr(value)`, raw value, nor a digest of it may be persisted. It is capture audit, not a
terminal call by itself.

Implement `ToolCorrelation.from_hook()` with this order:

1. nonblank `tool_call_id`;
2. complete session/task/turn/request tuple plus a true host `tool_call_ordinal`, tool name, and
   canonical sanitized-argument digest;
3. `uncertain` with a closed reason and `deduplication_allowed=False`.

Implement the shared structural redaction policy, iterative cycle/depth/node/byte bounds, and
canonical sanitized digest in dependency-neutral `engagement/normalization.py` during Task 1.
Neither `events.py` nor this module imports evidence/storage. A strict `SanitizedHostValue` carries
only the sanitized bounded structure plus its canonical digest/bytes metadata; construction is
module-controlled. `ToolCorrelation.from_hook()` receives that type or a typed normalization
failure and never traverses/hashes raw hook data. Task 4 reuses and extends this one implementation
for sidecar streaming; Task 7 calls it before every operational or control correlation. Add Task 1
tests for nested provider-secret removal, absence of raw/digest bytes, cycle/depth/node rejection,
and deterministic mapping order.

`ToolCorrelation` stores the exact lane stable key, kind, normalized host identity fields,
tool-name digest, sanitized-argument digest, stable correlation key, and deduplication flag/reason.
Even the preferred host ID is namespaced as
`sha256("tool-call-id\0" + lane.stable_key + "\0" + host_tool_call_id)`; the fallback hashes the
lane stable key plus complete host tuple including the true ordinal, tool name, and sanitized
argument digest. Never treat `api_call_count` as the ordinal: two identical calls in one response
may share it. Until the host emits `tool_call_ordinal`, absence of `tool_call_id` always takes the
uncertain branch. Derive the
bounded journal `call_id` from that stable key (`call-` plus lowercase SHA-256), never from the raw
provider ID. For an uncertain correlation, derive a unique non-deduplicable `call_id` from the
preallocated `tool_call_started.event_id`; candidate matching uses the exact lane key, tool name,
sanitized argument digest, turn/API fields that are actually present, and nonterminal status. A
post links only if those fields yield exactly one candidate. Cross-journal post lookup returns typed
ambiguous on more than one candidate and never picks first. Add tests where two sessions reuse the
same host `tool_call_id` (different correlation/call IDs) and where two identical uncertain pre
calls with the same session/task/turn/request/`api_call_count` get distinct IDs and their completion
remains ambiguous.

Before encoding/hashing, require nonblank `tool_name` at most `MAX_TOOL_NAME_CHARS`, every supplied
raw `tool_call_id`/turn/request identity at most `MAX_HOST_CORRELATION_ID_CHARS`,
`0 <= tool_call_ordinal <= MAX_TOOL_CALL_ORDINAL`,
`0 <= api_call_count <= MAX_API_CALL_COUNT`, and later
`0 <= duration_ms <= MAX_TOOL_DURATION_MS`; session/task IDs already use the exact lane bounds.
Reject booleans in integer fields, NaN, non-JSON arguments, and one-over/huge integers before any
encode/hash/allocation. The argument digest is computed only from the recursively sanitized argument structure, after provider/host secrets have been replaced; neither the correlation nor an error record may contain a digest of a removed secret value. Add every exact-bound/one-over test.

`ToolCallCompletedPayload.technical_status` is one of `returned`, `blocked`, `cancelled`, `error`, or `unknown`. It describes host delivery only and is never treated as strategic success. `ToolCallTerminatedPayload.resolution` is exactly `timed_out` or `abandoned`; both are terminal for the closure barrier and require a nonblank human/host reason. `ControlToolInvokedPayload.control_tool` accepts only the exact versioned `CONTROL_TOOL_NAMES` set.

- [ ] **Step 7: Export only the stable public contracts**

Create `src/sedna/engagement/__init__.py` and explicitly export the version constants, shared models, payloads, and helper functions. Do not export repository-private filesystem helpers.

```python
from sedna.engagement.events import EngagementSnapshot, JournalEvent, JournalEventDraft
from sedna.engagement.models import (
    ExecutionLaneKey,
    JournalRevision,
    ScopeReference,
)

__all__ = [
    "EngagementSnapshot",
    "ExecutionLaneKey",
    "JournalEvent",
    "JournalEventDraft",
    "JournalRevision",
    "ScopeReference",
]
```

Include the remaining public model and enum names in `__all__` as they are introduced, including
`EvidenceId`, `Sha256Hex`, `PendingSubjectCursor`, `ConfinedRelativePath`, `ProofRequirement`, and `CaptureLimitation`; keep
the six names above stable for M6B/M6C. Engagement-layer code must use this dependency-neutral
digest primitive rather than importing through `sedna.knowledge`.

- [ ] **Step 8: Run tests and Ruff to verify GREEN**

Run:

```bash
.venv/bin/pytest tests/engagement/test_models.py tests/engagement/test_normalization.py -v
.venv/bin/ruff check src/sedna/engagement/models.py src/sedna/engagement/normalization.py src/sedna/engagement/events.py tests/engagement/test_models.py tests/engagement/test_normalization.py
```

Expected: all tests pass and Ruff reports no errors.

- [ ] **Step 9: Commit the contracts**

```bash
git add src/sedna/engagement/__init__.py src/sedna/engagement/models.py src/sedna/engagement/normalization.py src/sedna/engagement/events.py tests/engagement/__init__.py tests/engagement/conftest.py tests/engagement/test_models.py tests/engagement/test_normalization.py
git commit -m "feat(engagement): define versioned journal contracts"
```

### Task 2: Pure Replay Reducer and Closure Barrier

**Files:**
- Create: `src/sedna/engagement/reducer.py`
- Create: `tests/engagement/test_reducer.py`

**Interfaces:**
- Consumes: `EngagementManifest`, `JournalEvent`, event payloads, `ExecutionLaneKey`.
- Produces: `reduce_engagement(manifest, events) -> EngagementState` and `EngagementReplayError`.

- [ ] **Step 1: Write failing lifecycle replay tests**

Create helper `event()` in the test file that computes valid sequence/hash values through the public `build_event_for_test()` helper exposed only from `tests/engagement/conftest.py`. Then add:

```python
def test_open_bind_decide_and_start_call_reduces_to_active_state(manifest, lane) -> None:
    events = event_chain(
        manifest,
        opened(scope_references=scope_references(manifest.initial_scope)),
        lane_bound(lane),
        decision_recorded(lane, decision_id="decision-1"),
        tool_started(lane, call_id="call-1", decision_id="decision-1"),
    )

    state = reduce_engagement(manifest, events)

    assert state.status == "active"
    assert state.bound_lanes == (LaneBinding(lane=lane, engagement_id=manifest.engagement_id),)
    assert state.active_decisions[0].decision_id == "decision-1"
    assert state.in_flight_call_ids == ("call-1",)


def test_close_waits_at_barrier_until_every_captured_call_is_terminal(manifest, lane) -> None:
    events = event_chain(
        manifest,
        opened(scope_references=scope_references(manifest.initial_scope)),
        lane_bound(lane),
        tool_started(lane, call_id="call-1"),
        closure_requested(watermark=3, in_flight=("call-1",)),
    )
    waiting = reduce_engagement(manifest, events)
    ready = reduce_engagement(
        manifest,
        (*events, next_event(events, tool_completed(lane, call_id="call-1"))),
    )

    assert waiting.status == "closing"
    assert waiting.closure.origin == "manual"
    assert waiting.closure_ready is False
    assert ready.status == "closing"
    assert ready.closure_ready is True


@pytest.mark.parametrize("resolution", ["timed_out", "abandoned"])
def test_explicit_terminal_resolution_releases_a_crashed_pre_hook_call(
    manifest, lane, resolution
) -> None:
    events = event_chain(
        manifest,
        opened(scope_references=scope_references(manifest.initial_scope)),
        lane_bound(lane),
        tool_started(lane, call_id="call-without-post"),
        closure_requested(watermark=3, in_flight=("call-without-post",)),
    )
    resolved = reduce_engagement(
        manifest,
        (
            *events,
            next_event(
                events,
                tool_terminated(
                    lane,
                    call_id="call-without-post",
                    resolution=resolution,
                    reason="host process ended before post_tool_call",
                ),
            ),
        ),
    )
    assert resolved.in_flight_call_ids == ()
    assert resolved.closure_ready is True


def test_replay_preserves_proof_settlement_closure_origin(manifest, lane) -> None:
    state = reduce_engagement(
        manifest,
        event_chain(
            manifest,
            opened(scope_references=scope_references(manifest.initial_scope)),
            lane_bound(lane),
            closure_requested(
                watermark=2,
                in_flight=(),
                origin="proof_settlement",
            ),
        ),
    )
    assert state.closure.origin == "proof_settlement"
```

- [ ] **Step 2: Write failing cancellation, lane-isolation, and invalid-stream tests**

```python
def test_new_operational_call_requires_cancellation_before_start(manifest, lane) -> None:
    invalid = event_chain(
        manifest,
        opened(scope_references=scope_references(manifest.initial_scope)),
        lane_bound(lane),
        closure_requested(watermark=2, in_flight=()),
        tool_started(lane, call_id="late-call"),
    )
    with pytest.raises(EngagementReplayError, match="closure must be cancelled"):
        reduce_engagement(manifest, invalid)


def test_decisions_are_isolated_per_execution_lane(manifest, lane) -> None:
    child = ExecutionLaneKey.from_host(
        host_kind=lane.host_kind,
        session_id=lane.session_id,
        task_id="task-child",
    )
    state = reduce_engagement(
        manifest,
        event_chain(
            manifest,
            opened(scope_references=scope_references(manifest.initial_scope)),
            lane_bound(lane),
            lane_bound(child),
            decision_recorded(lane, decision_id="parent-decision"),
            decision_recorded(child, decision_id="child-decision"),
        ),
    )
    assert {item.lane.task_id: item.decision_id for item in state.active_decisions} == {
        "task-root": "parent-decision",
        "task-child": "child-decision",
    }


def test_reducer_rejects_gap_bad_hash_and_duplicate_terminal_call(manifest) -> None:
    valid = event_chain(
        manifest,
        opened(scope_references=scope_references(manifest.initial_scope)),
    )
    with pytest.raises(EngagementReplayError):
        reduce_engagement(manifest, (valid[0].model_copy(update={"sequence": 2}),))


def test_closure_snapshot_must_equal_the_exact_prefix(manifest, lane) -> None:
    base = event_chain(
        manifest,
        opened(scope_references=scope_references(manifest.initial_scope)),
        lane_bound(lane),
        tool_started(lane, call_id="call-1"),
    )
    with pytest.raises(EngagementReplayError, match="closure snapshot"):
        reduce_engagement(
            manifest,
            (*base, next_event(base, closure_requested(watermark=2, in_flight=()))),
        )
```

- [ ] **Step 3: Run reducer tests and verify RED**

```bash
.venv/bin/pytest tests/engagement/test_reducer.py -v
```

Expected: failure because `reduce_engagement` is not implemented.

- [ ] **Step 4: Implement deterministic replay**

Implement `reducer.py` as a pure fold with no filesystem, clock, UUID, or LLM calls:

```python
class EngagementReplayError(ValueError):
    pass


def reduce_engagement(
    manifest: EngagementManifest,
    events: Sequence[JournalEvent],
) -> EngagementState:
    validate_event_chain(manifest.engagement_id, events)
    accumulator = _Accumulator.from_manifest(manifest)
    for item in events:
        accumulator.apply(item)
    return accumulator.freeze()
```

The accumulator must enforce:

- sequence starts at one and is contiguous;
- first event is `engagement_opened` and matches manifest scope references;
- every event engagement ID and hash link matches;
- one lane binds to at most one engagement at a time;
- every lane-scoped session, decision, control, tool-start, deviation, and child-link event names a
  lane currently bound to this engagement; an unbound or never-bound lane is a replay error;
- decision replacement is lane-local;
- a completion or explicit `timed_out`/`abandoned` termination matches one nonterminal call, and duplicate/conflicting terminal events fail replay;
- a closure request requires `terminal_watermark == closure_event.sequence - 1` and
  `in_flight_call_ids` byte-for-byte equal the sorted set of calls started but not terminal at that
  exact prefix; it records its event ID, sequence watermark, exact set, and immutable
  `manual`/`proof_settlement` origin;
- closure readiness derives from completed or explicitly terminated calls, never a mutable counter;
- `tool_call_started` while closing is invalid unless immediately preceded by a
  `closure_cancelled` that cites the one current barrier event ID; no other new-work event is
  permitted in `closing`;
- every `closure_cancelled` cites the current barrier and may be used only by the service flow
  authorized for that barrier origin; a missing, stale, or already-cancelled ID fails replay;
- `engagement_reopened` restores `active` from closing, abandoned, or future closed states;
- `engagement_abandoned` remains resumable;
- M6A never reduces an internally emitted event to `closed_unverified` or `closed_verified`.

Implement a closed `EventType -> LifecycleEffect` table, explicit
`RESUMABLE_STATUSES = {active, closing, abandoned}`, and a closed status matrix; assert set-equality
with `EventType` in tests. `active` admits ordinary bound-lane work and authorized
objective/scope/lifecycle changes. `closing` admits completion/termination of calls that started
before its watermark, exact barrier cancellation, manual/proof finalization bookkeeping, abandon,
or reopen. It also admits recovery control-plane events—idempotent `engagement_resumed`,
`lane_bound|lane_unbound`, `session_started|session_checkpointed|session_finalized`, and
`control_tool_invoked`—without
cancelling the barrier; these cannot create a decision/objective/scope change or operational call.
A genuinely new operational start is accepted only in the same prospective batch after its exact
cancellation. `abandoned` permits the same recovery control plane but requires an explicit
`engagement_reopened` before any new work; `closed_unverified|closed_verified` admit no new work
until their authorized lifecycle path reopens. They do, however, admit the inert control-plane
family `lane_bound|lane_unbound`, `session_started|session_checkpointed|session_finalized`, and
`control_tool_invoked` with no lifecycle/work effect. A closed-state `lane_bound` is legal only via
an explicit UUID/name selector after the global conflict check, never fuzzy resume. This lets a new
session inspect/verify/report/reopen and lets session-wide finalization enumerate an already-closed
binding without authorizing objective/decision/operational work. Completion/termination may
finish the exact already-started call even after its lane was unbound or the engagement was
abandoned, but cannot manufacture a new call. Lane bind/unbind, closure, recovery, and lifecycle
events follow their explicitly owned transitions rather than falling through a default branch.
Add table-driven tests for every status/event family, never-bound and previously-unbound lanes,
stale/wrong-origin closure cancellation, completion after unbind/abandon, and the atomic
cancel-current-barrier-then-start exception. Add closed bind/unbind/control/session, auto-close-
during-finalize, and already-closed finalize replay cases. Add crash → new-session resume/bind → resolve an
old in-flight call → closure-ready, plus abandoned resume-inspect/reopen tests.
Also replay session-wide finalize in closing and abandoned and require one bound-lane final event
without cancellation or new work.

Sort maps by stable lane/call keys before creating the frozen state. Derive `JournalRevision` from the final event or the zero revision `sequence=0`, `event_hash="0" * 64` when validating a pre-open fixture.

- [ ] **Step 5: Run reducer tests and verify GREEN**

```bash
.venv/bin/pytest tests/engagement/test_reducer.py -v
.venv/bin/ruff check src/sedna/engagement/reducer.py tests/engagement/test_reducer.py
```

Expected: all reducer tests pass.

- [ ] **Step 6: Commit the reducer**

```bash
git add src/sedna/engagement/reducer.py tests/engagement/test_reducer.py
git commit -m "feat(engagement): replay lifecycle and closure barriers"
```

### Task 3: Descriptor-Confined Append-Only Repository

**Files:**
- Create: `src/sedna/engagement/repository.py`
- Create: `tests/engagement/test_repository.py`

**Interfaces:**
- Consumes: strict models and `reduce_engagement()`.
- Produces: `EngagementJournalRepository`, `JournalHead`, private `_EvidenceObjectStore`, `AppendResult`, `BatchAppendResult`,
  `RevisionConflictError`, `ProjectionOwnershipError`, `JournalUnavailableError`, and private
  atomic storage primitives used only inside the engagement package.

- [ ] **Step 1: Write failing create, append, hash-chain, and idempotency tests**

Create `tests/engagement/test_repository.py`:

```python
from __future__ import annotations

import os
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest

from sedna.engagement import JournalRevision
from sedna.engagement.repository import (
    EngagementJournalRepository,
    ProjectionOwnershipError,
    RevisionConflictError,
)


def test_create_commits_manifest_open_and_lane_binding_atomically(
    tmp_path, manifest, lane
) -> None:
    with EngagementJournalRepository(tmp_path / "knowledge") as repository:
        snapshot = repository.create(manifest, initial_drafts(manifest, lane))

    root = tmp_path / "knowledge" / "engagements" / str(manifest.engagement_id)
    assert snapshot.revision.sequence == 2
    assert (root / "engagement.json").is_file()
    assert (root / "events.jsonl").read_text(encoding="utf-8").count("\n") == 2
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "engagement.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "events.jsonl").stat().st_mode) == 0o600
    assert (root / "engagement-state.json").is_file()
    assert not (root / "state.json").exists()


def test_append_assigns_sequence_and_hash_from_current_tail(tmp_path, manifest, lane) -> None:
    with EngagementJournalRepository(tmp_path / "knowledge") as repository:
        opened = repository.create(manifest, initial_drafts(manifest, lane))
        result = repository.append_batch(
            manifest.engagement_id,
            (
                user_note_draft(lane, "ready", "ready-note"),
                decision_draft(lane, "decision-1"),
            ),
            expected_revision=opened.revision,
        )
        events = repository.load_events(manifest.engagement_id)

    assert [item.sequence for item in events] == [1, 2, 3, 4]
    assert events[3].previous_event_hash == events[2].event_hash
    assert result.revision == JournalRevision(sequence=4, event_hash=events[3].event_hash)


def test_same_idempotency_key_returns_existing_event_but_collision_fails(
    tmp_path, manifest, lane
) -> None:
    draft = user_note_draft(lane, "same", "same-note")
    with EngagementJournalRepository(tmp_path / "knowledge") as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        first = repository.append_batch(manifest.engagement_id, (draft,))
        second = repository.append_batch(manifest.engagement_id, (draft,))
        with pytest.raises(ValueError, match="idempotency key collision"):
            repository.append_batch(
                manifest.engagement_id,
                (draft.model_copy(update={"actor_id": "different"}),),
            )

    assert first.created_event_ids == second.existing_event_ids


def test_append_rejects_oversized_event_or_batch_before_pending_io(
    tmp_path, manifest, lane, monkeypatch
) -> None:
    repository = EngagementJournalRepository(tmp_path / "knowledge")
    repository.create(manifest, initial_drafts(manifest, lane))
    pending_spy = forbid_pending_transaction_write(repository, monkeypatch)
    valid = user_note_draft(lane, "bounded note", "oversized")
    serialized_size = deterministic_materialized_event_size(repository, valid)
    monkeypatch.setattr(repository_module, "MAX_JOURNAL_EVENT_BYTES", serialized_size - 1)

    with pytest.raises(ValueError, match="journal event exceeds"):
        repository.append_batch(manifest.engagement_id, (valid,))
    with pytest.raises(ValueError, match="journal batch exceeds"):
        repository.append_batch(
            manifest.engagement_id,
            tuple(user_note_draft(lane, str(i), f"batch-{i}") for i in range(513)),
        )
    with pytest.raises(EngagementReplayError, match="nonterminal call"):
        repository.append_batch(
            manifest.engagement_id,
            (tool_completed(lane, call_id="never-started"),),
        )

    assert pending_spy.call_count == 0
```

- [ ] **Step 2: Write failing compare-and-swap and concurrent-writer tests**

```python
def test_expected_revision_rejects_stale_batch_without_partial_append(
    tmp_path, manifest, lane
) -> None:
    with EngagementJournalRepository(tmp_path / "knowledge") as repository:
        opening = repository.create(manifest, initial_drafts(manifest, lane))
        repository.append_batch(manifest.engagement_id, (user_note_draft(lane, "new", "new"),))
        with pytest.raises(RevisionConflictError):
            repository.append_batch(
                manifest.engagement_id,
                (decision_draft(lane, "stale"),),
                expected_revision=opening.revision,
            )
        assert len(repository.load_events(manifest.engagement_id)) == 3


def test_projection_writer_rejects_cross_milestone_ownership(
    tmp_path, manifest, lane
) -> None:
    with EngagementJournalRepository(tmp_path / "knowledge") as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        with pytest.raises(ProjectionOwnershipError):
            repository.write_projection(
                manifest.engagement_id,
                name="engagement-state",
                owner="planning",
                envelope=fixture_projection_envelope(),
            )


def test_concurrent_repository_instances_produce_one_monotonic_chain(
    tmp_path, manifest, lane
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalRepository(root) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))

    def append(index: int) -> None:
        with EngagementJournalRepository(root) as repository:
            repository.append_batch(
                manifest.engagement_id,
                (user_note_draft(lane, f"note-{index}", f"note-key-{index}"),),
            )

    with ThreadPoolExecutor(max_workers=8) as pool:
        tuple(pool.map(append, range(32)))

    with EngagementJournalRepository(root) as repository:
        events = repository.load_events(manifest.engagement_id)
    assert [event.sequence for event in events] == list(range(1, 35))
    assert len({event.event_hash for event in events}) == 34
```

- [ ] **Step 3: Write failing confinement and recovery tests**

Cover the same attack classes as the canonical repository:

```python
def test_symlinked_engagement_directory_cannot_escape(tmp_path, manifest) -> None:
    root = tmp_path / "knowledge"
    outside = tmp_path / "outside"
    outside.mkdir()
    engagements = root / "engagements"
    engagements.mkdir(parents=True)
    (engagements / str(manifest.engagement_id)).symlink_to(outside, target_is_directory=True)

    with EngagementJournalRepository(root) as repository, pytest.raises(ValueError):
        repository.load_events(manifest.engagement_id)
    assert not list(outside.iterdir())


def test_partial_final_jsonl_record_is_isolated_and_valid_prefix_replayed(
    tmp_path, manifest, lane
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalRepository(root) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        repository.append_batch(manifest.engagement_id, (user_note_draft(lane, "before-tail", "before-tail"),))
    journal = root / "engagements" / str(manifest.engagement_id) / "events.jsonl"
    with journal.open("ab") as stream:
        stream.write(b'{"sequence":4,"broken"')
        stream.flush()
        os.fsync(stream.fileno())

    with EngagementJournalRepository(root) as recovered:
        events = recovered.load_events(manifest.engagement_id)

    assert [event.sequence for event in events[:3]] == [1, 2, 3]
    assert events[-2].type == "evidence_attached"
    assert events[-1].type == "recovery_warning"
    recovery_files = list((journal.parent / "evidence").glob("blob-*.bin"))
    assert len(recovery_files) == 1
    assert recovery_files[0].read_bytes() == b'{"sequence":4,"broken"'
```

Also add tests for a FIFO `events.jsonl`, target-file symlink replacement, knowledge-root pathname replacement after open, mode checks, close idempotence, unsafe UUID/path components, and failure when POSIX descriptor primitives are unavailable.

- [ ] **Step 4: Run repository tests and verify RED**

```bash
.venv/bin/pytest tests/engagement/test_repository.py -v
```

Expected: failure because `EngagementJournalRepository` does not exist.

- [ ] **Step 5: Implement the confined repository root and atomic creation**

Implement a repository that retains a descriptor for the resolved knowledge root and opens every descendant relative to a retained descriptor. Use the canonical repository's established flags without importing its private methods:

```python
def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _read_flags() -> int:
    return os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _create_flags(*, append: bool = False) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    if append:
        flags |= os.O_APPEND
    return flags | getattr(os, "O_CLOEXEC", 0)
```

`EngagementJournalRepository.__init__(knowledge_root)` must:

1. reject nonabsolute/NUL paths after the caller resolves its configured root;
2. safely create/open the absolute root component-by-component;
3. retain and verify its descriptor identity;
4. create/open `engagements/` as `0700`;
5. pre-stat and incrementally enforce `MAX_MANIFEST_BYTES`, `MAX_JOURNAL_BYTES`,
   `MAX_JOURNAL_EVENTS`, `MAX_ENGAGEMENTS`, and `MAX_ENGAGEMENT_DIRECTORY_ENTRIES` before
   allocating or scanning; published UUID directories, recognized pending-create directories,
   locks, and any invalid entry all consume the directory scan cap;
6. recover pending append/tail transactions and validate the authoritative journal-head anchor
   before serving reads;
7. rebuild a missing, corrupt, or stale `engagement-state.json` from the authoritative validated
   manifest plus event chain before returning a snapshot.

`create(manifest, initial_drafts)` must require an immutable sequence of exactly two drafts:
`engagement_opened` followed by the caller's exact `lane_bound`. While holding the global
`engagements/.registry.lock`, it scans/replays every bounded engagement journal, including closed
and abandoned journals, and rejects a caller lane retained as bound to any different engagement.
A lane remains globally reserved until an explicit `lane_unbound`; closed-state unbind is a legal
control-plane mutation and does not reopen the engagement. Before staging-directory IO it requires
`existing_engagement_count + 1 <= MAX_ENGAGEMENTS`, canonical manifest bytes
`<= MAX_MANIFEST_BYTES`, and the prospective two-line initial chain within every event/count/byte
limit. It also reduces and canonical-serializes the initial `engagement-state` envelope and requires
it `<= MAX_DERIVED_PROJECTION_BYTES` before staging IO. Under that same lock it uses the deterministic hidden directory
`.pending-create-<engagement-uuid>` and first writes a bounded `.create-intent.json` containing the
exact canonical manifest, initial event lines, head, and their digests. It writes
`engagement.json`, the fully materialized two-event chain, and exact `journal-head.json` via
staged atomic files, fsyncs all three files
and the staging directory, atomically
renames the directory to the UUID, regenerates `engagement-state.json`, and fsyncs `engagements/`.
This makes manifest, opening event, and initial lane binding one atomic publication and globally
serializes competing create/bind attempts. A pre-existing UUID is a conflict; duplicate display
names remain legal. Mutation-spy tests cover the 10,000th accepted and 10,001st rejected engagement,
plus exact manifest/initial-chain under/over boundaries.

On root open/create retry under the registry lock, an exact valid create intent deterministically
finishes missing staged files and publishes once; a complete already-published UUID returns the
idempotent result only when all intent/manifest/chain/head bytes match. A conflicting intent fails
closed. A recognized empty directory left before intent fsync may be removed/fsynced because it
contains no user/evidence bytes; any unknown file or mismatched staged byte is moved to a bounded
internal create-recovery quarantine or fails closed, never treated as an engagement. Add fault
points after directory/intent/each file/fsync/rename, exact retry, conflicting UUID, and
`MAX_ENGAGEMENT_DIRECTORY_ENTRIES + 1` mixed published/staging/invalid entries; scans and disk use
remain bounded.

All later `bind_lane()` and `unbind_lane()` operations acquire the same registry lock around global lane-conflict validation plus the per-engagement append. Conflict scans include every retained
binding in every lifecycle status, not only active engagements. Lock order is always registry then
engagement; no code acquires them in reverse. The registry remains an index-free scan in M6A,
bounded by the repository's engagement/event limits. Add close -> same-lane create rejection ->
closed-state unbind -> create success, plus reopen-after-conflicting-bind rejection tests.

- [ ] **Step 6: Implement canonical event hashing and durable batch append**

Use sorted compact JSON with `allow_nan=False`. Compute `event_hash` over the UTF-8 canonical envelope with `event_hash` omitted and `previous_event_hash` included.

Define `JOURNAL_HEAD_SCHEMA_VERSION = "sedna.journal-head.v1"` and a frozen, extra-forbid
`JournalHead` containing the engagement UUID, exact `JournalRevision`, event count, exact
`events.jsonl` byte length including newlines, and SHA-256 of those complete bytes.
`journal-head.json` is a small authoritative commit anchor, not a projection. Creation publishes it
inside the same staging directory as the initial chain. Every later append intent records both the
complete exact base head and prospective target head.

Implement:

```python
def append_batch(
    self,
    engagement_id: UUID,
    drafts: Sequence[JournalEventDraft],
    *,
    expected_revision: JournalRevision | None = None,
) -> BatchAppendResult:
    ...
```

The actual implementation must execute these concrete operations under the per-engagement `flock`:

1. recover an existing `.pending-append.json`;
2. strictly load and validate the current chain;
3. resolve and canonical-byte-validate an exact already-committed idempotent batch; when every
   draft matches consecutively, return its existing IDs/result before stale-CAS rejection;
4. compare `expected_revision` when supplied for any genuinely new or conflicting batch;
5. deep-revalidate every new draft and materialize UUID/time/sequence/hash;
6. require `1 <= len(drafts) <= MAX_JOURNAL_BATCH_EVENTS`, then canonicalize every fully
   materialized event and require each UTF-8 line length `<= MAX_JOURNAL_EVENT_BYTES` before any
   pending/file write; require `len(existing_events) + len(new_events) <= MAX_JOURNAL_EVENTS` and
   the exact prospective `events.jsonl` byte size (existing bytes plus every canonical line and
   newline) `<= MAX_JOURNAL_BYTES`; idempotent existing events are validated against the same bounds;
7. prospectively call `reduce_engagement(manifest, (*existing_events, *new_events))` and reject a
   schema-valid but lifecycle-invalid batch before any pending/file write; canonicalize its exact
   `engagement-state` projection envelope and require it `<= MAX_DERIVED_PROJECTION_BYTES`, so no
   accepted journal can create a mandatory projection that later cannot rebuild;
8. persist a mode-`0600` pending transaction containing the complete base head, exact canonical
   event lines, and complete prospective target head;
9. append the lines to `events.jsonl`, flush, and fsync;
10. atomically write/fsync `journal-head.json` to the transaction's target head and fsync the
    engagement directory;
11. delete the pending transaction and fsync again;
12. atomically regenerate `engagement-state.json` from the already validated replay with owner
    `engagement`.

Recovery rolls a pending batch forward: it verifies that the existing head is exactly the base or
target and the journal is the base plus a valid prefix of the transaction, appends only the missing
lines, then publishes the target head before clearing the intent. Without an exact pending
transaction, journal bytes/count/revision/digest must equal the head. A journal behind the head, a
newline-terminated valid extension ahead of it, or any divergence is `journal_corrupt`; it is never
accepted as a shorter valid history. Journal-ahead-of-head is recoverable only when the pending
transaction proves that exact extension. A missing/corrupt head is recoverable only during
descriptor-confined initial staging with its create intent; an already published engagement fails
closed rather than deriving a weaker anchor from journal bytes.

Fault-inject after journal fsync/before head replace, after head replace/before intent clear, and
before the original response. Retrying the exact event-ID/key
and draft batch against its old expected revision returns the committed IDs without duplication;
changing one byte or using a genuinely new draft with that stale revision returns the typed
collision/revision error and writes nothing.
Add exact-under and one-event/one-byte-over total-limit tests with a pending/file-write spy. Pending
recovery revalidates the same prospective totals recorded by its base revision before appending a
missing suffix; a transaction that would exceed them fails closed rather than making future reads
unopenable.

Do not use an unlocked mutable sequence counter. `append()` may be a one-draft convenience wrapper around `append_batch()`.

- [ ] **Step 7: Implement safe reads, tail isolation, and projection primitives**

All reads must use `O_NONBLOCK | O_NOFOLLOW`, verify regular files, pre-stat and incrementally
enforce the exact manifest/journal/event/inventory bounds, parse through Pydantic, and validate the
full chain and its exact `journal-head.json`. `load_snapshot` and repository open treat
manifest+events+head as authoritative: a missing,
corrupt, owner-mismatched, or stale `engagement-state.json` is atomically rebuilt and fsynced before
return, never trusted or allowed to make the valid journal unavailable. Add crash-after-pending-clear
and missing/corrupt/stale projection tests proving byte-identical recovery.

If and only if bytes through the authoritative head validate exactly and the only bytes beyond it
are one incomplete final record without a newline, use this exact
two-phase recovery; never acquire `.evidence.lock` while holding the journal/registry lock:

Task 3 implements the minimal private `_EvidenceObjectStore` needed here: bounded directory
classification/count/quota, digest/size-bound capture intent, temp write/fsync/verify,
no-replace canonical publication, and exact slice verification. It accepts only already-normalized
bytes and emits no public evidence API. Task 4 builds `EvidenceStore` on this same primitive and
adds host normalization, public capture results, orphan pagination, and logbooks; it must not
duplicate storage/locking logic. Thus Task 3's tail-recovery test is independently GREEN before
Task 4 while exercising the eventual canonical evidence transaction.

1. under the journal lock, validate the exact head/prefix, read the bounded tail, derive its
   digest/evidence ID and deterministic
   recovery-event IDs, and write/fsync a descriptor-confined `.tail-recovery.json` intent binding
   the current journal identity, full file size, last-valid offset, valid-prefix revision/hash,
   tail digest, and exact recovery-pair drafts; then release the journal lock without truncating;
2. under `.evidence.lock`, revalidate the bounded evidence inventory/object-count/byte quota,
   create or byte-verify `evidence/blob-<sha256>.bin` as `0600`, fsync it and `evidence/`, then
   release the evidence lock;
3. reacquire the journal lock and require the same descriptor identity, authoritative head, file
   size, valid-prefix hash, tail bytes/digest, and intent. Any intervening divergence fails closed;
   otherwise truncate to the head's exact byte length, fsync, and append the intent-bound
   idempotent atomic pair through the normal head-updating transaction
   `[evidence_attached(representation="recovery_tail"), recovery_warning]`;
4. remove/fsync the intent only after the pair is durable. On open, the intent proves whether to
   finish evidence capture, perform the still-unmodified truncate/pair transaction, or return the
   already committed pair. Every ordinary append/load observes and completes or blocks on this
   intent before using the journal.

An invalid newline-terminated record is corruption, not a recoverable tail. Fault-inject before and
after intent fsync, evidence capture, second-lock revalidation, truncate, pair append, and intent
removal; every reopen yields exactly one evidence attachment/warning pair and never an unaccounted
truncated tail. A barrier test races tail recovery with a normal boundary evidence capture: lock
order never inverts, the aggregate quota/object cap admits only the valid winner, and no partial
blob, tail truncation, or unjournaled evidence remains.

Add clean newline suffix-truncation tests with the projection present, missing, and stale: the
shorter chain remains hash-valid internally but disagrees with `journal-head.json` and fails as
`journal_corrupt`. Also fault-inject initial head publication and every journal/head/intent window;
recovery either reaches the one target head or exposes no engagement, never silently rolls the
authoritative revision backward.

Add repository-private projection methods that atomically load/write only this closed ownership map:

```python
PROJECTION_OWNERS = {
    "engagement-state": "engagement",
    "state": "planning",
    "frontier": "planning",
    "strategy-ledger": "planning",
}
```

They must store `authoritative_revision` in every projection, reject a compare-and-swap mismatch, and reject an owner/name mismatch before opening a target. Journal creation/append is the only M6A path allowed to write `engagement-state.json`; generic facade commits are restricted to the three planning-owned names. Do not expose a generic path-writing method outside the package, and do not let one writer infer ownership from caller-supplied paths.
Pre-stat/incrementally read each derived envelope at `MAX_DERIVED_PROJECTION_BYTES`; every commit
preflights its exact canonical bytes before temp creation. Add state collections large enough to
hit exact-under/one-over projection bytes and prove the over append writes neither journal nor head.

- [ ] **Step 8: Run repository tests and verify GREEN**

```bash
.venv/bin/pytest tests/engagement/test_repository.py -v
.venv/bin/ruff check src/sedna/engagement/repository.py tests/engagement/test_repository.py
```

Expected: all repository tests pass.

- [ ] **Step 9: Commit the repository**

```bash
git add src/sedna/engagement/repository.py tests/engagement/test_repository.py
git commit -m "feat(engagement): persist append-only journals safely"
```

### Task 4: Original Evidence Sidecars and Reproducible Session Logbooks

**Files:**
- Modify: `src/sedna/engagement/normalization.py`
- Create: `src/sedna/engagement/evidence.py`
- Create: `src/sedna/engagement/logbook.py`
- Modify: `tests/engagement/test_normalization.py`
- Create: `tests/engagement/test_evidence_logbook.py`
- Modify: `src/sedna/engagement/repository.py`

**Interfaces:**
- Consumes: Task 1's dependency-neutral sanitizer/normalizer, repository descriptors,
  `EvidenceReference`, journal events, engagement snapshot.
- Produces: `EvidenceStore`, `EvidenceCapture`, `EvidenceQuota`, `EvidenceCaptureError`, `render_session_logbook()`, and bounded byte-slice reads.

- [ ] **Step 1: Write failing byte-preservation, deduplication, and quota tests**

```python
from hashlib import sha256

from sedna.engagement import CaptureLimitation
from sedna.engagement.evidence import (
    DEFAULT_EVIDENCE_QUOTA,
    EvidenceCaptureError,
    EvidenceQuota,
    EvidenceStore,
)


def test_evidence_store_preserves_exact_bytes_and_deduplicates_by_digest(
    tmp_path, manifest, lane
) -> None:
    payload = b"user flag: HTB{private-proof}\npassword=p@ssw0rd\xff"
    with EngagementJournalRepository(tmp_path / "knowledge") as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        first = repository.write_evidence(
            manifest.engagement_id,
            payload,
            media_type="application/octet-stream",
            representation="host_bytes",
        )
        second = repository.write_evidence(
            manifest.engagement_id,
            payload,
            media_type="application/octet-stream",
            representation="host_bytes",
        )
        repository.append_batch(
            manifest.engagement_id,
            (evidence_attached_draft(lane, first),),
        )
        restored = repository.read_evidence_slice(
            manifest.engagement_id,
            first.evidence_id,
            offset=0,
            limit=len(payload),
        )

    assert first == second
    assert first.evidence_id == f"evidence-sha256-{first.sha256}"
    assert restored.data == payload
    assert restored.complete is True


def test_quota_failure_is_typed_and_does_not_create_partial_sidecar(
    tmp_path, manifest, lane
) -> None:
    with EngagementJournalRepository(
        tmp_path / "knowledge",
        evidence_quota=EvidenceQuota(max_item_bytes=4, max_engagement_bytes=16),
    ) as repository:
        repository.create(manifest, initial_drafts(manifest, lane))
        with pytest.raises(EvidenceCaptureError) as caught:
            repository.write_evidence(
                manifest.engagement_id,
                b"12345",
                media_type="text/plain",
                representation="host_text",
            )
    assert caught.value.reason_code == "item_quota_exceeded"
    assert caught.value.observed_size == 5


def test_default_evidence_quota_is_exactly_the_m6a_contract() -> None:
    assert DEFAULT_EVIDENCE_QUOTA == EvidenceQuota(
        max_item_bytes=64 * 1024 * 1024,
        max_engagement_bytes=4 * 1024 * 1024 * 1024,
    )
```

- [ ] **Step 2: Write failing Markdown-injection and rebuild tests**

```python
def test_logbook_keeps_untrusted_markdown_in_a_dynamic_code_fence(
    tmp_path, manifest, lane
) -> None:
    hostile = "```\n</script>\n[click](javascript:alert(1))\nHTB{proof}\n````"
    snapshot = recorded_session_snapshot(tmp_path, manifest, lane, hostile)

    first = rebuild_session_logbooks(snapshot.repository, manifest.engagement_id)
    path = first[0]
    rendered = path.read_text(encoding="utf-8")
    path.unlink()
    second = rebuild_session_logbooks(snapshot.repository, manifest.engagement_id)

    assert second[0].read_text(encoding="utf-8") == rendered
    assert "HTB{proof}" in rendered
    assert "javascript:alert(1)" in rendered
    assert rendered.count("`````") >= 2
    assert rendered.find("javascript:alert(1)") > rendered.find("`````")


def test_provider_credentials_are_removed_before_argument_sidecar_capture(
    tmp_path, manifest, lane
) -> None:
    secret = "provider-secret-that-must-never-reach-disk"
    target_credential = "Basic dGFyZ2V0LWV4YW1wbGU="
    captured = capture_tool_arguments(
        tmp_path,
        manifest,
        lane,
        {
            "command": "curl https://192.0.2.44/",
            "headers": {"Authorization": target_credential},
            "provider": {"authorization": secret},
            "provider_token": secret,
        },
    )

    assert captured.reference.capture_limitations == (
        CaptureLimitation.PROVIDER_OR_HOST_SECRET_REDACTED,
    )
    assert secret.encode() not in all_engagement_bytes(tmp_path, manifest.engagement_id)
    assert sha256(secret.encode()).hexdigest().encode() not in all_engagement_bytes(
        tmp_path, manifest.engagement_id
    )
    assert b"[REDACTED:provider-or-host-secret]" in captured.persisted_bytes
    assert target_credential.encode() in captured.persisted_bytes
```

Add tests for UTF-8 text, invalid UTF-8, JSON result, empty result, large output, binary metadata, safe relative links, two sessions on one day, same-name engagements, orphan evidence discovery, and `limit > 64 KiB` rejection in `read_evidence_slice()`.

- [ ] **Step 3: Run evidence/logbook tests and verify RED**

```bash
.venv/bin/pytest tests/engagement/test_evidence_logbook.py -v
```

Expected: failure because evidence and logbook services do not exist.

- [ ] **Step 4: Implement content-addressed evidence persistence**

Use production defaults of exactly 64 MiB per evidence item and 4 GiB per engagement, represented by an injected `EvidenceQuota` so tests can use small limits.

`EvidenceStore.capture()` must:

1. accept bytes already produced by `normalize_host_payload()`;
2. calculate SHA-256 and size before opening a target and derive `EvidenceId` as `evidence-sha256-<digest>`;
3. verify quota against regular referenced and orphan sidecars;
4. write/fsync a bounded `.capture-<uuid>.json` intent containing expected digest, size, canonical
   target, and private temp name; create that descriptor-confined hidden temp in `evidence/` with
   `O_EXCL | O_NOFOLLOW`, mode `0600`, then write all bytes, flush/fsync, rewind and verify
   size/digest before publication;
5. publish the complete temp to the one canonical `evidence/blob-<sha256>.bin` name with an atomic
   no-replace descriptor-relative operation (`linkat` or an equivalently proven primitive), fsync
   `evidence/`, unlink/fsync the temp, and never write through the canonical name;
6. on `EEXIST`, byte/size/digest-verify the complete canonical object before reusing it; a mismatch
   fails closed. On open under `.evidence.lock`, recover every bounded intent: a complete matching
   temp is atomically published/reused as a recoverable orphan even if the host never redelivers;
   an incomplete/mismatched temp is moved with its intent to a closed, bounded quarantine name and
   reported as `capture_recovery_incomplete`, never confused with the canonical object or silently
   deleted. Clear/fsync the intent only after canonical publication/quarantine is durable;
7. return an `EvidenceReference` with the typed content-addressed ID, digest, size, media type, representation, relative path, and typed `capture_limitations` tuple.

Hold a descriptor-confined per-engagement `.evidence.lock` across bounded inventory, aggregate quota
check, dedup verification, create/write/fsync, and result construction. Never hold a journal or
registry lock while acquiring it; journal attachment happens afterward. A two-repository barrier
test starts two boundary captures that individually fit but jointly exceed the quota: exactly one
succeeds, the other gets `engagement_quota_exceeded`, and no partial blob exists.

Fault-inject after intent/temp create, each partial-write boundary, temp fsync, no-replace publication,
directory fsync, and temp unlink. Every retry observes the canonical name either absent or complete
and byte-exact; a crash after temp fsync/before publication recovers the exact orphan without host
redelivery, and bounded recovery never mistakes a canonical object for garbage.

Under the same lock, classify every directory entry through a closed filename/kind table:
canonical `blob-<sha256>.bin`, canonical generated session-logbook names, recognized capture
temp/intent/quarantine names, recognized `.logbook-<uuid>.tmp` projections, and nothing else.
Every entry—including unknown/symlink/FIFO entries that then fail closed—consumes
`MAX_EVIDENCE_DIRECTORY_ENTRIES`. Every payload-bearing canonical blob, capture temp, and
quarantined partial counts toward `MAX_EVIDENCE_OBJECTS` and the 4-GiB physical evidence byte
quota (hard-linked temp/final names count their inode once); capture intents are separately
pre-stat/incrementally capped by `MAX_CAPTURE_INTENT_BYTES`. Logbooks/derived temps count the
directory cap and `MAX_DERIVED_PROJECTION_BYTES`, not private evidence quota. Fail before allocation
on a prospective cap violation. Test capture → render logbook → second capture, mixed-kind exact caps,
unknown/special files, last accepted object, and one-over mutation spies.

Normalize hook values without losing the delivered representation:

```python
str   -> UTF-8 bytes, representation="host_text"
bytes -> unchanged, representation="host_bytes"
non-None JSON-compatible object -> canonical JSON bytes, representation="canonical_host_json"
None -> no evidence attachment; terminal completion records host_returned_no_result semantics
unsupported object -> typed evidence capture failure
```

Before `normalize_host_payload()` or correlation hashing, run `sanitize_host_arguments()` through a
cycle-safe iterative walker over mappings and sequences. Define
`MAX_HOST_VALUE_DEPTH = 32`, `MAX_HOST_VALUE_NODES = 100_000`,
`MAX_HOST_SCALAR_BYTES = 64 * 1024 * 1024`, and
`MAX_HOST_NORMALIZED_BYTES = 64 * 1024 * 1024`. Track container identity to reject cycles, sort
normalized mapping keys, and stream canonical output through a byte counter/digest/temp writer
rather than first constructing an unbounded copy. Depth/node/cycle/scalar/encoded-byte overflow
returns `EvidenceCaptureFailed(reason_code="normalization_limit_exceeded")` without `repr`, raw
value, partial digest, or correlation hash; a fallback correlation without a stable host ID becomes
typed uncertain. A complete normalized value that exceeds the remaining item/engagement quota may
carry its safe observed size/digest. Add cycle, depth+1, node+1, scalar-byte+1, canonical-byte+1, and
huge-map tests proving bounded work and no rejected secret/digest leakage.

Always replace unambiguous case-insensitive keys `provider_token`, `provider_credential`,
`provider_api_key`, `host_token`, `host_credential`, and `host_runtime_secret`. Replace generic
`api_key`, `apikey`, `authorization`, `cookie`, `secret_access_key`, and `token` only when nested
beneath the closed namespaces `provider`, `host_runtime`, `transport_auth`, or `telemetry_auth`, or
when the same object has `credential_scope="provider" | "host_runtime"`. Use the constant
`[REDACTED:provider-or-host-secret]`. This structural rule deliberately preserves
engagement-target credentials such as an HTTP Authorization header in ordinary tool arguments.
Never hash, log, capture in an exception, or place removed values in a sidecar. Both the event's
bounded `safe_arguments` summary and private argument sidecar derive from the sanitized structure;
the sidecar records
`capture_limitations=(CaptureLimitation.PROVIDER_OR_HOST_SECRET_REDACTED,)`. Target credentials
returned by operational tools remain private original result evidence.

Define and export from `normalization.py` one source-specific closed key set rather than duplicating
lists:

```python
SOURCE_SECRET_KEYS = frozenset({
    "provider_token", "provider_credential", "provider_api_key",
    "host_token", "host_credential", "host_runtime_secret",
    "token", "access_token", "api_key", "apikey", "authorization",
    "cookie", "password", "secret", "secret_access_key",
})
```

It is the union of structural always-sensitive provider/host keys and generic keys that are always
unsafe in a globally shared source locator/metadata field. Registry validation and the add-source
pre-hook redaction import this exact object; set-equality tests cover every member.

Define `MAX_SAFE_ARGUMENT_BYTES = 8 * 1024`, `MAX_SAFE_ARGUMENT_DEPTH = 4`, and
`MAX_SAFE_ARGUMENT_ITEMS = 64`. After sanitization, traverse mappings by normalized sorted key and
sequences by index, admit complete scalar/subtree records until any bound would be crossed, and add
one typed `ArgumentOmission(omitted_count, omitted_sha256)` whose digest covers the ordered canonical
*sanitized* omitted records. The full sanitized structure remains only in the quota-bound private
sidecar. Deep/large argument tests prove the complete `tool_call_started` envelope fits
`MAX_JOURNAL_EVENT_BYTES`, summaries are byte-stable, and neither a provider secret nor its digest
appears in the summary, omission digest, correlation, sidecar, or exception.

- [ ] **Step 5: Implement bounded evidence reads and orphan inventory**

Expose repository wrappers:

```python
def read_evidence_slice(
    self,
    engagement_id: UUID,
    evidence_id: EvidenceId,
    *,
    offset: int,
    limit: int,
) -> EvidenceSlice:
    ...
```

Require `0 <= offset`, `1 <= limit <= 65536`, verify the typed content-addressed reference against journal metadata, open the sidecar descriptor-relative, verify digest/size, and return `complete=False` when bytes remain. This is the only M6A API later LLM code may use to inspect raw evidence. Document the M6B consumer contract here: request 32 KiB per slice, process at most 64 slices per settlement pass, and return `incomplete` without marking the remainder interpreted when the 2 MiB cap is reached.

`inventory_orphan_evidence(engagement_id, *, after_name=None, limit=256) -> OrphanEvidencePage`
compares sidecar digests against all `evidence_attached` and recovery references without deleting
them. The frozen page contains at most 256 confined object names/digests, `total_count`,
`next_after_name`, and an ordered digest/count summary of undisplayed objects. Both the journal
reference scan and directory enumeration enforce their hard caps and never return an unbounded
tuple. Reopening never silently removes private evidence. Add 256/257 and
`MAX_EVIDENCE_OBJECTS + 1` inventory tests.

- [ ] **Step 6: Implement safe deterministic logbook rendering**

Define `MAX_LOGBOOK_INLINE_ITEM_BYTES = 64 * 1024`,
`MAX_LOGBOOK_INLINE_TOTAL_BYTES = 1 * 1024 * 1024`, and
`MAX_LOGBOOK_BYTES = 2 * 1024 * 1024`, `MAX_LOGBOOK_DESCRIPTOR_ENTRIES = 2048`, and
`MAX_LOGBOOK_TIMELINE_ENTRIES = 4096`, `MAX_LOGBOOK_REBUILD_RETRIES = 3`. Admission is deterministic in journal order. Inline only a
complete textual evidence item that fits both inline budgets. For large, binary, truncated, or
overflow evidence, render only its attachment event ID, validated confined relative link, media
type, byte size, digest, representation, and capture limitations; originals remain in sidecars.
When any otherwise-inline item is omitted, add an ordered overflow summary with omitted count,
event-sequence range, and SHA-256 of the canonical omitted descriptor list. Render at most the
descriptor/timeline entry caps; represent every further occurrence by a second deterministic
count/sequence-range/digest summary so even a 100,000-event metadata-only session is bounded. Never
silently truncate a value or read a large sidecar to decide eligibility. The final UTF-8 Markdown
must fit `MAX_LOGBOOK_BYTES`; otherwise move additional whole inline, descriptor, or timeline entries to the
corresponding summary until it does.

Implement:

```python
def render_session_logbook(
    manifest: EngagementManifest,
    state: EngagementState,
    events: Sequence[JournalEvent],
    evidence_reader: EvidenceReader,
    *,
    session_id: str,
) -> str:
    ...
```

The renderer must include identity/objective/scope, starting revision, strategies and decisions,
suggested versus executed commands when present, every budget-admitted original textual output
(including flags and credentials), metadata/links for every other output, correlation limitations,
evidence hashes, checkpoint, and remaining in-flight calls. It must never execute or interpret
captured markup.

Use static headings only. Treat *every* dynamic scalar—display name, objective, scope value,
strategy, rationale, tool/error/source metadata, command, output, credential, and flag—as untrusted:
HTML-escape it and place multiline material in a backtick fence one character longer than the
longest run in that fragment (minimum three); use an adaptive escaped code span for single lines.
Construct sidecar links only from validated `EvidenceReference.relative_path`; never use a captured
filename or URL as a Markdown destination. Because the canonical reference is engagement-root
relative (`evidence/blob-<sha256>.bin`) while the logbook already lives in `evidence/`, require that
exact prefix and derive a URL-encoded basename href relative to the logbook parent. Reject any
other shape; never emit `..` or duplicate `evidence/evidence`. A path-resolution test proves each
rendered href opens the exact event-bound sidecar beneath the retained engagement descriptor.

Name logbooks `YYYYMMDD-HHMMSSffffff-<slug>-<session-digest>.md`. Derive the timestamp from the first
linked session event and the digest from the full session ID. Render from one immutable snapshot
outside locks, then acquire a descriptor-confined per-engagement logbook-projection lock, reload the
authoritative journal revision, and atomically replace only if it still equals the rendered
revision. A stale renderer discards and retries from the newer snapshot; it can never overwrite
revision N+1 with N. Retry at most `MAX_LOGBOOK_REBUILD_RETRIES`; exhaustion leaves authoritative
journal/evidence and the last valid derived logbook untouched and raises host-neutral
`LogbookProjectionConflict(code="logbook_rebuild_conflict")`. Task 4 defines that closed typed
exception/result in the logbook module and its tests; it imports no adapter/health state. Task 7
catches it only in Hades flows, records the bounded health code, and returns fail-open. Repository
open/snapshot remains usable and a bounded logbook read either returns the last validated derived
file marked stale or surfaces that typed projection conflict, never imports the adapter. On
repository/runtime open and bounded logbook read, missing/corrupt metadata
or a revision/digest mismatch triggers rebuild from the authoritative manifest/events. Add a
barrier test whose N+1 renderer finishes before N, plus crash/missing/corrupt/stale read-repair
tests and a continuously-winning writer test; the final successful Markdown is byte-identical and
at journal head.

Place the projection lock at `<engagement>/.logbook.lock`. Publish through a recognized
`evidence/.logbook-<uuid>.tmp` while briefly holding `.evidence.lock` only for final directory
classification/rename/fsync (rendering and evidence reads happen before it). Capture never sees an
unclassified publication artifact; recovery validates/removes only stale derived temps. Add a
capture-vs-logbook-rename barrier test with no false corruption or lock inversion.

Add parser-based hostile fixtures for every dynamic field (headings, HTML, links, backticks), plus
a 64-MiB textual and binary sidecar fixture whose reader spy proves no full read and a one-over
metadata-only fixture for both entry caps. Rebuilds are byte-identical, stay within every budget,
retain original sidecars, and represent omissions only through explicit summaries.

- [ ] **Step 7: Run evidence/logbook tests and verify GREEN**

```bash
.venv/bin/pytest tests/engagement/test_normalization.py tests/engagement/test_evidence_logbook.py -v
.venv/bin/ruff check src/sedna/engagement/normalization.py src/sedna/engagement/evidence.py src/sedna/engagement/logbook.py tests/engagement/test_normalization.py tests/engagement/test_evidence_logbook.py
```

Expected: all tests pass.

- [ ] **Step 8: Commit evidence and logbooks**

```bash
git add src/sedna/engagement/normalization.py src/sedna/engagement/evidence.py src/sedna/engagement/logbook.py src/sedna/engagement/repository.py tests/engagement/test_normalization.py tests/engagement/test_evidence_logbook.py
git commit -m "feat(engagement): retain evidence and render session logbooks"
```

### Task 5: Shared `sources.md` Registry with Preserved Manual Content

**Files:**
- Create: `src/sedna/engagement/sources.py`
- Create: `tests/engagement/test_sources.py`
- Modify: `src/sedna/engagement/repository.py`
- Modify: `src/sedna/engagement/__init__.py`

**Interfaces:**
- Consumes: retained knowledge-root descriptor and atomic-write primitive.
- Produces: `SharedSourceEntry`, `SourceOrigin`, `SourceStatus`, `SourceRegistryResult`, `SourceRegistrySnapshot`, and bounded atomic `SharedSourceRegistry.add_or_update()`, `.snapshot()`, and `.list_entries()`.

- [ ] **Step 1: Write failing machine-block and manual-preservation tests**

```python
from sedna.engagement.sources import (
    SharedSourceEntry,
    SharedSourceRegistry,
    SourceRegistryLimitError,
)


def test_registry_adds_readable_machine_block_and_preserves_manual_bytes(tmp_path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir()
    manual = "# My sources\n\nThis paragraph is maintained by the user.\n"
    (root / "sources.md").write_text(manual, encoding="utf-8")

    with EngagementJournalRepository(root) as repository:
        result = SharedSourceRegistry(repository).add_or_update(
            SharedSourceEntry.suggested(
                name="HackTricks",
                locator="https://book.hacktricks.wiki/",
                topics=("web", "linux", "active directory"),
                notes="Useful orientation; validate claims against current evidence.",
            )
        )

    rendered = (root / "sources.md").read_text(encoding="utf-8")
    assert rendered.startswith(manual)
    assert f"<!-- sedna-source:v1 begin {result.entry.source_id} -->" in rendered
    assert "### Source" in rendered
    assert "HackTricks" in rendered
    assert "https://book.hacktricks.wiki/" in rendered
    assert f"<!-- sedna-source:v1 end {result.entry.source_id} -->" in rendered
```

- [ ] **Step 2: Write failing idempotency, replacement, corruption, and concurrency tests**

```python
def test_same_normalized_locator_is_idempotent_and_changed_machine_block_is_replaced(
    tmp_path,
) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalRepository(root) as repository:
        registry = SharedSourceRegistry(repository)
        first = registry.add_or_update(source_entry("https://example.test/docs"))
        same = registry.add_or_update(source_entry("https://example.test/docs"))
        changed = registry.add_or_update(
            source_entry("https://example.test/docs", topics=("windows",))
        )
    rendered = (root / "sources.md").read_text(encoding="utf-8")

    assert first.changed is True
    assert same.changed is False
    assert changed.changed is True
    assert rendered.count("sedna-source:v1 begin") == 1
    assert "windows" in rendered


def test_malformed_machine_block_fails_without_rewriting_manual_content(tmp_path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir()
    original = "manual\n<!-- sedna-source:v1 begin broken -->\nunterminated\n"
    (root / "sources.md").write_text(original, encoding="utf-8")
    with EngagementJournalRepository(root) as repository:
        with pytest.raises(ValueError, match="invalid managed source block"):
            SharedSourceRegistry(repository).add_or_update(source_entry("https://example.test"))
    assert (root / "sources.md").read_text(encoding="utf-8") == original


def test_snapshot_and_list_entries_are_atomic_and_bounded(tmp_path) -> None:
    root = tmp_path / "knowledge"
    with EngagementJournalRepository(root) as repository:
        registry = SharedSourceRegistry(repository)
        registry.add_or_update(source_entry("https://example.test/one"))
        snapshot = registry.snapshot()
        listed = registry.list_entries()

    assert snapshot.entries == listed
    assert snapshot.content_sha256
    assert snapshot.byte_size <= 1024 * 1024


def test_oversized_registry_fails_before_unbounded_parse(tmp_path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "sources.md").write_bytes(b"x" * (1024 * 1024 + 1))
    with EngagementJournalRepository(root) as repository:
        with pytest.raises(SourceRegistryLimitError, match="byte_limit_exceeded"):
            SharedSourceRegistry(repository).snapshot()
```

Add a concurrent-writer test using two repository instances, a reader/writer stress test proving snapshots are always one complete before/after file, a 4,097-managed-entry rejection, a symlinked `sources.md` rejection, mode `0600`, duplicate source-ID collision detection, and rejection of marker tokens in user-supplied fields.

- [ ] **Step 3: Run source-registry tests and verify RED**

```bash
.venv/bin/pytest tests/engagement/test_sources.py -v
```

Expected: failure because `SharedSourceRegistry` does not exist.

- [ ] **Step 4: Implement strict source records and stable identity**

Define:

```python
SOURCE_REGISTRY_SCHEMA_VERSION = "sedna.sources.v1"
MAX_SOURCE_REGISTRY_BYTES = 1024 * 1024
MAX_SOURCE_REGISTRY_ENTRIES = 4096


class SourceOrigin(StrEnum):
    USER_SUGGESTED = "user_suggested"
    BUILT_IN = "built_in"
    DISCOVERED = "discovered"


class SourceStatus(StrEnum):
    SUGGESTED = "suggested"
    CONSULTED = "consulted"
    USEFUL = "useful"
    CONTRADICTED = "contradicted"
    STALE = "stale"
    PREFERRED = "preferred"


class SharedSourceEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    schema_version: Literal["sedna.sources.v1"] = SOURCE_REGISTRY_SCHEMA_VERSION
    source_id: Annotated[str, Field(pattern=r"^source-[0-9a-f]{64}$")]
    name: Annotated[str, Field(min_length=1, max_length=256)]
    locator: Annotated[str, Field(min_length=1, max_length=4096)]
    topics: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=128)], ...],
        Field(max_length=64),
    ] = ()
    origin: SourceOrigin
    status: SourceStatus
    notes: Annotated[str, Field(max_length=8192)] = ""
    last_observed_on: date | None = None


class SourceRegistrySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    content_sha256: Sha256Hex
    byte_size: int = Field(ge=0, le=MAX_SOURCE_REGISTRY_BYTES)
    entries: Annotated[
        tuple[SharedSourceEntry, ...],
        Field(max_length=MAX_SOURCE_REGISTRY_ENTRIES),
    ]
```

Normalize locator whitespace and HTTP(S) host casing when it is a URL. Generate `source_id` from
SHA-256 of the normalized locator. Normalize unique topics but preserve readable name and notes.
Reject NUL, control characters, and the exact managed-marker prefix `<!-- sedna-source:` from every
user field. Reuse the dependency-neutral normalization module's closed provider/host-secret
classifier before source-ID hashing or persistence: an HTTP(S) locator may contain no URL userinfo,
and query/fragment keys normalized into `SOURCE_SECRET_KEYS` are rejected.
Name, topic, and notes fields reject bearer/basic credentials and the same bounded `key=value`
forms; no rejected value or its digest may enter a source ID, journal, managed block, log, or error.
This is fail closed rather than redaction because a shared locator or note must remain a stable
human-authored identity. Validate the per-field/count bounds above before normalization, sorting,
rendering, or registry scanning; add exact-bound/one-over schema tests for name, locator, topics,
notes, entries, and total registry bytes, plus URL-userinfo/query/fragment and free-text provider
secret fixtures proving value-and-digest absence from every persisted byte.

- [ ] **Step 5: Implement managed-block parsing and atomic updates**

The block format uses static structure and inert dynamic content exactly like this:

````markdown
<!-- sedna-source:v1 begin source-<digest> -->
### Source

````text
Name: Human-readable name
Locator: https://example.test/
Topics: linux, web
Origin: user_suggested
Status: suggested
````

`````json
{"canonical machine record":"stored here with sorted keys"}
`````
<!-- sedna-source:v1 end source-<digest> -->
````

Parse markers only when they occupy an entire line, require matched IDs and exactly one adaptive
canonical JSON fence, and deep-validate the JSON record. Managed readable fields are HTML-escaped
and emitted only inside an adaptive inert text fence; no user-controlled heading/link destination
is generated. The readable Markdown is derived and replaced together with its machine record.
Preserve every byte before, between, and after managed blocks that is not inside a managed block.
Add hostile name/locator/topic/note fixtures with headings, HTML, links, and arbitrary backtick runs;
a Markdown parser must see only the fixed managed heading/fences plus preserved manual content.

Hold `<knowledge-root>/.sources.lock` with `flock`, read using `O_NONBLOCK | O_NOFOLLOW`, atomically
replace `sources.md` with mode `0600`, and fsync the knowledge root. Because a human editor does not
take this cooperative lock, retain the opened file identity plus full-byte digest and, immediately
before rename, descriptor-reopen/re-stat/re-read the bounded target. If identity or bytes changed,
boundedly reload/remerge once or return typed `source_registry_conflict`; never overwrite the
external edit. A malformed managed block aborts without modification. Add a barrier test that
inserts manual prose between the registry read and replace and proves it is preserved or the
operation conflicts with no write.

`snapshot()` takes a shared registry lock, verifies a regular file and `st_size <= MAX_SOURCE_REGISTRY_BYTES` before allocating/reading, reads one immutable byte snapshot, rejects more than `MAX_SOURCE_REGISTRY_ENTRIES` managed records, validates every block, and returns entries sorted by `source_id` plus the full-file digest and byte size. A missing file returns the digest of empty bytes and no entries. `list_entries()` returns `snapshot().entries`; it must not stat/read/parse a second time. Writers enforce the same byte and entry limits before atomic replacement, so neither path can create a registry that readers reject.

- [ ] **Step 6: Run source-registry tests and verify GREEN**

```bash
.venv/bin/pytest tests/engagement/test_sources.py -v
.venv/bin/ruff check src/sedna/engagement/sources.py tests/engagement/test_sources.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit the source registry**

```bash
git add src/sedna/engagement/sources.py src/sedna/engagement/repository.py src/sedna/engagement/__init__.py tests/engagement/test_sources.py
git commit -m "feat(engagement): manage shared source suggestions"
```

### Task 6: `EngagementJournalService` Facade and Named Engagement Workflow

**Files:**
- Create: `src/sedna/engagement/service.py`
- Create: `tests/engagement/test_service.py`
- Modify: `src/sedna/engagement/repository.py`
- Modify: `tests/engagement/test_repository.py`
- Modify: `src/sedna/engagement/__init__.py`

**Interfaces:**
- Consumes: repository, reducer, evidence, logbook, source registry, injected clock and UUID factory.
- Produces: stable `EngagementJournalService`, repository-issued service/mixed commit capabilities,
  `EngagementMutationResult`, `JournalEventPage`, `EvidenceDescriptorPage`,
  `EngagementSettlementPort`, `SettlementReason`, `ClosureFinalizer`, lane resolution, and
  compare-and-swap event/projection APIs consumed by M6B/M6C.

- [ ] **Step 1: Write failing create, unique resume, and ambiguity tests**

```python
from sedna.engagement import EngagementJournalService, ExecutionLaneKey


def test_create_requires_human_name_and_binds_calling_lane(
    tmp_path, authorized_scope, lane
) -> None:
    with EngagementJournalService.open(
        tmp_path / "knowledge",
        clock=fixed_clock,
        uuid_factory=fixed_uuid_factory,
    ) as service:
        created = service.create_engagement(
            display_name="HTB-Orion",
            objective="Obtain user and root flags",
            scope=authorized_scope,
            lane=lane,
            required_proofs=(
                ProofRequirement(
                    proof_id="user-flag",
                    kind="flag",
                    description="A valid HTB user flag",
                ),
                ProofRequirement(
                    proof_id="root-flag",
                    kind="flag",
                    description="A valid HTB root flag",
                ),
            ),
        )
        resolved = service.resolve_lane_binding(lane)

    assert created.snapshot.manifest.display_name == "HTB-Orion"
    assert [item.proof_id for item in created.snapshot.manifest.required_proofs] == [
        "user-flag",
        "root-flag",
    ]
    assert created.snapshot.state.status == "active"
    assert resolved.mode == "exact"
    assert resolved.engagement_id == created.snapshot.manifest.engagement_id


def test_resume_by_scope_is_automatic_only_when_one_open_engagement_is_compatible(
    tmp_path, authorized_scope, lane
) -> None:
    with engagement_service(tmp_path) as service:
        first = service.create_engagement(
            display_name="HTB-Orion",
            objective="Obtain flags",
            scope=authorized_scope,
            lane=lane,
        )
        service.unbind_lane(first.engagement_id, lane, reason="session_changed")
        resumed = service.resume_engagement(lane=new_lane("session-2"), scope=authorized_scope)
    assert resumed.snapshot.manifest.engagement_id == first.snapshot.manifest.engagement_id


def test_same_target_in_two_open_engagements_returns_readable_candidates(
    tmp_path, authorized_scope
) -> None:
    with engagement_service(tmp_path) as service:
        service.create_engagement(
            display_name="Orion-A",
            objective="Obtain flags",
            scope=authorized_scope,
            lane=new_lane("session-a"),
        )
        service.create_engagement(
            display_name="Orion-B",
            objective="Validate foothold",
            scope=authorized_scope,
            lane=new_lane("session-b"),
        )
        with pytest.raises(EngagementAmbiguousError) as caught:
            service.resume_engagement(lane=new_lane("session-c"), scope=authorized_scope)
    assert [item.display_name for item in caught.value.candidates] == ["Orion-A", "Orion-B"]
```

- [ ] **Step 2: Write failing public-facade and projection CAS tests**

```python
def test_public_facade_supports_snapshot_events_evidence_and_projection_cas(
    tmp_path, manifest, lane
) -> None:
    with engagement_service(tmp_path) as service:
        created = service.create_from_manifest(manifest, lane=lane)
        appended = service.append_events(
            manifest.engagement_id,
            (user_note_draft("projection CAS fixture"),),
            expected_revision=created.snapshot.revision,
        )
        planner_projection = fixture_planner_state(appended.snapshot.revision)
        state_path = service.commit_projection(
            manifest.engagement_id,
            "state",
            planner_projection,
            expected_revision=appended.snapshot.revision,
        )
        loaded = service.load_projection(
            manifest.engagement_id,
            "state",
            type(planner_projection),
        )
        lifecycle = service.load_projection(
            manifest.engagement_id,
            "engagement-state",
            type(appended.snapshot.state),
        )

    assert state_path.name == "state.json"
    assert loaded == planner_projection
    assert lifecycle == appended.snapshot.state
    with engagement_service(tmp_path) as service, pytest.raises(RevisionConflictError):
        service.commit_projection(
            manifest.engagement_id,
            "state",
            planner_projection,
            expected_revision=created.snapshot.revision,
        )
```

Add a second facade test that appends 300 notes, captures one argument and one result sidecar, then asserts:

```python
first = service.load_events(engagement_id, after_sequence=0, limit=256)
second = service.load_events(
    engagement_id,
    after_sequence=first.next_after_sequence,
    through_revision=first.authoritative_revision,
    limit=256,
)
descriptors = service.list_evidence_descriptors(
    engagement_id,
    after_sequence=0,
    through_revision=first.authoritative_revision,
    limit=256,
)
assert len(first.events) == 256
assert all(event.sequence <= first.authoritative_revision.sequence for event in second.events)
assert {item.reference.evidence_id for item in descriptors.items} == expected_evidence_ids
assert all(isinstance(item.reference.capture_limitations, tuple) for item in descriptors.items)
```

Assert through `inspect.signature()` that all public methods listed in the Global Constraints retain `expected_revision`, `offset`, `limit`, `name`, `model_type`, `after_sequence`, and `through_revision` keyword names. Limits above 256 and unknown/mismatched `through_revision` return typed validation errors rather than silently widening the read.

- [ ] **Step 3: Write failing decision, closure, and child inheritance tests**

```python
def test_decision_is_bound_only_to_calling_lane(tmp_path, authorized_scope, lane) -> None:
    child = new_lane(lane.session_id, task_id="child")
    with engagement_service(tmp_path) as service:
        created = service.create_engagement(
            display_name="HTB-Orion",
            objective="Obtain flags",
            scope=authorized_scope,
            lane=lane,
        )
        service.bind_lane(created.engagement_id, child, reason="explicit_child")
        service.record_decision(
            created.engagement_id,
            lane=lane,
            strategy="Enumerate exposed services",
            rationale="No target facts exist yet",
        )
        assert service.load_active_decision(created.engagement_id, child) is None
        assert service.load_active_decision(created.engagement_id, lane) is not None


def test_m6a_close_stops_at_closing_even_when_barrier_is_ready(
    tmp_path, authorized_scope, lane
) -> None:
    with engagement_service(tmp_path) as service:
        created = create_orion(service, authorized_scope, lane)
        closing = service.request_close(
            created.engagement_id,
            lane=lane,
            reason="objective proof observed",
        )
    assert closing.snapshot.state.status == "closing"
    assert closing.snapshot.state.closure_ready is True
    assert closing.snapshot.state.closure.origin == "manual"


def test_empty_requirements_allow_manual_close_but_not_proof_driven_close(
    tmp_path, authorized_scope, lane
) -> None:
    with engagement_service(tmp_path) as service:
        created = service.create_engagement(
            display_name="Research-task",
            objective="Explore the authorized target",
            scope=authorized_scope,
            lane=lane,
            required_proofs=(),
        )
        assert created.snapshot.manifest.required_proofs == ()
        assert all(event.type != "closure_requested" for event in created.snapshot.events)
        closing = service.request_close(
            created.engagement_id,
            lane=lane,
            reason="user requested manual close",
        )
    assert closing.snapshot.state.status == "closing"


def test_call_without_post_hook_can_be_explicitly_abandoned_before_close(
    tmp_path, authorized_scope, lane
) -> None:
    with engagement_service(tmp_path) as service:
        created = create_orion(service, authorized_scope, lane)
        start_operational_call_through_sealed_test_capability(
            service,
            created.engagement_id,
            lane=lane,
            call_id="crashed-call",
            expected_revision=created.snapshot.revision,
        )
        waiting = service.request_close(
            created.engagement_id,
            lane=lane,
            reason="manual close after host crash",
        )
        assert waiting.snapshot.state.closure_ready is False
        resolved = service.terminate_tool_call(
            created.engagement_id,
            "crashed-call",
            resolution="abandoned",
            reason="post_tool_call was lost when the host exited",
            lane=lane,
            expected_revision=waiting.snapshot.revision,
        )
    assert resolved.snapshot.state.closure_ready is True


def test_child_inheritance_requires_exact_or_unique_parent_binding(tmp_path) -> None:
    with engagement_service(tmp_path) as service:
        first, second = create_two_parent_tasks_in_same_session(service)
        ambiguous = service.link_child_session(
            parent_session_id="parent",
            parent_task_id=None,
            child_session_id="child",
            child_subagent_id="subagent-1",
        )
    assert ambiguous.mode == "ambiguous"
    assert ambiguous.engagement_id is None
```

- [ ] **Step 4: Run service tests and verify RED**

```bash
.venv/bin/pytest tests/engagement/test_service.py -v
```

Expected: failure because the service facade does not exist.

- [ ] **Step 5: Implement the stable facade and projection envelope**

Create `service.py` with:

```python
from sedna.engagement.models import SettlementSafeCode


ProjectionT = TypeVar("ProjectionT", bound=BaseModel)


SettlementReason = Literal[
    "plan",
    "close",
    "verify",
    "reject",
    "reopen",
    "report",
    "resume",
    "session_finalize",
]


class EngagementSettlementOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["complete", "incomplete", "failed", "unavailable"]
    pending_range_count: int = Field(ge=0, le=MAX_SETTLEMENT_PENDING_RANGES)
    next_pending_offset: int | None = Field(
        default=None, ge=0, le=MAX_EVIDENCE_ITEM_BYTES
    )
    next_pending_subject: PendingSubjectCursor | None = None
    pending_inventory_sha256: Sha256Hex | None = None
    safe_code: SettlementSafeCode | None = None

    # after-validator:
    # complete => zero/no offset/subject/digest/code;
    # incomplete => positive true count, digest, optional offset/cursor, and incomplete/budget code;
    # failed => safe_code == "interpretation_failed" and either zero/none pending metadata or
    #           an exact positive count + digest with optional offset/cursor;
    # unavailable => zero/none pending metadata and one of
    #                journal_unavailable|journal_corrupt|settlement_unavailable;
    # every branch forbids exception text.


class EngagementSettlementPort(Protocol):
    def settle(
        self,
        engagement_id: UUID,
        *,
        reason: SettlementReason,
    ) -> EngagementSettlementOutcome: ...


class EngagementSettlementPortFactory(Protocol):
    def open(
        self,
        resolved_root: Path,
    ) -> AbstractContextManager[EngagementSettlementPort]: ...


class ClosureFinalizer(Protocol):
    def finalize(
        self,
        *,
        snapshot: EngagementSnapshot,
    ) -> EngagementSnapshot: ...


class ProofClosureCapability:
    """Sealed M6C capability; construction requires a package-private authority token."""

    def request_proof_close(
        self,
        engagement_id: UUID,
        *,
        authoritative_revision: JournalRevision,
        lane: ExecutionLaneKey | None,
        reason: str,
    ) -> "EngagementMutationResult": ...

    def cancel_proof_close(
        self,
        engagement_id: UUID,
        *,
        expected_revision: JournalRevision,
        lane: ExecutionLaneKey | None,
        reason: str,
    ) -> "EngagementMutationResult": ...


class EngagementMutationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    snapshot: EngagementSnapshot
    created_event_ids: tuple[UUID, ...] = ()
    existing_event_ids: tuple[UUID, ...] = ()

    @property
    def engagement_id(self) -> UUID:
        return self.snapshot.engagement_id


class EngagementJournalService:
    @classmethod
    def open(
        cls,
        knowledge_root: Path,
        *,
        clock: Callable[[], datetime] = utc_now,
        uuid_factory: Callable[[], UUID] = uuid4,
        evidence_quota: EvidenceQuota = DEFAULT_EVIDENCE_QUOTA,
    ) -> "EngagementJournalService": ...

    def create_engagement(
        self,
        *,
        display_name: str,
        objective: str,
        scope: AuthorizationScope,
        lane: ExecutionLaneKey,
        required_proofs: Sequence[ProofRequirement] = (),
    ) -> EngagementMutationResult: ...

    def create_from_manifest(
        self,
        manifest: EngagementManifest,
        *,
        lane: ExecutionLaneKey,
    ) -> EngagementMutationResult: ...

    def resume_engagement(
        self,
        *,
        lane: ExecutionLaneKey,
        engagement_id: UUID | None = None,
        display_name: str | None = None,
        scope: AuthorizationScope | None = None,
        expected_revision: JournalRevision | None = None,
    ) -> EngagementMutationResult: ...

    def inspect_engagement(self, engagement_id: UUID) -> EngagementSnapshot: ...

    def list_engagements(
        self,
        *,
        after_engagement_id: UUID | None = None,
        limit: int = 64,
    ) -> EngagementListPage: ...

    def bind_lane(
        self,
        engagement_id: UUID,
        lane: ExecutionLaneKey,
        *,
        reason: str,
        expected_revision: JournalRevision | None = None,
    ) -> EngagementMutationResult: ...

    def unbind_lane(
        self,
        engagement_id: UUID,
        lane: ExecutionLaneKey,
        *,
        reason: str,
        expected_revision: JournalRevision | None = None,
    ) -> EngagementMutationResult: ...

    def link_child_session(
        self,
        *,
        parent_session_id: str,
        parent_task_id: str | None,
        child_session_id: str,
        child_subagent_id: str | None,
    ) -> LaneBindingResolution: ...

    def change_objective(
        self,
        engagement_id: UUID,
        *,
        lane: ExecutionLaneKey,
        objective: str,
        authorization_basis: str,
        expected_revision: JournalRevision | None = None,
    ) -> EngagementMutationResult: ...

    def change_scope(
        self,
        engagement_id: UUID,
        *,
        lane: ExecutionLaneKey,
        scope: AuthorizationScope,
        authorization_basis: str,
        expected_revision: JournalRevision | None = None,
    ) -> EngagementMutationResult: ...

    def reopen_engagement(
        self,
        engagement_id: UUID,
        *,
        lane: ExecutionLaneKey,
        reason: str,
        expected_revision: JournalRevision | None = None,
    ) -> EngagementMutationResult: ...

    def abandon_engagement(
        self,
        engagement_id: UUID,
        *,
        lane: ExecutionLaneKey,
        reason: str,
        expected_revision: JournalRevision | None = None,
    ) -> EngagementMutationResult: ...

    def load_snapshot(self, engagement_id: UUID) -> EngagementSnapshot: ...

    def load_events(
        self,
        engagement_id: UUID,
        *,
        after_sequence: int = 0,
        through_revision: JournalRevision | None = None,
        limit: int = 256,
    ) -> JournalEventPage: ...

    def list_evidence_descriptors(
        self,
        engagement_id: UUID,
        *,
        after_sequence: int = 0,
        through_revision: JournalRevision | None = None,
        limit: int = 256,
    ) -> EvidenceDescriptorPage: ...

    def append_events(
        self,
        engagement_id: UUID,
        drafts: Sequence[JournalEventDraft],
        *,
        expected_revision: JournalRevision | None = None,
    ) -> EngagementMutationResult: ...

    def request_close(
        self,
        engagement_id: UUID,
        *,
        lane: ExecutionLaneKey,
        reason: str,
        expected_revision: JournalRevision | None = None,
    ) -> EngagementMutationResult: ...

    def read_evidence_slice(
        self,
        engagement_id: UUID,
        evidence_id: EvidenceId,
        *,
        offset: int,
        limit: int,
    ) -> EvidenceSlice: ...

    def load_projection(
        self,
        engagement_id: UUID,
        name: str,
        model_type: type[ProjectionT],
    ) -> ProjectionT | None: ...

    def commit_projection(
        self,
        engagement_id: UUID,
        name: str,
        projection: BaseModel,
        *,
        expected_revision: JournalRevision,
    ) -> Path: ...

    def resolve_lane_binding(self, lane: ExecutionLaneKey) -> LaneBindingResolution: ...

    def load_active_decision(
        self,
        engagement_id: UUID,
        lane: ExecutionLaneKey,
    ) -> ActiveDecision | None: ...

    def terminate_tool_call(
        self,
        engagement_id: UUID,
        call_id: str,
        *,
        resolution: Literal["timed_out", "abandoned"],
        reason: str,
        lane: ExecutionLaneKey,
        expected_revision: JournalRevision | None = None,
    ) -> EngagementMutationResult: ...

    def record_decision(
        self,
        engagement_id: UUID,
        *,
        lane: ExecutionLaneKey,
        proposal_id: UUID | None = None,
        strategy: Annotated[str | None, Field(min_length=1, max_length=8192)] = None,
        rationale: Annotated[str | None, Field(min_length=1, max_length=8192)] = None,
        host_adapted_command: HostAdaptedCommandRecord | None = None,
        expected_revision: JournalRevision | None = None,
    ) -> EngagementMutationResult: ...
```

Define `JournalEventPage(engagement_id, authoritative_revision, through_revision, events, next_after_sequence, complete)`, `EvidenceDescriptor(attachment_event_id, event_sequence, reference)`, and `EvidenceDescriptorPage(engagement_id, authoritative_revision, through_revision, items, next_after_sequence, complete)` as frozen, extra-forbid results. Also define
`EngagementListItem(engagement_id, display_name, status, created_at, revision)` and
`EngagementListPage(items: tuple[EngagementListItem, ...] <= 64, total_count,
next_after_engagement_id, omitted_items_sha256)` with sorted UUID cursor and exact
truncation/digest validators. Define `LaneBindingResolution(mode:
Literal["exact","session_unique","linked_child_unique","ambiguous","unbound"],
engagement_id: UUID | None, lane: ExecutionLaneKey | None, candidates: tuple[EngagementListItem,
...] <= 64, total_count, omitted_items_sha256)` with identity present only for the three unique
modes and candidates only for ambiguity. `load_events()` returns at most 256 ordered events and honors an exact hash-bearing `through_revision`; `list_evidence_descriptors()` returns at most 256 attachment descriptors ordered/cursored by event sequence through that same revision. Repeated attachments of one content-addressed `EvidenceId` remain distinct descriptors, so their representation and `capture_limitations` are not lost. It never inventories directories or exposes bytes. Every create/resume/lifecycle/decision/append mutation returns `EngagementMutationResult`; there is no mixture of naked state, snapshot, and append-result shapes. `EngagementSnapshot.engagement_id`, `.revision`, `.manifest`, `.events`, and `.state` must mutually validate.

`load_projection()` accepts only the fixed names `engagement-state`, `state`, `frontier`, or `strategy-ledger`. `commit_projection()` accepts only the three planning-owned names; M6A lifecycle replay alone writes `engagement-state.json`. Store a `DerivedProjectionEnvelope` containing name, schema identifier, authoritative revision, and deeply validated payload. Never accept a caller-supplied path.

Define one exhaustive, package-private `EVENT_APPEND_OWNER_BY_TYPE` table and assert
`set(EVENT_APPEND_OWNER_BY_TYPE) == set(EventType)` in tests. Its exact M6A ownership is:

```text
repository_create:
  engagement_opened
lifecycle_service:
  engagement_resumed, lane_bound, lane_unbound, child_lane_linked,
  objective_changed, scope_changed, decision_recorded, agent_deviation_recorded,
  engagement_reopened, engagement_abandoned
hook_adapter:
  session_started, session_checkpointed, session_finalized, tool_call_started,
  tool_call_completed, evidence_attached, evidence_capture_failed,
  unmatched_tool_completion, unplanned_action, control_tool_invoked,
  uncertain_correlation
tool_resolution_service:
  tool_call_terminated
closure_service:
  closure_requested, closure_cancelled
source_registry:
  source_suggested
recovery_repository:
  recovery_warning
caller_facade:
  user_note
```

Generic `append_events()` accepts only `caller_facade` payloads and rejects every other type before
the repository. Each service/hook/repository path uses a package-private, constructor-token-checked
capability that can create only its listed payloads and still undergoes prospective replay/CAS.
The table names the primary single-event owner; exactly three sealed mixed capabilities are explicit
exceptions needed for indivisible cross-owner batches:

```text
OperationalStartCommitCapability.commit_start(...)
  -> [argument evidence_attached|evidence_capture_failed,
      closure_cancelled iff the locked current barrier exists,
      tool_call_started, unplanned_action?, uncertain_correlation?]
ChildStopCommitCapability.commit_child_stop(...)
  -> [session_checkpointed, lane_unbound] only for the exact linked child with no in-flight call
TailRecoveryCommitCapability.commit_tail_recovery(...)
  -> [evidence_attached(recovery_tail), recovery_warning] only from the sealed tail intent
```

Their constructors are repository-issued and identity-checked; callers supply semantic inputs, not
arbitrary drafts, while each method derives payloads/order/system correlation under the one CAS.
No holder of a single-family capability gains a mixed method. Add exact-batch success and
drop/reorder/extra/cross-capability forgery tests for all three.
`request_close()` owns manual requests; M6C receives a sealed proof-settlement request capability
rather than generic append access. Actor strings are never authority. Add forgery tests for
scope/objective changes, reopen, abandon, closure cancellation, tool completion/termination,
lane mutation, recovery, and cross-capability drafts. M6B and M6C must extend both the exhaustive
table and its set-equality tests when they extend `EventType`, issuing narrow planner/report/
promotion capabilities rather than widening the generic facade.

`request_close(engagement_id, *, lane, reason, expected_revision=None)` has exactly the signature
shown above. Under its successful CAS it derives `terminal_watermark` from the locked current tail
and `in_flight_call_ids` from prospective replay; the caller cannot supply either. A stale CAS or
concurrent new call returns without a closure event. Add `inspect.signature`, stale-close/new-call
race, and exact watermark/in-flight-set tests.

`ProofClosureCapability` is deliberately omitted from `sedna.engagement.__all__`. The owned runtime
composition obtains one instance through a package-private service factory whose constructor token
is identity-checked by the repository. `request_proof_close()` appends only
`closure_requested(origin="proof_settlement")` against the exact authoritative revision;
`cancel_proof_close()` can cancel only that origin. Both retain an optional exact execution lane or
an explicit system correlation record, and neither accepts an arbitrary event draft.

`EngagementSettlementPort` and `ClosureFinalizer` are compatibility seams only in M6A.
`SettlementReason` is the exact literal union shown above; later milestones import it rather than
redefining it. M6B's adapter converts its private `SettlementResult` into the host-neutral
`EngagementSettlementOutcome`; M6A inspects only this closed status/range metadata and never a
situation or private value. Neither protocol imports planning/reporting types. The adapter may
invoke an injected settlement port only after its M6A service/repository context has closed; M6C's
terminal coordinator invokes `finalize(snapshot=...)` only after a complete settlement and a ready
closure barrier. M6A itself never stores or invokes a finalizer.
Export `SettlementReason`, `SettlementSafeCode`, `EngagementSettlementOutcome`,
`EngagementSettlementPort`, `EngagementSettlementPortFactory`, and `ClosureFinalizer` from the
stable package surface. Keep the
concrete `ProofClosureCapability` constructor/factory package-private as specified above.

- [ ] **Step 6: Implement create, resume, inspect, lifecycle, and decision use cases**

Use an injected UUID and UTC clock. `create_engagement()` validates name, objective, authorized scope, and `required_proofs: Sequence[ProofRequirement] = ()` before creating the knowledge root. It passes opening plus initial exact lane-binding drafts to the repository's single atomic `create()`. No objective-text heuristic is allowed: for the standard HTB workflow the caller explicitly supplies `user-flag` and `root-flag`; an empty tuple remains a valid manual-close-only engagement.

`resume_engagement()` resolves in this order:

1. exact UUID selector;
2. exact normalized display name among resumable states;
3. exactly one compatible resumable engagement whose active scope intersects the supplied typed scope;
4. typed `EngagementAmbiguousError` with candidates sorted by display name/date;
5. typed `EngagementNotFoundError`.

Service-layer failures are typed exceptions; the Hades adapter alone maps them to compact error JSON. Therefore every successful create/resume/lifecycle/decision/append call has the same `EngagementMutationResult` shape.

An IP or hostname is never used as global identity. `request_close()` is always an explicit/manual operation in M6A: it snapshots the current revision and exact in-flight call IDs into `closure_requested(origin="manual")` and never appends a terminal close event. Empty proof requirements never cause an automatic request. Replay preserves the origin in `ClosureBarrier`; only M6C's sealed capability may create or automatically cancel `origin="proof_settlement"`. `reopen_engagement()` supports closing, abandoned, and future closed states. `abandon_engagement()` appends only a lifecycle event and leaves later tool completions journalable.

When the caller's current lane is not already bound, `reopen_engagement` requires an explicit UUID
or exact display-name selector, acquires registry then engagement locks, rechecks the lane is not
reserved elsewhere, and atomically appends `[lane_bound, engagement_reopened]`. If already bound it
appends only reopen. A lane conflict rejects both effects; no partial bind is visible. This same
registry-locked mixed lifecycle capability is the M6C post-settlement seam. Add close -> unbind ->
new-session explicit reopen success and conflicting-bind/no-lifecycle-mutation tests.

`terminate_tool_call()` requires an existing nonterminal `call_id`, an exact bound lane, one of the two terminal resolutions, a bounded nonblank reason, and optional compare-and-swap revision. It appends `tool_call_terminated`; it does not fabricate a result sidecar or a strategic outcome. A timeout policy may call it automatically, while `abandoned` is an explicit operator/host recovery action after a lost post hook. Both make the call terminal for an existing closure barrier.

`record_decision()` has exactly the signature above and accepts either a future planner proposal
UUID or internal `strategy` plus rationale, never both. The host input uses the unambiguous wire
name `custom_strategy` and the adapter maps it once to this internal parameter. In M6A, a non-null proposal ID must refer to
an existing proposal event for that exact lane or return `proposal_not_found`; custom decisions are
always lane-local. The resulting durable payload field remains the resolved `strategy`, so the
wire name is never accepted as an implicit service alias. Add `inspect.signature`, XOR, cross-lane proposal, stale
CAS, optional host-adapted-command canonical-size/privacy, and custom-decision replay tests. The
resolved `ActiveDecision` retains the bounded optional record; no command is executed by this API.

- [ ] **Step 7: Implement exact/univocal lane binding**

`resolve_lane_binding()` returns one of `exact`, `session_unique`, `linked_child_unique`, `ambiguous`, or `unbound`.

- `exact`: full host/session/task key matches.
- `session_unique`: there is exactly one compatible binding for that session; the adapter must immediately append a new exact `lane_bound` event before recording an action.
- `linked_child_unique`: a `child_lane_linked` event identifies one parent engagement and the first observed child tool lane is bound exactly before capture.
- `ambiguous`: two or more engagements or parent task lanes qualify; attach nothing.
- `unbound`: no evidence supports a binding.

Never choose by target text observed in a tool argument. Never reuse another lane's active decision.

- [ ] **Step 8: Run service tests and verify GREEN**

```bash
.venv/bin/pytest tests/engagement/test_service.py tests/engagement/test_repository.py -v
.venv/bin/ruff check src/sedna/engagement/service.py src/sedna/engagement/repository.py tests/engagement/test_service.py tests/engagement/test_repository.py
```

Expected: all tests pass.

- [ ] **Step 9: Export the facade and commit**

Add the stable service and model names to `sedna.engagement.__all__`, then run:

```bash
git add src/sedna/engagement/service.py src/sedna/engagement/repository.py src/sedna/engagement/__init__.py tests/engagement/test_service.py tests/engagement/test_repository.py
git commit -m "feat(engagement): add named journal service facade"
```

### Task 7: Hades Control Tools and Observer-Hook Adapter

**Files:**
- Create: `src/sedna/engagement/hades_adapter.py`
- Create: `tests/engagement/test_hades_adapter.py`

**Interfaces:**
- Consumes: `EngagementJournalService`, optional host-neutral `EngagementSettlementPort`, host `ctx.register_tool`, `ctx.register_hook`, and documented Hades hook payloads.
- Produces: `HadesEngagementAdapter.register()`, three M6A control tools, nine hook callbacks, exact versioned control set, typed control-call events, and typed boundary errors.

- [ ] **Step 1: Write failing registration and control-tool tests**

```python
class FakeHadesContext:
    def __init__(self, knowledge_root) -> None:
        self.sedna_knowledge_root = knowledge_root
        self.tools = []
        self.hooks = {}

    def register_tool(self, **definition) -> None:
        self.tools.append(definition)

    def register_hook(self, name, callback) -> None:
        self.hooks[name] = callback


def test_adapter_registers_compact_tools_and_required_hooks(tmp_path) -> None:
    context = FakeHadesContext(tmp_path / "knowledge")
    adapter = HadesEngagementAdapter(
        context,
        root_resolver=lambda: context.sedna_knowledge_root,
    )
    adapter.register()

    assert [item["name"] for item in context.tools] == [
        "sedna_manage_engagement",
        "sedna_record_decision",
        "sedna_add_source",
    ]
    assert set(context.hooks) == {
        "pre_tool_call",
        "post_tool_call",
        "pre_llm_call",
        "on_session_start",
        "on_session_end",
        "on_session_finalize",
        "on_session_reset",
        "subagent_start",
        "subagent_stop",
    }
    assert all(
        item["schema"]["parameters"]["additionalProperties"] is False
        for item in context.tools
    )


def test_manage_create_requires_host_lane_and_has_no_per_call_root(tmp_path) -> None:
    context, tools, _ = registered_adapter(tmp_path)
    for definition in tools.values():
        schema = definition["schema"]["parameters"]
        assert "knowledge_root" not in schema.get("properties", {})

    missing = call_tool(tools, "sedna_manage_engagement", create_payload())
    created = call_tool(
        tools,
        "sedna_manage_engagement",
        create_payload(),
        session_id="session-orion",
        task_id="task-root",
    )
    assert missing == {
        "ok": False,
        "error": {"code": "host_context_required", "retryable": False},
    }
    assert created["ok"] is True
    assert created["engagement"]["display_name"] == "HTB-Orion"
```

- [ ] **Step 2: Write failing pre/post-hook evidence tests**

```python
def test_bound_operational_tool_is_recorded_with_original_result(tmp_path) -> None:
    context, tools, hooks = registered_adapter(tmp_path)
    created = create_bound_orion(tools)
    call_tool(
        tools,
        "sedna_record_decision",
        {
            "custom_strategy": "Enumerate exposed services",
            "rationale": "No services are known yet",
        },
        session_id="session-orion",
        task_id="task-root",
    )
    identity = {
        "session_id": "session-orion",
        "task_id": "task-root",
        "turn_id": "turn-1",
        "api_request_id": "request-1",
        "api_call_count": 1,
        "tool_call_id": "tool-call-1",
    }
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "id"}, **identity)
    hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "id"},
        result="uid=1000(user) HTB{private-proof}",
        duration_ms=17,
        **identity,
    )

    events, evidence = load_private_capture(tmp_path, created["engagement"]["engagement_id"])
    assert [event.type for event in events][-2:] == [
        "evidence_attached",
        "tool_call_completed",
    ]
    assert events[-1].payload.technical_status == "returned"
    assert b"HTB{private-proof}" in evidence
    assert session_logbook_path(
        tmp_path,
        created["engagement"]["engagement_id"],
        "session-orion",
    ).is_file()
```

- [ ] **Step 3: Write failing allowlist, closing-cancellation, and correlation tests**

```python
def test_only_exact_control_tools_are_skipped_and_legacy_nmap_is_captured(tmp_path) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    assert CONTROL_TOOL_POLICY_VERSION == "sedna.control-tools.v1"
    assert CONTROL_TOOL_NAMES == frozenset(
        {
            "sedna_manage_engagement",
            "sedna_plan_next",
            "sedna_record_decision",
            "sedna_add_source",
            "sedna_learn_local",
            "sedna_retrieve_knowledge",
            "sedna_get_knowledge_artifact",
            "sedna_knowledge_maintenance",
        }
    )
    for control in sorted(CONTROL_TOOL_NAMES):
        hooks["pre_tool_call"](tool_name=control, args={}, **stable_hook_identity(control))
    hooks["pre_tool_call"](
        tool_name="sedna_retrieve_knowledge",
        args={},
        **stable_hook_identity("sedna_retrieve_knowledge"),
    )
    hooks["pre_tool_call"](
        tool_name="sedna_nmap_tcp_discovery",
        args={"target": "192.0.2.44"},
        **stable_hook_identity("legacy-nmap"),
    )
    assert captured_started_tool_names(tmp_path) == ["sedna_nmap_tcp_discovery"]
    assert captured_control_tool_names(tmp_path) == sorted(CONTROL_TOOL_NAMES)


def test_new_call_while_closing_appends_cancel_and_start_in_one_batch(tmp_path) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    call_tool(tools, "sedna_manage_engagement", {"action": "close", "reason": "proof"}, **LANE)
    hooks["pre_tool_call"](tool_name="terminal", args={"command": "whoami"}, **HOOK_ID)
    snapshot = load_snapshot(tmp_path)
    assert snapshot.state.status == "active"
    assert [event.type for event in snapshot.events][-3:] == [
        "evidence_attached",
        "closure_cancelled",
        "tool_call_started",
    ]


def test_incomplete_correlation_is_recorded_without_deduplication(tmp_path) -> None:
    _, _, hooks = registered_bound_adapter(tmp_path)
    hooks["pre_tool_call"](
        tool_name="terminal",
        args={"command": "id"},
        session_id="session-orion",
        task_id="task-root",
    )
    assert latest_event(tmp_path, "uncertain_correlation").payload.reason_code == (
        "missing_stable_identity"
    )


def test_manage_can_abandon_a_call_left_open_by_host_crash(tmp_path) -> None:
    _, tools, hooks = registered_bound_adapter(tmp_path)
    hooks["pre_tool_call"](
        tool_name="terminal",
        args={"command": "long-running-fixture"},
        **HOOK_ID,
    )
    journal_call_id = latest_event(tmp_path, "tool_call_started").payload.call_id
    assert journal_call_id != HOOK_ID["tool_call_id"]
    call_tool(tools, "sedna_manage_engagement", {"action": "close", "reason": "done"}, **LANE)
    resolved = call_tool(
        tools,
        "sedna_manage_engagement",
        {
            "action": "resolve_call",
            "call_id": journal_call_id,
            "resolution": "abandoned",
            "reason": "host process exited before post hook",
        },
        **LANE,
    )
    assert resolved["ok"] is True
    assert load_snapshot(tmp_path).state.closure_ready is True
```

The inspect/manage result exposes bounded journal `call_id` values for in-flight calls; operators
pass those IDs to `resolve_call`. Raw provider `tool_call_id` values are correlation inputs only and
are never accepted as journal call IDs.

Add tests proving: a stable duplicate pre/post pair is idempotent; redelivering a stable pre already
captured before an engagement entered `closing` is a complete no-op and does not cancel closure; an
uncertain completion links only when exactly one matching in-flight call exists; two candidates in
the same engagement may emit one sealed unmatched audit there, while zero candidates or candidates
across engagements attach no evidence/event and set only a bounded session-health code; a post hook
arriving after explicit timeout/abandon becomes
`unmatched_tool_completion(reason_code="call_already_terminated")` instead of a second terminal
event; optional host-supplied blocked/cancelled/error technical statuses are terminal; the exact
documented minimum post signature (`tool_name`, `args`, `result`, `task_id`, `duration_ms`, plus
ordinary general correlation kwargs) succeeds without `status`, `error_type`, or `error_message`;
post completion follows the pre-call engagement even after lane rebind; a bound lane without a
decision emits `unplanned_action`; an unbound/ambiguous lane attaches nothing. A result string that
contains “success”, “failed”, or an exit-code-looking fragment must not change `technical_status` or
create a strategic outcome event. Force argument/result quota and unsupported-serialization
failures: each emits the exact capture-role audit without raw/repr bytes; result failure atomically
terminates the call, and only an unavailable journal needs later `terminate_tool_call` recovery.
Finally, `result is None` emits no evidence attachment and one terminal completion with
`technical_status="unknown"` absent an explicit host status, allowing M6C to project
`absence_reason="host_returned_no_result"` rather than an artificial `b"null"` artifact.

- [ ] **Step 4: Write failing session, child, and fail-open health tests**

Cover:

```python
def test_child_session_inherits_only_from_unique_parent_binding(tmp_path) -> None:
    context, _, hooks = registered_bound_adapter(tmp_path)
    hooks["subagent_start"](
        parent_session_id="session-orion",
        parent_turn_id="turn-1",
        parent_subagent_id=None,
        child_session_id="session-child",
        child_subagent_id="subagent-1",
        child_role="worker",
        child_goal="Inspect the HTTP hypothesis",
    )
    hooks["pre_tool_call"](
        tool_name="terminal",
        args={"command": "true"},
        session_id="session-child",
        task_id="child-task-observed",
        tool_call_id="child-call-1",
        turn_id="child-turn-1",
        api_request_id="child-request-1",
        api_call_count=1,
    )
    assert resolve_bound_engagement(tmp_path, "session-child", "child-task-observed") is not None


def test_hook_write_failure_surfaces_next_turn_without_raising_from_hook(tmp_path, monkeypatch) -> None:
    context, _, hooks = registered_bound_adapter(tmp_path)
    monkeypatch.setattr(EngagementJournalService, "open", raising_journal_failure)
    assert hooks["pre_tool_call"](tool_name="terminal", args={}, **HOOK_ID) is None
    reminder = hooks["pre_llm_call"](
        session_id="session-orion",
        turn_id="turn-2",
        user_message="continue",
        conversation_history=[],
        is_first_turn=False,
        model="fixture",
        platform="cli",
    )
    assert "not reliably journaled" in reminder["context"]
    assert "private failure" not in reminder["context"]


def test_resume_and_finalize_call_optional_settlement_port_outside_journal_context(
    tmp_path,
) -> None:
    port = MutatingRecordingSettlementPort(
        tmp_path / "knowledge",
        assert_no_journal_lock=True,
    )
    _, tools, hooks = registered_bound_adapter(
        tmp_path,
        settlement_port_factory=StaticSettlementPortFactory(port),
    )
    resumed = call_tool(tools, "sedna_manage_engagement", {"action": "resume"}, **LANE)
    after_resume = load_snapshot(tmp_path)

    assert resumed["engagement"]["revision"] == after_resume.revision.model_dump(mode="json")
    assert after_resume.events[-1].payload.note == "settled:resume"

    hooks["on_session_finalize"](session_id=LANE["session_id"], task_id=LANE["task_id"])
    finalized = load_snapshot(tmp_path)
    settlement_event = event_with_note(finalized.events, "settled:session_finalize")
    final_checkpoint = latest_event_of_type(finalized.events, "session_finalized")

    assert [call.reason for call in port.calls] == [
        "resume",
        "session_finalize",
    ]
    assert settlement_event.sequence < final_checkpoint.sequence
    assert final_checkpoint.previous_event_hash == settlement_event.event_hash
    assert logbook_authoritative_revision(tmp_path) == finalized.revision


def test_settlement_failure_is_typed_without_returning_stale_state(tmp_path) -> None:
    port = RaisingSettlementPort(code="settlement_unavailable", assert_no_journal_lock=True)
    _, tools, hooks = registered_bound_adapter(
        tmp_path,
        settlement_port_factory=StaticSettlementPortFactory(port),
    )

    resumed = call_tool(tools, "sedna_manage_engagement", {"action": "resume"}, **LANE)
    assert resumed["ok"] is False
    assert resumed["error"]["code"] == "settlement_unavailable"
    assert "engagement" not in resumed
    assert hooks["on_session_finalize"](
        session_id=LANE["session_id"],
        task_id=LANE["task_id"],
    ) is None
    assert latest_event(tmp_path, "session_finalized").payload.reason == (
        "settlement_unavailable"
    )

    reminder = hooks["pre_llm_call"](
        session_id=LANE["session_id"],
        task_id=LANE["task_id"],
        turn_id="turn-after-failure",
        user_message="continue",
        conversation_history=[],
        is_first_turn=False,
        model="fixture",
        platform="cli",
    )
    assert "settlement unavailable" in reminder["context"]
    assert "private exception" not in reminder["context"]
```

Add the parallel `IncompleteSettlementPort` regression with `pending_range_count > 0` and
`next_pending_offset` caused by more than 2 MiB of pending evidence. Resume returns typed
`evidence_budget_exhausted` with no engagement status/frontier; session finalize records the exact
non-complete metadata and the logbook cannot label settlement clean.

Add close/reopen recording-port regressions. The adapter releases every journal lock, settles with
the exact reason, reloads, then performs the lifecycle CAS; an incomplete/failed outcome appends no
close/reopen event and returns the one non-complete envelope. A concurrent event between settlement
and CAS yields a typed retry/conflict, never a mutation based on the pre-settlement snapshot. With
M6C composition the richer lifecycle path owns this sequence and the recording port observes
exactly one call, not two.

Implement and test these exact fail-open host callback contracts (all return `None`, accept bounded
documented fields plus ignored `**kwargs`, and record only a stable health code on storage failure):

```python
on_session_start(*, session_id: str, task_id: str | None = None,
                 model: str | None = None, platform: str | None = None, **kwargs) -> None
on_session_end(*, session_id: str, task_id: str | None = None,
               completed: bool = False, interrupted: bool = False,
               reason: str | None = None, **kwargs) -> None
on_session_finalize(*, session_id: str, platform: str | None = None,
                    reason: str | None = None, **kwargs) -> None
on_session_reset(*, session_id: str, old_session_id: str | None = None,
                 platform: str | None = None,
                 reason: str | None = None, **kwargs) -> None
subagent_stop(*, parent_session_id: str, child_session_id: str,
              child_subagent_id: str | None = None, task_id: str | None = None,
              child_status: str | None = None, duration_ms: int | None = None,
              reason: str | None = None, **kwargs) -> None
```

`on_session_start` resolves only one exact bound lane and appends one idempotent `session_started`
with key `session-start:<lane.stable_key>`; model/platform are 1..128 characters after
normalization, with the literal `unknown` when absent. If binding is not yet established, the next
create/resume or bound pre-tool flow runs the same `_ensure_session_started` operation before its
first session event, so restart does not depend on an in-memory pending map. A duplicate start or
deferred ensure is a no-op by the same key. `on_session_end` preserves the actual host
`completed`/`interrupted` booleans in one non-final checkpoint (reject both true), normalizes the
bounded reason, and keys idempotency by lane plus the host turn/callback identity; finalize is the
only callback that appends `session_finalized` and invokes settlement.

The real finalize callback may omit `task_id`. It performs one bounded session-wide binding
enumeration, deduplicates exact engagement UUIDs across task lanes, sorts them, then for each
uniquely resolved engagement runs the no-lock settlement/final-checkpoint/logbook sequence exactly
once. For the single lane-scoped `session_finalized` event, select the lexicographically lowest
already-exact bound `ExecutionLaneKey.stable_key` for that session+engagement as deterministic event
context; this does not change/unbind any lane. It never fabricates a root task; conflicting/corrupt bindings are skipped with typed health
state and do not cause a guessed append. `on_session_reset` refers to the *new* session ID after the
old session's finalize callback and may include `old_session_id`: it performs no checkpoint,
unbind, settlement, or closure mutation for either ID and never duplicates old finalize. It only
clears bounded health for the new session; `on_session_start` or the first bound operation will
durably start it.

`subagent_stop` accepts the real minimum host shape without child subagent/task IDs and resolves a
unique prior `child_lane_linked` relation by parent+child session; supplied optional IDs must match.
Zero or multiple relations are typed no-op health states, never first-match guesses. Map
`child_status` through the exact current-Hades table: `ok -> (completed=True, interrupted=False)`,
`timeout|interrupted -> (False, True)`, and `error -> (False, False)` with a stable reason;
and missing/unknown -> `(False, False)` plus `unknown_child_status`; never inspect or persist a raw
child summary. Validate optional duration as `0..86_400_000` ms if retained. It appends the mapped
child checkpoint and, only when that child has no in-flight calls, an exact
`lane_unbound` in one CAS batch; otherwise it retains the binding for explicit call resolution.
It never unbinds the parent or guesses among candidate engagements. Unbound/ambiguous callbacks are
no-op plus bounded health state. Every successful callback rebuilds the revision-CAS logbook.
Add duplicate/restart delivery, start-before-bind, new-session reset after old-session finalize,
finalize without `task_id` over multiple task lanes/engagements (including two lanes on one
engagement and deterministic event lane), every child-status mapping,
child-stop with/without an in-flight call, and ambiguous/unbound regressions; assert exactly one
event/binding effect per engagement and no automatic close.

- [ ] **Step 5: Run adapter tests and verify RED**

```bash
.venv/bin/pytest tests/engagement/test_hades_adapter.py -v
```

Expected: failure because `HadesEngagementAdapter` does not exist.

- [ ] **Step 6: Implement strict tool inputs and typed results**

Define frozen, extra-forbid Pydantic inputs:

```python
class _ManageEngagementInput(BaseModel):
    action: Literal["create", "resume", "inspect", "list", "close", "reopen",
                    "abandon", "change_scope", "change_objective", "unbind",
                    "resolve_call"]
    engagement_id: UUID | None = None
    display_name: Annotated[str | None, Field(min_length=1, max_length=256)] = None
    objective: Annotated[str | None, Field(min_length=1, max_length=8192)] = None
    authorization: Annotated[tuple[Annotated[str, Field(min_length=1, max_length=2048)], ...],
                             Field(max_length=256)] = ()
    required_proofs: Annotated[
        tuple[ProofRequirement, ...], Field(max_length=MAX_REQUIRED_PROOFS)
    ] = ()
    reason: Annotated[str | None, Field(min_length=1, max_length=1024)] = None
    authorization_basis: Annotated[str | None, Field(min_length=1, max_length=2048)] = None
    call_id: Annotated[str | None, Field(pattern=r"^call-[0-9a-f]{64}$")] = None
    resolution: Literal["timed_out", "abandoned"] | None = None
    after_engagement_id: UUID | None = None
    after_call_id: Annotated[str | None, Field(pattern=r"^call-[0-9a-f]{64}$")] = None
    limit: int = Field(default=64, ge=1, le=64)

class RecordDecisionInput(BaseModel):
    engagement_id: UUID | None = None
    proposal_id: UUID | None = None
    custom_strategy: Annotated[str | None, Field(min_length=1, max_length=8192)] = None
    rationale: Annotated[str | None, Field(min_length=1, max_length=8192)] = None
    host_adapted_command: HostAdaptedCommandRecord | None = None

class AddSourceInput(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=256)]
    locator: Annotated[str, Field(min_length=1, max_length=4096)]
    topics: Annotated[tuple[Annotated[str, Field(min_length=1, max_length=128)], ...],
                      Field(max_length=64)] = ()
    notes: Annotated[str, Field(max_length=8192)] = ""


class PublicStringInventoryPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    items: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = Field(
        max_length=MAX_PUBLIC_INVENTORY_ITEMS
    )
    total_count: int = Field(ge=0, le=MAX_JOURNAL_EVENTS)
    next_after_id: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    omitted_items_sha256: Sha256Hex | None = None


class EngagementPublicSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    engagement_id: UUID
    display_name: Annotated[str, Field(min_length=1, max_length=256)]
    status: EngagementStatus
    revision: JournalRevision
    bound_lanes: PublicStringInventoryPage
    active_decisions: PublicStringInventoryPage
    in_flight_call_ids: PublicStringInventoryPage
```

A model validator enforces fields per action and rejects list-only pagination fields on other
actions. `create.required_proofs` is explicit and defaults to empty; never derive it from
`objective`. `resolve_call` requires all of `call_id`, resolution, and reason. `after_call_id` is
accepted only by `inspect`; `resume` returns the deterministic first page and callers continue with
`inspect` rather than expanding the mutation response.
`RecordDecisionInput` is an exact two-branch union enforced by an after-validator: proposal
selection requires only `proposal_id` and the service resolves its event-bound strategy/rationale
for the current lane; custom selection forbids `proposal_id` and requires both `custom_strategy` and
`rationale`. Missing or mixed forms fail schema validation, and an unknown/cross-lane proposal is a
typed `proposal_not_found`. The handler normally resolves the engagement from the trusted exact
lane; an optional explicit UUID must equal that binding and can never override it. Omitted succeeds,
while a mismatch returns typed `engagement_conflict|lane_unbound` without append. Convert bounded
authorization strings through `ValidatedTarget` and `AuthorizationScope` before creating any
journal path. All scalar/count validation runs before root creation, registry lookup, sorting, or
rendering. Add exact-limit/one-over schemas for every field/tuple above. Both list and ambiguity
results expose at most 64 candidates sorted by normalized display name/creation time/UUID and
return `total_count`, `next_after_engagement_id`, and an ordered `omitted_candidates_sha256` when
more exist. Resume ambiguity always refuses to choose even when the displayed candidate page is
truncated. Add 64/65 and 10,000-engagement serialization tests; public JSON remains compact and
deterministic.
Add JSON-schema/runtime cases for both valid decision branches and every missing/mixed/extra
combination; M6B reuses this exact proposal-selection contract.
The optional `host_adapted_command` is accepted on either valid decision branch, deep-validated
before event construction, and forwarded unchanged to the service. It never changes branch
selection or becomes a source suggestion. Add aggregate event-size, provider-secret, and JSON
round-trip tests.

The adapter projects every successful create/resume/inspect/lifecycle mutation into
`EngagementPublicSummary`; it never serializes `EngagementSnapshot.events`, raw scope/evidence, or
the unbounded state tuples. Each inventory page is sorted by canonical stable ID, contains at most
`MAX_PUBLIC_INVENTORY_ITEMS`, reports the true count, and when truncated carries both the last
returned cursor and SHA-256 of the ordered omitted IDs. `inspect(after_call_id=...)` paginates
in-flight calls without a journal mutation; lane/decision inventories use the same bounded internal
paginator and return their first page in schema v1. Decision/source success envelopes contain only
their bounded public ID plus the resulting `EngagementPublicSummary`/`SourceRegistryResult`, never a
snapshot or managed-source bytes. Before returning, canonicalize the complete envelope and require
`<= MAX_HOST_RESULT_BYTES`; overflow returns closed `result_too_large` with `retryable=false`
rather than silent truncation. Add maximal-state, exact-cap/one-byte-over, 64/65-item, and
projection-privacy tests.

Return compact typed JSON. The closed error vocabulary is:

```text
invalid_input
host_context_required
invalid_target
unauthorized_scope
engagement_not_found
engagement_ambiguous
engagement_conflict
invalid_transition
proposal_not_found
call_not_found
lane_unbound
journal_unavailable
journal_corrupt
evidence_capture_failed
in_flight_limit_exceeded
result_too_large
source_registry_failed
unsupported_platform
evidence_budget_exhausted
interpretation_incomplete
interpretation_failed
settlement_unavailable
```

Never return raw filesystem, hook, provider, or host exceptions. Every non-complete settlement uses
one envelope: `{"ok": false, "error": {"code": <closed safe code>, "retryable": true},
"settlement": {"status": ..., "pending_range_count": ..., "next_pending_offset": ...,
"next_pending_subject": ..., "pending_inventory_sha256": ...}}`; it
contains no engagement snapshot/status/frontier. Non-settlement errors omit `settlement` but use the
same `ok/error` shape. Unknown safe codes fail model validation.

- [ ] **Step 7: Implement hook capture and health reminders**

Use the documented callback shapes directly: `pre_tool_call(tool_name, args, task_id, **kwargs)` and `post_tool_call(tool_name, args, result, task_id, duration_ms, **kwargs)`. General hook kwargs such as `session_id`, `turn_id`, `api_request_id`, `api_call_count`, and optional `tool_call_id` supply correlation; a future documented `tool_call_ordinal` may enable the stable fallback, but current Hades does not emit it. Accept additional `**kwargs` for forward compatibility, but never require undocumented `status`, `error_type`, or `error_message`. Normalize the lane from host kind, session, and task. Include `telemetry_schema_version` in safe event metadata but do not require it for old hosts.

Define the constants exactly once in `events.py`; `hades_adapter.py` imports and re-exports these same objects:

```python
CONTROL_TOOL_POLICY_VERSION = "sedna.control-tools.v1"
CONTROL_TOOL_NAMES = frozenset(
    {
        "sedna_manage_engagement",
        "sedna_plan_next",
        "sedna_record_decision",
        "sedna_add_source",
        "sedna_learn_local",
        "sedna_retrieve_knowledge",
        "sedna_get_knowledge_artifact",
        "sedna_knowledge_maintenance",
    }
)
```

On an exact control name, do not create `tool_call_started`/result-evidence events. If the lane resolves uniquely, append `control_tool_invoked` with only the exact name, policy version, and typed correlation. Stable correlation uses idempotency key `control:<policy-version>:<stable-correlation-key>:<tool-name>`, so a replayed pre hook produces one event; uncertain correlation remains explicitly non-deduplicable. State-changing management, decision, and source handlers also append their specific typed semantic event; existing learn/retrieve/artifact/maintenance calls therefore remain visible without leaking their results. `sedna_plan_next` is reserved for M6B and will additionally emit planner-specific events. If create/resume starts unbound, its handler's opening/resume/binding event is the authoritative typed record; never attach a control event to a guessed engagement.

Pre-tool flow:

1. compare `tool_name` to `CONTROL_TOOL_NAMES`, but do not yet construct correlation or persist;
2. run the shared bounded structural sanitizer/normalizer before *any* correlation or idempotency
   material. On normalization failure, use a preallocated-event uncertain correlation with no raw
   argument digest;
3. resolve exact/univocal binding and append exact binding when needed. For a control name, append
   only `control_tool_invoked` from the sanitized correlation and return without ordinary argument
   sidecar/summary capture;
4. for an operational name, create typed correlation from sanitized arguments and resolve stable idempotency against the
   existing start *before* evidence capture or closure cancellation; an exact duplicate returns;
5. persist the sanitized argument sidecar with its capture limitation, or prepare a typed
   `evidence_capture_failed(capture_role="arguments")` without repr/raw/digest when normalization is
   impossible;
6. construct one atomic batch in this exact order: exactly one of `evidence_attached` or argument
   `evidence_capture_failed`, optional `closure_cancelled`, `tool_call_started`, optional
   `unplanned_action`, optional `uncertain_correlation`; this same-batch invariant prevents a
   visible cancellation without its genuinely new replacement call;
7. rebuild the session logbook projection after the authoritative append.

For `sedna_add_source`, the pre-hook sanitizer additionally runs the same source-secret classifier
on `locator`, `name`, `topics`, and `notes` before correlation. A detected value is replaced in the
sanitized correlation structure by one constant marker (not a digest); the semantic handler then
rejects the input fail closed. Two distinct secret values therefore produce the same redacted
argument digest, and neither each raw secret, its direct SHA-256, nor its canonical-context digest
may appear in any journal/evidence/log/error byte.

Add a control-call fixture whose nested arguments contain `provider_token`; neither the value nor
its SHA-256 appears in any journal/evidence/log/error/correlation byte. A cyclic control argument
produces uncertain correlation and no ordinary sidecar, never raw `repr` output.

Post-tool flow:

1. locate the original start by stable correlation across engagement journals, not by current lane binding;
2. for uncertain correlation, link only when exactly one open candidate matches. If multiple
   candidates all belong to one engagement, its sealed hook capability may append one
   `unmatched_tool_completion(reason_code="ambiguous_within_engagement")` with no guessed call ID;
   zero candidates or candidates spanning engagements have no authoritative destination, so append
   no journal/evidence and set only `unmatched_completion` health for the pinned store/session. If
   the one stable call is already explicitly terminated, record typed unmatched completion in that
   exact engagement instead of completing twice;
3. if `result is not None`, save the original result before appending its reference; on a typed
   capture failure prepare `evidence_capture_failed(capture_role="result")`; never canonicalize
   `None` to JSON bytes;
4. append either `[evidence_attached, tool_call_completed]`,
   `[evidence_capture_failed, tool_call_completed(evidence=None)]`, or for `None` the lone
   `tool_call_completed(evidence=None)` atomically. Without an optional recognized host
   `tool_status`, record `returned` only for a delivered non-None result and `unknown` for None;
5. mark simple flag-shaped text only as `possible_terminal_evidence`; do not interpret or close;
6. rebuild the logbook. A capture/storage error with a still-available journal must never leave the
   start in flight; only a journal append/write failure records health and requires later
   `terminate_tool_call`.

Map an optional host `tool_status` (or legacy extra named `status`) only through a closed technical-status table. Do not inspect result/error prose, command exit-looking text, or flags to determine strategic success. Never persist raw provider `error_message`; `error_type` may be retained only after mapping to a bounded closed host code. `blocked`, `cancelled`, `error`, `returned`, and `unknown` all terminate the technical call; M6B later assesses the strategic outcome from evidence.

Construct the adapter with
`settlement_port_factory: EngagementSettlementPortFactory | None = None`. At the start of each host
tool/hook invocation, call the dynamic zero-configuration `root_resolver` exactly once, validate
and retain that absolute root/store identity, and use it for every M6A service reopen and the
factory context in that invocation. The factory's internal `open(resolved_root)` may receive this
pinned path, but the returned port's public `settle(engagement_id, reason=...)` remains rootless.
Never let the settlement adapter resolve a profile independently. Open one factory context for all
distinct engagements handled by a session-wide finalize callback and close it once. Implement these exact no-lock sequences:

```text
resume:
  pin resolved_root once for this host invocation
  commit resume/binding mutation
  close M6A service context and release every journal/projection/source lock
  with settlement_port_factory.open(resolved_root) as port: outcome = port.settle(..., reason="resume")
  if outcome.status != "complete": return typed non-complete control result without snapshot/status/frontier
  reopen M6A service at the same resolved_root and reload the post-settlement snapshot
  return that reloaded revision/snapshot

close or reopen:
  pin resolved_root once for this host invocation
  resolve the exact engagement/lane, then close M6A service context and release every lock
  with settlement_port_factory.open(resolved_root) as port: outcome = port.settle(...)
  if outcome.status != "complete": return the typed non-complete result without lifecycle mutation
  reopen M6A service, reload the post-settlement snapshot, and CAS request_close/reopen against it
  return the resulting authoritative snapshot (`close` remains `closing` in M6A)
  M6C later routes these actions through its richer lifecycle service using the same one settlement;
  it must not invoke this M6A adapter sequence and settle a second time

session_finalize:
  pin resolved_root once; enumerate distinct engagement IDs at that store; release every lock
  open one port factory context; settle each ID exactly once with reason="session_finalize"
  reopen M6A service and load the settled revision
  if outcome.status == "complete": append session_finalized(reason="finalized")
  else: append session_finalized(reason="settlement_<status>", pending range metadata and safe_code)
  use expected_revision equal to the post-attempt revision
  rebuild the logbook at the resulting final revision
  return None to the host hook; expose the typed outcome only to the adapter's internal test helper
```

When no port is configured, skip only the settle call and retain the same close/reopen/reload
ordering for standalone M6A. The port is never called after a final checkpoint/logbook has already
been written. An exception is converted to `status="unavailable"` and safe code
`settlement_unavailable`; never expose the exception. With a configured port, `incomplete`,
`failed`, or `unavailable` resume never returns a potentially stale engagement status/frontier.
Finalize may record that the host session ended, but the event and logbook must carry the exact
non-complete outcome/pending range and cannot say the evidence was cleanly settled. The next
`pre_llm_call` surfaces a typed bounded reminder. Add adapter tests with more than 2 MiB pending
evidence proving resume exposes no stale snapshot and finalize cannot look clean. M6B supplies the
adapter implementation without redefining this protocol.

Add an alternating/profile-switch resolver test: even if the configured active profile changes
after the first resolve, the mutation, settlement, reload, final checkpoint, and logbook all touch
only the initially pinned store, and the resolver count is exactly one. The next independent host
invocation observes the new profile.

Maintain a thread-safe in-process health map keyed by
`(pinned_store_identity_digest, session_id)`, not session alone. A hook failure records only a
stable code; reset/purge is scoped to the same store identity. `pre_llm_call` returns at most a 1
KiB control reminder for a uniquely active engagement or unhealthy journal; it never injects
evidence or a frontier. Implement the map as one lock-protected insertion-ordered LRU containing
only `(closed_code, occurrence_count)` per already-bounded key. Define adapter-private:

```python
HookHealthCode = Literal[
    "journal_unavailable", "journal_corrupt", "evidence_capture_failed",
    "in_flight_limit_exceeded", "unmatched_completion", "ambiguous_binding",
    "unbound_lane", "unknown_child_status", "settlement_incomplete",
    "settlement_failed", "settlement_unavailable", "logbook_rebuild_conflict",
]
```

Validate the code, store digest, and
session ID against their exact model bounds before insertion, keep at most
`MAX_HEALTH_ENTRIES_PER_STORE` and `MAX_HEALTH_ENTRIES_TOTAL`, and evict oldest entries
deterministically (per-store first, then global). Occurrence count saturates at
`MAX_HEALTH_OCCURRENCES`; repeated failures never allocate a larger integer. Reset purges only the
pinned store/session key. Finalize purges stale health for that key at invocation start; a fully
successful finalize leaves it empty, while incomplete/failed/unavailable settlement or a later
journal/logbook failure inserts the new closed code after that purge and never purges it again.
The next `pre_llm_call` can therefore surface the failure shown in the RED test. Add unknown-code
rejection, max/max-plus-repeat saturation, exact 512/513 per-store and 4,096/4,097 global tests,
concurrent insert/purge, success-clears/failure-survives-finalize, and profile switch isolation.

`pre_llm_call` emits only a static versioned control envelope such as
`{"kind":"sedna_engagement_health_v1","untrusted_data":{"display_name":...},"code":...}`;
all dynamic fields are JSON encoded, length bounded, explicitly labelled untrusted data, and never
concatenated into instructional prose. It includes no objective, evidence, strategy, command, or
proof value. Add newline, closing-tag/fence, and `ignore prior instructions` display-name fixtures
proving the parsed envelope remains data and stays within 1 KiB.

- [ ] **Step 8: Run adapter tests and verify GREEN**

```bash
.venv/bin/pytest tests/engagement/test_hades_adapter.py -v
.venv/bin/ruff check src/sedna/engagement/hades_adapter.py tests/engagement/test_hades_adapter.py
```

Expected: all tests pass.

- [ ] **Step 9: Commit the adapter**

```bash
git add src/sedna/engagement/hades_adapter.py tests/engagement/test_hades_adapter.py
git commit -m "feat(plugin): capture Hades engagement events"
```

### Task 8: Plugin Wiring, Manifest Contract, and Zero-Configuration Isolation

**Files:**
- Modify: `src/sedna/plugin.py`
- Modify: `plugin.yaml`
- Modify: `src/sedna/__init__.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_plugin.py`
- Modify: `tests/test_plugin_knowledge.py`
- Modify: `tests/test_plugin_knowledge_root.py`
- Create: `tests/test_plugin_engagement.py`

**Interfaces:**
- Consumes: `HadesEngagementAdapter`, existing `_knowledge_root()` resolution, existing tool registrations.
- Produces: one plugin registration containing nine tools and the M6A hook surface, version `0.2.0`.

- [ ] **Step 1: Update fake contexts and write failing plugin-surface tests**

Extend every fake context used with `register()`:

```python
def register_hook(self, name: str, callback) -> None:
    self.hooks.setdefault(name, []).append(callback)
```

Update `tests/test_plugin.py`:

```python
def test_plugin_registers_all_implemented_tools_and_hooks():
    context = FakeContext()
    register(context)

    assert [tool["name"] for tool in context.tools] == [
        "sedna_nmap_tcp_discovery",
        "sedna_nmap_service_scan",
        "sedna_learn_local",
        "sedna_retrieve_knowledge",
        "sedna_get_knowledge_artifact",
        "sedna_knowledge_maintenance",
        "sedna_manage_engagement",
        "sedna_record_decision",
        "sedna_add_source",
    ]
    assert set(context.hooks) == set(EXPECTED_ENGAGEMENT_HOOKS)


def test_manifest_declares_every_registered_tool_and_hook():
    context = FakeContext()
    register(context)
    manifest = load_plugin_manifest()
    assert manifest["provides_tools"] == [tool["name"] for tool in context.tools]
    assert set(manifest["provides_hooks"]) == set(context.hooks)
```

- [ ] **Step 2: Write failing dynamic-root and profile-isolation integration test**

Create `tests/test_plugin_engagement.py`:

```python
def test_engagement_tools_and_hooks_follow_active_profile_on_every_operation(
    tmp_path, monkeypatch
) -> None:
    active = {"home": tmp_path / "profile-a"}
    install_fake_hermes_home(monkeypatch, lambda: active["home"])
    context = HookContext()
    register(context)

    first = call_tool(
        context,
        "sedna_manage_engagement",
        create_payload("Orion-A"),
        session_id="session-a",
        task_id="root-a",
    )
    active["home"] = tmp_path / "profile-b"
    second = call_tool(
        context,
        "sedna_manage_engagement",
        create_payload("Orion-B"),
        session_id="session-b",
        task_id="root-b",
    )

    assert first["ok"] and second["ok"]
    assert (tmp_path / "profile-a" / "knowledge" / "sedna" / "engagements").is_dir()
    assert (tmp_path / "profile-b" / "knowledge" / "sedna" / "engagements").is_dir()
    assert not (tmp_path / "profile-a" / "knowledge" / "sedna" / "sources.md").exists()
```

Also prove that registration itself resolves/creates no root, context override wins, relative root fails closed, engagement schemas contain no per-call root, and invalid/unauthorized create input writes nothing.

- [ ] **Step 3: Run plugin tests and verify RED**

```bash
.venv/bin/pytest tests/test_plugin.py tests/test_plugin_engagement.py tests/test_plugin_knowledge_root.py -v
```

Expected: failures because engagement registration and hook manifest entries are absent.

- [ ] **Step 4: Wire the adapter without disturbing existing knowledge handlers**

At the end of `sedna.plugin.register(ctx)`, construct and register one adapter:

```python
HadesEngagementAdapter(
    ctx,
    root_resolver=lambda: _knowledge_root(ctx, None),
).register()
```

Do not resolve the root during registration. Do not change the existing per-call root behavior of M1–M5 knowledge tools. Do not broaden the old `_bind_context()` behavior; the M6A adapter registers its own handlers that retain `session_id` and `task_id` kwargs.

Add `provides_hooks` to `plugin.yaml` and append the three tool names to `provides_tools`. Keep Nmap tools registered but describe them as deprecated operational compatibility tools.

- [ ] **Step 5: Synchronize package versions**

Set all three public versions to `0.2.0`:

```text
pyproject.toml                 project.version
plugin.yaml                   version
src/sedna/__init__.py         __version__
```

Do not bump semantic schema, compiler, canonical repository, or SQLite retrieval versions in M6A.

- [ ] **Step 6: Run plugin and existing knowledge tests to verify GREEN**

```bash
.venv/bin/pytest tests/test_plugin.py tests/test_plugin_engagement.py tests/test_plugin_knowledge.py tests/test_plugin_knowledge_root.py -v
.venv/bin/ruff check src/sedna/plugin.py src/sedna/engagement/hades_adapter.py tests/test_plugin.py tests/test_plugin_engagement.py
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit plugin wiring**

```bash
git add src/sedna/plugin.py src/sedna/__init__.py plugin.yaml pyproject.toml tests/test_plugin.py tests/test_plugin_knowledge.py tests/test_plugin_knowledge_root.py tests/test_plugin_engagement.py
git commit -m "feat(plugin): expose persistent Sedna engagements"
```

### Task 9: Crash Replay Acceptance, Operating Guide, and Milestone Verification

**Files:**
- Create: `tests/engagement/simulated_hades.py`
- Create: `tests/engagement/test_m6a_replay.py`
- Create: `docs/llm/sedna-engagement-tools.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: complete M6A plugin and service.
- Produces: executable acceptance trace, operator protocol, confirmation of the already-approved specification status, and full-suite evidence.

- [ ] **Step 1: Write the failing cross-session acceptance test**

Create `tests/engagement/test_m6a_replay.py` with one complete simulated trace:

```python
from tests.engagement.simulated_hades import SimulatedHades, inject_partial_tail


def test_named_engagement_survives_sessions_crash_tail_closing_and_reopen(tmp_path) -> None:
    host = SimulatedHades(tmp_path / "knowledge")
    engagement_id = host.create(
        name="HTB-Orion",
        objective="Obtain user and root flags",
        target="192.0.2.44",
        required_proofs=("user-flag", "root-flag"),
        session_id="session-1",
    )
    host.decide("Enumerate exposed services", session_id="session-1")
    host.tool(
        "terminal",
        {"command": "simulated-scan 192.0.2.44"},
        "22/tcp open ssh\n80/tcp open http\nHTB{private-user-proof}",
        session_id="session-1",
        tool_call_id="call-1",
    )
    host.end_session("session-1")
    inject_partial_tail(host.knowledge_root, engagement_id, b'{"interrupted"')

    resumed = host.resume(target="192.0.2.44", session_id="session-2")
    assert resumed.engagement_id == engagement_id
    host.tool(
        "terminal",
        {"command": "simulated-read-root-flag"},
        "HTB{private-root-proof}",
        session_id="session-2",
        tool_call_id="call-2",
    )
    closing = host.close(reason="all expected proof observed", session_id="session-2")
    reopened = host.reopen(reason="platform rejected the proof", session_id="session-3")

    assert closing.status == "closing"
    assert closing.closure_ready is True
    assert reopened.status == "active"
    assert "HTB{private-root-proof}" in host.logbook_text(engagement_id, "session-2")
    assert host.valid_hash_chain(engagement_id)
    assert host.recovery_tail_count(engagement_id) == 1
    assert host.global_retrieval_contains("HTB{private-root-proof}") is False
```

The simulator calls registered handlers and hook callbacks directly; it never shells out or invokes a network tool.

- [ ] **Step 2: Run the acceptance test and verify RED before filling missing integration behavior**

```bash
.venv/bin/pytest tests/engagement/test_m6a_replay.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'tests.engagement.simulated_hades'`; the acceptance harness is introduced only in the next step.

- [ ] **Step 3: Make the acceptance trace GREEN**

Create `tests/engagement/simulated_hades.py`. `SimulatedHades` must register the real plugin handlers and Hades hook callbacks against the temporary knowledge root and expose only these thin test-driver methods: `create`, `decide`, `tool`, `end_session`, `resume`, `close`, `reopen`, `logbook_text`, `valid_hash_chain`, `recovery_tail_count`, and `global_retrieval_contains`. `recovery_tail_count()` counts typed recovery-warning attachments rather than relying on filenames. `inject_partial_tail()` must append the supplied bytes to the selected engagement journal through a descriptor opened beneath the temporary root. No method may execute a command or access the network.

Run:

```bash
.venv/bin/pytest tests/engagement/test_m6a_replay.py -v
```

Expected: one complete simulated engagement passes with no external execution.

- [ ] **Step 4: Write the Hades-facing operating guide**

Create `docs/llm/sedna-engagement-tools.md` with contract version `sedna-engagement-tools-v1` and these exact sections:

1. recognize an authorized machine task;
2. obtain or infer the human-readable name, but ask when missing, and declare expected proofs explicitly (standard HTB: separate `user-flag` and `root-flag`);
3. create or resume through `sedna_manage_engagement`;
4. call `sedna_record_decision` before a material operational branch;
5. validate any later Sedna command suggestion through `/learn`;
6. explain that hooks retain commands/results automatically;
7. treat `closing` as waiting for M6C finalization, not as a verified success;
8. resolve a timed-out/abandoned tool call explicitly after a missing post hook, and reopen after rejected evidence;
9. add optional global sources without treating them as mandatory;
10. respond to every typed error without inventing hidden causes.

Include complete JSON examples for create with explicit proof requirements, a manual-close-only create with an empty requirement array, resume, custom decision, add source, call timeout/abandon resolution, close request, reopen, and ambiguous resume. State explicitly that an empty proof list never means “already complete,” that M6A has no planner yet, and that Hades must continue using existing knowledge retrieval until M6B.

- [ ] **Step 5: Update project documentation and verify specification status**

In `README.md`, add an `M6A Engagement Journal` section describing the root layout (`engagement-state.json` versus M6B-owned `state.json`), explicit proof requirements, private evidence and provider-secret `capture_limitations`, session logbooks, control/operational hook behavior, call timeout/abandon recovery, POSIX boundary, and the single M6B settlement/M6C finalizer seams.

Verify read-only during implementation that
`docs/superpowers/specs/2026-08-11-sedna-event-journal-adaptive-planner-design.md` says
`**Status:** Approved for implementation planning` and already contains the planning-time
`journal-head.json` amendment. Do not make further specification edits from this implementation
task.

- [ ] **Step 6: Run every M6A and legacy unit test**

```bash
.venv/bin/pytest tests/engagement tests/test_plugin.py tests/test_plugin_engagement.py tests/test_plugin_knowledge.py tests/test_plugin_knowledge_root.py -v
```

Expected: all selected tests pass.

- [ ] **Step 7: Run the full quality gate**

```bash
.venv/bin/ruff check src tests
.venv/bin/pytest -q
git diff --check
```

Expected: Ruff passes, the complete pytest suite passes, and `git diff --check` prints nothing.

- [ ] **Step 8: Inspect private/global separation manually**

Run the acceptance fixture with `pytest --basetemp` pointing at a retained temporary directory, then inspect only filenames and search boundaries:

```bash
.venv/bin/pytest tests/engagement/test_m6a_replay.py -v --basetemp=/tmp/sedna-m6a-acceptance
find /tmp/sedna-m6a-acceptance -type f -print
rg -n 'HTB\{private-root-proof\}' /tmp/sedna-m6a-acceptance
```

Expected: the proof appears only beneath the private engagement evidence/logbook. It does not appear in semantic bundles, manifests, retrieval SQLite inputs, `sources.md`, or public tool responses.

- [ ] **Step 9: Commit M6A acceptance and documentation**

```bash
git add tests/engagement/simulated_hades.py tests/engagement/test_m6a_replay.py docs/llm/sedna-engagement-tools.md README.md
git commit -m "docs(engagement): complete M6A operating contract"
```

## Implementation Handoff

Implement this plan with `superpowers:subagent-driven-development`, one task at a time, with specification compliance review followed by code-quality review after every task. Do not begin M6B until Task 9's full quality gate passes and the complete public compatibility surface declared near the top of this plan can be imported from `sedna.engagement`.
