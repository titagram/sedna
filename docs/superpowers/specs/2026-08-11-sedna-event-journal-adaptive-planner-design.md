# Sedna Event Journal and Adaptive Planner Design

**Date:** 2026-08-11

**Status:** Conversational design approved; written specification pending final review

**Scope:** Persistent security engagements, append-only execution evidence, LLM-managed
strategic frontiers, Hades/Hermes plugin orchestration, operational reports, and verified case
promotion

## Relationship to Earlier Milestones

This design implements M6 from
`2026-08-07-sedna-semantic-ingestion-retrieval-design.md`. It builds on the implemented M1–M5
foundation:

- deterministic source inventory, parsing, segmentation, sanitization, and provenance;
- host-LLM semantic compilation with extractor, critic, and bounded repair;
- canonical references, cases, decision guidance, and negative evidence;
- local SQLite FTS5 and facet-aware retrieval;
- document learning, typed knowledge gaps, target validation, and Hades-facing plugin tools;
- zero-configuration storage below the active host home.

The earlier design remains authoritative for canonical knowledge. This document introduces the
runtime decision loop that the earlier design deliberately deferred.

One boundary is refined here: Sedna still does not own authoritative operation of tools, but it
may provide concrete command suggestions as contextual examples after M6 adds the required
versioned execution-example boundary. Hades or Hermes must validate and adapt those suggestions
using its `/learn` tool knowledge before execution.

The separate future Codex evaluation skill is captured in
`../../architecture/2026-08-11-sedna-adversarial-roleplay-skill.md`.

## Product Model

The target agent should be treated as an inexperienced but capable operator. A general-purpose
LLM may know how to invoke tools yet still behave like a script kiddie: it enumerates without a
coherent hypothesis, repeats low-value actions, overuses familiar tools, and fails to transfer
experience between related situations.

Sedna is the mentor and memory for that operator:

- canonical knowledge is historical experience and technical reference material;
- the Event Journal is memory of the current authorized engagement;
- the adaptive planner compares current evidence with historical experience;
- Hades or Hermes remains the operator and final decision maker;
- Hades `/learn` remains the authoritative source for exact tool operation;
- a completed, verified engagement may later become a new Sedna case study.

Sedna is functionally agent-like but is not a second autonomous daemon with an independent
conversation or provider account. It is an embedded specialist within the host plugin. It uses
the host LLM through a bounded structured-call interface and persists all durable state locally.

## Goals

1. Preserve an authorized machine-solving engagement across Hades sessions.
2. Record decisions, real commands, original outputs, observations, outcomes, and score changes.
3. Keep raw evidence out of the ordinary prompt context while making it available on demand.
4. Retrieve applicable Sedna experience from the current situation rather than from the initial
   target alone.
5. Let an LLM propose, score, and reassess a bounded strategic frontier under explicit rules.
6. Use an isolated critic and at most one repair before returning a frontier.
7. Keep Hades free to deviate from Sedna while recording why it did so.
8. Preserve exact tool-operation ownership in Hades `/learn` while allowing useful concrete
   command suggestions.
9. Support generic external research without permitting solution or flag lookup for the current
   machine.
10. Produce a private evidence-rich operational report.
11. Promote only verified engagements into sanitized, provenance-backed global experience.
12. Provide reproducible replay, crash recovery, and auditable planning changes.

## Non-goals

- Adding new operational execution to Sedna or letting the M6 planner invoke tools directly;
  existing pilot Nmap tools are a documented, deprecated compatibility exception.
- Replacing Hades authorization, approval, sandbox, or safety policy.
- Running a separate Sedna LLM daemon or requiring a separate provider credential.
- Implementing a deterministic semantic scoring engine in M6.
- Training or fine-tuning model weights.
- Automatically trusting a walkthrough, web source, or user-suggested source.
- Searching for the current machine's walkthrough, solution, or flag.
- Ingesting raw engagement logs into global retrieval.
- Implementing SysReptor integration in this milestone.
- Implementing deep `/learn` capability resolution beyond a stable hint boundary.
- Implementing the Codex adversarial roleplay skill in the same milestone.

## Responsibility Boundary

### Sedna owns

- engagement identity, lifecycle, sessions, and journal persistence;
- safe evidence storage and correlation;
- derivation of the current situation;
- retrieval of historical cases, references, negative evidence, and guidance;
- LLM observation extraction, frontier generation, criticism, and repair;
- local frontier scores and retry conditions;
- explanations and provenance for strategic suggestions;
- operational report generation;
- sanitized candidate-case compilation and verified promotion.

### Hades or Hermes owns

- the host LLM, model selection, authentication, and provider policy;
- recognizing when an authorized security engagement should begin or resume;
- obtaining a human-readable engagement name when none is supplied;
- validating suggested commands using `/learn`;
- selecting and invoking real tools;
- adapting commands to installed versions and runtime conditions;
- live authorization, approval, sandboxing, and operational safety;
- the final choice among Sedna proposals or an independently reasoned alternative.

### The user owns

- authorization and scope;
- the engagement's human-readable name and objective when they cannot be inferred safely;
- explicit scope changes;
- optional source suggestions;
- optional verification or rejection of a candidate flag when no platform verification exists.

## High-Level Architecture

```text
user task
    -> Hades/Hermes host agent
    -> Sedna engagement start or resume
    -> Event Journal + pending evidence
    -> host-LLM observation extractor
    -> current situation
    -> Sedna canonical retrieval
    -> host-LLM frontier planner
    -> isolated host-LLM critic
    -> optional single repair
    -> ranked strategic suggestions and command examples
    -> Hades validates via /learn and executes
    -> pre/post tool hooks append raw evidence
    -> next planning cycle reassesses the frontier
    -> flag, report, verification, and optional case promotion
```

The deterministic runtime validates, persists, orders, caches, and enforces scope. It does not
decide whether HTTP, FTP, SSH, Active Directory, or another strategic path is semantically best.

## Host Integration

### Embedded specialist

Sedna is loaded as a plugin. The current Hades integration is the reference adapter. The core
journal and planner must remain host-neutral so a Hermes adapter can translate equivalent tool,
hook, session, and structured-LLM APIs without changing domain logic.

Sedna uses the LLM already exposed by the host plugin context. Calls are stateless and structured;
the model receives only the bounded context required for its role. Sedna owns prompt versions but
not model credentials.

### Plugin tools

The M6 surface should remain compact:

- `sedna_manage_engagement`: create, resume, inspect, close, verify, reject, reopen, or abandon;
- `sedna_plan_next`: process pending evidence and return the current validated frontier;
- `sedna_record_decision`: select a proposal or record an independent Hades strategy;
- `sedna_add_source`: add a user-suggested source to the shared registry.

Existing document-learning, retrieval, artifact, and maintenance tools remain available. The
planner normally invokes retrieval internally rather than forcing Hades to orchestrate every
retrieval call.

### Guided, non-coercive protocol

Hades is instructed to consult Sedna at the beginning of an engagement and whenever evidence
materially changes the strategic situation. Sedna does not block an operational tool merely
because no proposal was selected.

An unplanned action is still recorded and assessed. This preserves creativity, supports useful
operator deviations, and avoids fragile classification of every support tool as strategic or
non-strategic. A future strict mode may be evaluated only if real traces show that guidance is
insufficient.

### Session reminders

At session start, the adapter may inject a small control-plane reminder containing only the
active engagement name, status, and instruction to consult Sedna before the next material
decision. It must not inject the whole journal or frontier into every user turn.

## Physical Layout

The existing zero-configuration root remains unchanged:

```text
<host-home>/knowledge/sedna/
```

M6 adds project-scoped shared sources and engagements without moving existing canonical storage:

```text
<host-home>/knowledge/sedna/
├── sources.md
└── engagements/
    └── <engagement-uuid>/
        ├── engagement.json
        ├── events.jsonl
        ├── evidence/
        │   ├── 20260811-143022-htb-orion.md
        │   ├── nmap-event-0021.txt
        │   └── response-event-0037.json
        ├── state.json
        ├── strategy-ledger.json
        ├── strategy-archive.jsonl
        ├── frontier.json
        ├── reports/
        │   ├── report-v1.md
        │   └── report-v1.json
        └── promotion/
            ├── candidate-case.json
            └── sources/
                ├── promotion-v1.md
                └── promotion-v1.provenance.json
```

Existing explicit host knowledge-root overrides remain supported. M6 introduces neither a new
root environment variable nor a storage migration solely for this feature.

## Shared Source Registry

`<host-home>/knowledge/sedna/sources.md` is global to the Sedna installation rather than to one
machine. It records broadly useful information sources suggested by the user, supplied by Sedna,
or discovered during engagements.

An entry should retain:

- name and URL or locator;
- topical coverage;
- origin: user-suggested, built-in, or discovered;
- status: suggested, consulted, useful, contradicted, stale, or preferred;
- optional usage notes and last observation date.

A suggestion is a priority hint, not an allowlist or an assertion of truth. Hades may search
elsewhere. Actual queries and sources consulted for one engagement remain in that engagement's
journal and logbook.

## Engagement Identity and Lifecycle

### Identity

Every engagement has:

- an internal immutable UUID;
- a required human-readable display name;
- an initial objective stated in natural language;
- initial authorized targets and scope;
- a creation timestamp;
- host and schema identity metadata.

Subsequent objective, scope, session/task association, lifecycle, and update timestamps are
derived from authoritative events rather than duplicated as mutable manifest truth.

The UUID is normally hidden. If exactly one open engagement is compatible with the current task,
Hades resumes it automatically. Ambiguity is resolved using display name, target, and date. An IP
or hostname alone is never a globally unique engagement identity.

### Lifecycle states

- `active`;
- `closing`;
- `closed_unverified`;
- `closed_verified`;
- `abandoned`.

Finding all flags or other proofs required by the stated objective appends a closure request and
transitions to `closing`. New planning stops, while tool calls already in flight may finish and
their results remain journaled. When every pre-request call has completed, timed out, or been
explicitly abandoned, terminal settlement can transition to `closed_unverified` and generate the
report. A contradictory late result cancels closure and returns to `active`. Platform or user
confirmation promotes a completed snapshot to `closed_verified`.

The closure-request event records a terminal watermark and the exact set of in-flight call IDs
across every lane bound to the engagement. A close event and report snapshot are invalid until a
matching completion, timeout, or explicit abandonment event exists for every call in that set.
The final close event records the satisfied barrier and report revision. Recovery recomputes this
set from the journal instead of trusting a mutable counter.

If Hades starts a new operational call while `closing`, the non-coercive hook records it and
cancels the closure request back to `active`; Sedna never emits a report that omits an accepted
in-flight or newly started action.

A rejected flag, revoked verification, or explicit continuation appends a reopen event and
returns the engagement to `active`. Closing never deletes or seals the journal. An abandoned
engagement remains resumable.

For objectives requiring multiple proofs, such as user and root flags, finding only one records
progress but does not close the engagement.

## Authoritative and Derived Records

### Authoritative records

- `engagement.json`: immutable identity header, initial objective and scope, and version metadata;
- `events.jsonl`: append-only chronological event stream;
- original evidence payloads stored inline in typed events or in referenced `evidence/` sidecars,
  including outputs, attachments, flags, and runtime secrets.
- committed promotion source revisions and their provenance maps after a
  `promotion_source_committed` event binds their exact digests.

Objective, scope, and lifecycle changes after creation are authoritative events rather than
silent rewrites of the identity header.

### Rebuildable projections

- `state.json`: compact current situation;
- `strategy-ledger.json`: bounded hot strategy families, variants, attempts, and reassessments;
- `strategy-archive.jsonl`: paginated compact projections of the remaining archived strategies;
- `frontier.json`: most recent validated strategic frontier;
- rendered per-session Markdown logbooks;
- report Markdown renderings;
- candidate case artifacts.

Each versioned report JSON is an immutable generated snapshot bound to an event revision. It is
not authoritative engagement state. If it must be repaired or regenerated, Sedna creates a new
report revision rather than silently rewriting it; its Markdown rendering remains reproducible.

A damaged projection must be rebuildable without changing authoritative events or evidence.

## Event Model

Every event has a closed, versioned envelope containing at least:

```json
{
  "sequence": 42,
  "event_id": "uuid",
  "timestamp": "2026-08-11T14:30:22Z",
  "engagement_id": "uuid",
  "session_id": "host-session-id",
  "task_id": "optional-host-task-id",
  "turn_id": "optional-host-turn-id",
  "host_kind": "hades|hermes|other",
  "actor": "user|host_agent|sedna|tool",
  "actor_id": "optional-host-agent-id",
  "type": "outcome_assessed",
  "payload": {},
  "previous_event_hash": "sha256",
  "event_hash": "sha256"
}
```

The hash chain detects accidental truncation, rewriting, and incoherent replay. It is not a claim
of external notarization.

Initial event families include:

- engagement opened, resumed, closed, verified, reopened, or abandoned;
- session started, checkpointed, and ended;
- scope or objective changed;
- user note or source suggested;
- tool call started and completed;
- evidence attached;
- observation extracted and hypothesis formed;
- outcome assessed;
- plan requested;
- frontier proposed, criticized, repaired, or rejected;
- strategy selected or agent deviation recorded;
- flag found, verified, or rejected;
- report generated;
- case candidate compiled, promoted, superseded, or revoked.

Arbitrary untyped payloads are not accepted at the persistence boundary.

### Execution lanes

Concurrent work is correlated through an explicit execution-lane key composed from host kind,
session ID, and task ID. When the host has no task ID, the adapter supplies a stable root-task
identifier for that session. Turn ID identifies a call within the lane but does not replace the
lane identity.

Calling `sedna_manage_engagement` binds the caller's lane to exactly one engagement. A child task
inherits that binding only through an explicit parent-child event supplied by the adapter. Hooks
never guess an engagement from a target string when more than one lane or engagement exists.

Each execution lane has its own active decision. `sedna_record_decision` updates only the caller's
lane, and a pre-tool hook associates a call only with the exact lane decision. If no exact binding
exists, the action is unplanned or unbound; it is never attached to another concurrent task's
decision. The engagement journal still provides one monotonic global event sequence across all
of its lanes.

## Evidence and Session Logbooks

Every host session linked to an engagement creates one human-readable logbook such as:

```text
evidence/20260811-143022-htb-orion.md
```

The timestamp includes sufficient precision to avoid same-day collisions. The logbook is
persisted and updated during the session, but remains reproducible from journal events and
original evidence. It contains:

- the session objective and starting situation;
- proposed strategies and score changes;
- Hades decisions and deviations;
- suggested and actually executed commands;
- original textual outputs;
- extracted observations and outcome assessments;
- flags and credentials found;
- consulted sources;
- final checkpoint and remaining frontier.

Original textual results may be stored in bounded typed event payloads and reproduced verbatim in
the logbook. Large text, binary data, or structured responses use content-addressed or
event-addressed sidecar files. The logbook links them with hashes and metadata. Sedna preserves
the exact bytes delivered by the host; when the host itself supplies a truncated result or only an
external artifact reference, the journal records that limitation. Sedna must not silently
truncate additional evidence; configurable quotas must fail visibly or spill to sidecars.

Byte-exact content remains in the event payload or sidecar. The Markdown logbook renders only a
safe escaped representation: dynamic code fences or escaped text prevent captured Markdown,
HTML, links, or scripts from becoming active document structure. A link to a sidecar must never
be mistaken for proof that its content was interpreted.

Flags and runtime credentials are intentionally retained at this private execution layer so task
success can be verified. They are excluded at later retrieval and promotion boundaries.

## Current Situation Projection

The situation contains only decision-relevant state:

- objective progress;
- authorized target facts;
- confirmed observations;
- hypotheses with supporting and contradicting evidence;
- inferred OS, architecture, environment, services, controls, and reachability;
- current access and available credentials or secret references;
- attempted strategies and execution variants;
- negative evidence, incompatibilities, and unresolved ambiguity;
- knowledge gaps;
- pending evidence not yet interpreted.

Every material field cites one or more journal events. Facts, hypotheses, and unknown values remain
distinct. A projection revision and digest bind a frontier to the exact situation from which it
was produced.

## Strategy Ledger and Frontier

The strategy ledger is the durable engagement-local memory needed to avoid erasing a weak path.
It is a rebuildable projection over authoritative strategy and outcome events, but it retains all
known strategy identities rather than only the currently visible recommendations.

Its hierarchy is:

- **strategy family:** a durable intent such as obtaining SSH access or enumerating HTTP;
- **execution variant:** one way of pursuing the family, such as credential reuse, common
  credentials, or a bounded wordlist attempt;
- **attempt:** the actual tool call or group of calls made in one state revision.

The runtime assigns immutable IDs to all three levels after validating model-provided bounded
keys and ancestry. An LLM cannot replace identity merely by changing prose. Merges, splits, and
superseding relationships require explicit planner output, critic approval, and journal events.

For every family and variant, the ledger retains:

- current and historical scores;
- status and last relevant state revision;
- supporting and contradicting evidence;
- attempts and outcomes;
- prerequisites and retry conditions;
- parent, replacement, or superseding relationships;
- reason for becoming available, deferred, blocked, exhausted, completed, or archived.

The frontier is only the ranked active view of this ledger. Hades normally receives the top three
to eight proposals, but low-scoring, deferred, blocked, and exhausted entries remain represented
in the hot ledger or archive. A planner revision must explicitly retain, update, merge, supersede,
complete, block, or archive every hot or selected reactivation strategy; silent disappearance is
a validation failure. Archived entries retain a compact summary and can be reactivated when their
retry conditions become true.

This distinction lets an exhausted exact `rockyou` attempt reach zero while SSH credential access
as a broader family remains available for later credentials or changed conditions.

The complete ledger may grow on disk within the engagement storage quota, but it is never
inserted wholesale into an LLM request. Initial schema limits are 32 hot families, 64 hot
variants, and 16 archive reactivation candidates per planning call. Available and deferred
entries occupy the hot set; older blocked, exhausted, completed, and superseded entries move to a
paginated compact archive projection while remaining fully reconstructible from events.

Retry conditions are typed predicates over situation facts, prerequisites, evidence categories,
and state revisions. The reducer—not a semantic scorer—selects at most 16 archived entries whose
predicates may now match. Planner input contains the hot set, those candidates, and an aggregate
archive summary capped initially at 16 KiB. If the model proposes a key that already exists in the
archive, the runtime reuses its identity and loads its bounded detail instead of creating a
duplicate.

At the cap, the planner must merge, supersede, complete, or archive redundant candidates before
adding more; failure to reconcile within the limits produces a planning gap rather than silent
loss. A later schema version may tune these limits using measured traces.

## LLM Decision Pipeline

### Untrusted-data boundary

Every observation, tool result, target response, web page, source-registry entry, user-supplied
document, canonical excerpt, prior model output, and command literal is untrusted data, never an
instruction to the observation extractor, planner, critic, or repair call.

Each versioned role prompt states this rule independently. Untrusted values are passed in bounded
structured data fields and are never concatenated into the system-instruction section. The model
must ignore embedded requests to change role, reveal secrets, modify scope, suppress evidence, or
alter its output contract. Retrieved text can support a claim only through provenance; it cannot
change the planner policy.

The critic is an additional semantic check, not the primary prompt-injection boundary. All model
responses undergo deep schema, scope, reference, and size validation before persistence.

Regression fixtures must include adversarial terminal output, HTML, Markdown, web content,
`sources.md` entries, and canonical text that attempt instruction override, data exfiltration,
scope expansion, false flag declaration, or solution lookup.

### Lazy evidence interpretation

Pre- and post-tool hooks persist facts quickly and do not invoke the LLM after every command.
`sedna_plan_next` first processes any pending completed tool results in a bounded batch.

This ensures that evidence is durable before interpretation, avoids slowing every tool call, and
allows related tool outputs to be assessed together.

### Mandatory settlement points

Lazy interpretation must not leave a final flag pending forever. One internal
`settle_pending_evidence` operation runs the observation extractor, updates objective state, and
applies any automatic unverified closure. It is invoked by:

- every `sedna_plan_next` call;
- explicit close, verify, reject, and report operations;
- clean session finalization before the final checkpoint;
- engagement resume before status or frontier is returned.

Hades protocol instructions require settlement after every materially relevant result and before
announcing task completion. The synchronous post-tool hook still performs persistence only; it
marks possible terminal evidence so the adapter can schedule settlement outside the hook and all
repository locks. If the process crashes before scheduled settlement or clean finalization, the
next resume performs it before exposing engagement state.

Automatic closure therefore means closure as part of the first successful settlement that
confirms all objective proofs, without requiring a separate user close command. It does not mean
that the persistence hook performs an inline LLM call. Settlement first enters `closing` when
other lane calls are in flight and emits the final report only after their terminal events are
accounted for.

### Observation extractor

The extractor emits:

- confirmed observations;
- hypotheses;
- missing information;
- technical execution status;
- strategic outcome category;
- evidence references.

Outcome categories are closed:

- `progress`;
- `partial_progress`;
- `no_effect`;
- `negative_evidence`;
- `incompatible`;
- `execution_error`;
- `ambiguous`.

An execution error must not be represented as proof that the strategy is poor.

### Situation-conditioned retrieval

After reducing pending observations, Sedna retrieves references, analogous cases, decision
guidance, and negative evidence using the current situation and authorization scope. Retrieval
scores remain evidence for the planner; they are not copied directly into frontier weights.

Case studies describe what worked in a particular context. They are inspiration subject to
transfer checks, never immutable playbooks.

### Planner

The planner receives a bounded package containing:

- objective and current situation;
- bounded hot strategy ledger, selected archive reactivation candidates, archive summary, and
  frontier from the previous revision;
- relevant recent events;
- prior attempts and their outcomes;
- retrieved knowledge and provenance;
- relevant shared or session sources;
- authorization and research restrictions.

It emits a reconciliation for every hot entry and selected archive candidate plus normally three
to eight active frontier proposals, while allowing fewer when the situation is strongly
constrained. The model emits bounded family and variant keys; after validation, the runtime
assigns the immutable IDs used by Hades and the journal. A proposal contains:

- runtime-assigned proposal identifier and strategic description;
- score and confidence from 0 through 100;
- previous score when the strategy persists;
- rationale and score-change explanation;
- one or more typed authorized scope references covering the intended action;
- journal evidence references;
- canonical knowledge references;
- expected evidence or progress;
- prerequisites and retry conditions;
- status such as available, deferred, blocked, exhausted, or completed;
- capability hints;
- zero or more concrete command suggestions;
- cost, noise, and risk assessments.

The reconciliation portion explicitly disposes every previously available or deferred entry and
may reactivate archived entries whose retry conditions now match. The visible top-three-to-eight
limit never deletes the remaining strategy memory.

The planner may adapt retrieved experience, combine multiple cases, or introduce a novel strategy
not found in Sedna knowledge.

### Score semantics

M6 uses LLM-managed scores. There is no deterministic semantic formula.

- A score is relative priority in the current situation, not a calibrated success probability.
- The planner reassesses the complete frontier after material state changes.
- Every changed score cites evidence and explains the change.
- A single failed attempt does not automatically erase a strategy.
- A complete negative test should reduce a strategy more than a partial attempt.
- An execution error should not materially reduce the strategy.
- An exact action should not repeat in an unchanged state without explicit justification.
- New prerequisites or evidence may reactivate an earlier strategy.
- Out-of-scope proposals are invalid and rejected before they can enter the ledger or frontier;
  they are never retained with a low score.
- Score zero is reserved for impossible or definitively incompatible in-scope actions in the
  current state.
- An exhausted exact execution variant may reach zero while its broader strategy family remains
  available when explicit retry conditions still exist.
- Cheap, high-information actions should normally be preferred early, subject to context.

These are prompt rules checked semantically by the critic, not a growing collection of
deterministic domain exceptions.

### Critic and repair

The critic receives the same evidence boundaries plus the proposed frontier. It checks:

- factual grounding and valid citations;
- no invented target facts, services, credentials, or outcomes;
- scope and authorization;
- applicability, including OS and architecture;
- distinction between case examples and universal rules;
- justified score changes and retry conditions;
- separation of execution errors from strategic failures;
- repeated-action and loop risks;
- honest knowledge gaps and uncertainty;
- safe research queries;
- command suggestions presented as examples rather than guaranteed syntax.

A failed critique permits one repair call. If the repaired frontier still fails, Sedna records a
typed `planning_gap` and returns no falsely validated new frontier. Hades may continue
independently, and subsequent actions remain journaled as unplanned.

### Deterministic guardrails

Code performs only:

- deep schema validation;
- bounded size, count, score, and confidence checks;
- event and knowledge-reference validation;
- exact authorization-scope validation of all structured target references and command bindings;
- duplicate and ordering checks;
- state-revision binding;
- caching and serialization.

A deterministic semantic scorer is deferred until real journal data demonstrates a measurable
need and supports regression evaluation.

## Tool Hook Protocol

### Pre-tool hook

For every operational tool in a bound execution lane, including deprecated Sedna Nmap pilot
tools:

- correlate engagement, session, task, turn, and current decision;
- persist the real tool name and safe argument representation;
- append `tool_call_started`;
- append an unplanned-action event if no decision is active;
- avoid recursion through an exact, versioned control-tool allowlist rather than a name prefix;
- do not block the call solely because it was unplanned.

### Post-tool hook

- correlate with the start event;
- record duration and technical status;
- persist the original result or a sidecar reference;
- append `tool_call_completed` and `evidence_attached`;
- mark the evidence pending interpretation;
- mark possible objective-completion evidence for out-of-lock terminal settlement;
- avoid an inline planning call.

Hook errors are never silently represented as successful journal writes.

### Decision binding

`sedna_record_decision` accepts either a planner proposal ID or a custom Hades strategy with a
rationale. It updates the active decision for the caller's exact execution lane. Subsequent calls
in that lane are associated with the decision until another decision or planning cycle supersedes
it. A decision in one task or session cannot replace another lane's decision. Multiple tool calls
may implement one strategic decision.

The journal separately retains:

- Sedna's strategy;
- Sedna's command suggestion;
- Hades's selected or custom strategy;
- the command Hades actually executed;
- execution status;
- strategic outcome.

## Tool-Knowledge Boundary

### Canonical execution-example extension

The implemented M1–M5 compiler deliberately removes exact commands and stores only action intent
and capability references. M6 must not claim that a command came from a prior case until that
boundary is extended.

Before the planner consumes source-backed commands, M6B adds a versioned, non-searchable
`ExecutionExample` canonical record linked to a reference or case step. It is source-owned and
lives inside the same `SemanticKnowledgeBundle` as its parent rather than in an independently
committed file. It contains:

- immutable example ID and parent artifact ID;
- source-backed command template or invocation fragment;
- tool or capability hint when established;
- purpose, prerequisites, platform constraints, and observed role;
- source references and extraction metadata;
- explicit `requires_validation = true`;
- no final flag, provider credential, or promoted runtime secret.

Target addresses and case-local credential literals are parameterized when they are not the point
of the example. Their strategic role remains available, but a literal observed in one source is
never presented as a credential for the current target.

Execution examples are committed, replaced, loaded, audited, quarantined, and deleted atomically
with their source bundle and verification record. Recompilation or source replacement cannot
leave stale examples behind. Deterministic IDs include source ownership and cited spans.

They do not enter ordinary FTS text and are not retrieved independently. An optional lookup
projection may index parent ID and example ID only, never command text. Examples are loaded from
the deeply validated canonical bundle by explicit drill-down after the parent strategic artifact
has passed applicability ranking. The semantic bundle, extractor, critic, repository, audit, and
compiler versions must be bumped, and eligible existing sources must be recompiled through the
normal migration path.

A planner command suggestion declares its origin:

- `source_example`, with an `ExecutionExample` reference;
- `model_generated`, with strategy and evidence references but no claim that the literal command
  appeared in a source;
- `host_adapted`, when Hades records the command produced after `/learn` validation.

This bundle-owned lane preserves useful examples without turning commands into globally
searchable recipes.

Every suggestion uses a structured `CommandSuggestion` rather than an opaque fully bound shell
string. It contains a command template, typed placeholders, target or credential references,
origin, capability hint, and validation requirement. The runtime resolves target placeholders
only from the engagement's authorized scope and may render a concrete preview such as an actual
in-scope IP. Any concrete IP, hostname, URL, CIDR, or other structured target already present in a
model or source literal is parsed and checked against scope before return. Unresolved or
unparseable target bindings reject the suggestion rather than relying on shell-text inspection.

This validation does not claim that arbitrary shell syntax is safe. Hades authorization,
sandboxing, and `/learn` validation still govern whether and how the rendered suggestion is
executed.

### Operational ownership

Sedna may say what to try, why, expected evidence, and provide commands that worked in similar
cases once the execution-example extension is present. It may also generate a clearly labelled
new suggestion. Hades must treat both as suggestions.

Hades `/learn` remains responsible for:

- installed tool syntax and version differences;
- correct option combinations;
- provider or environment setup;
- execution mechanics;
- adapting a suggested command to runtime constraints.

Capability hints such as `network.port_scan` or `http.inspect` provide a future integration seam.
M6 does not require a complete capability registry.

A command syntax failure lowers confidence in the execution variant, not in the underlying
strategy. A valid, complete action that produces negative evidence may lower the strategy.

### Legacy Sedna Nmap tools

The current plugin still exposes `sedna_nmap_tcp_discovery` and `sedna_nmap_service_scan`. They
are legacy operational pilot tools, not control-plane Sedna tools and not the intended M6
execution path. M6 deprecates them while retaining temporary compatibility; a later major plugin
revision may remove them after Hades `/learn` migration coverage exists.

Until removal, their calls are journaled exactly like any other operational tool. Hook recursion
prevention uses an exact allowlist of Sedna control tools—engagement management, planning,
decision recording, source management, knowledge retrieval, artifact inspection, learning, and
maintenance—and never a broad `sedna_` prefix filter. The legacy Nmap tool names are excluded
from that control allowlist. A control tool records its own typed journal event when its action is
material to an engagement, so hook recursion suppression does not erase provenance.

## External Research Policy

The planner may emit a `knowledge_gap_research` strategy. Hades performs the actual search.

Allowed research includes generic technical topics:

- services and versions;
- protocols and configuration behavior;
- error messages;
- CVEs and vendor documentation;
- methodologies and techniques.

Disallowed research includes:

- the named current machine plus `walkthrough`, `writeup`, or equivalent solution terms;
- the current machine plus `flag`, `user.txt`, `root.txt`, or equivalent proof terms;
- direct searches for a known flag value.

User-suggested sources receive attention but are not mandatory. Queries, URLs, retrieval times,
excerpts, and usefulness assessments are recorded in the session logbook. Web results remain
session evidence unless later processed through normal document ingestion or verified case
promotion.

An explicit autonomous instruction such as `proceed` permits generic technical research. When
the user has not granted comparable autonomy, Hades may ask before searching.

## Reporting

Every closure creates a versioned report snapshot. Its JSON is frozen once emitted and its
Markdown is a safe rendering of that snapshot. Reopening, regeneration after repair, or closing
again creates a later version rather than rewriting history. Reports remain outputs of the
journal, never an alternative source of engagement truth.

The private operational report includes:

- scope and objective;
- session timeline;
- target observations;
- hypotheses and decisions;
- frontier changes;
- commands and outputs;
- failed attempts and dead ends;
- discovered credentials and flags;
- evidence hashes and source citations;
- final access and completion state;
- unresolved issues.

The structured JSON report is deliberately independent from SysReptor. A later adapter may map it
to SysReptor projects and findings without changing the journal schema.

## Verified Case Promotion

`closed_unverified` produces a report but cannot update global knowledge. `closed_verified`
permits automatic candidate compilation.

### Internal promotion adapter

Promotion does not pass the file under `engagements/<uuid>/promotion/` to the public
`learn-local` path. Public learning correctly rejects any source that overlaps the canonical
knowledge root.

A dedicated internal `JournalPromotionAdapter` instead:

1. reads a verified immutable journal revision;
2. renders a sanitized, versioned promotion source with no raw flag or runtime secret;
3. atomically writes that exact source plus a span-to-event/evidence provenance map under
   `promotion/sources/`, fsyncs them, and appends a `promotion_source_committed` event containing
   both digests;
4. makes the committed source and map immutable authoritative engagement assets; a later attempt
   creates a new version rather than rewriting them;
5. assigns a stable source identity in a dedicated internal `journal-promotion` namespace;
6. constructs the same strict prepared-source boundary required by the semantic compiler;
7. invokes the existing extractor, critic, repair, and canonical repository through an internal
   descriptor-confined transition;
8. never exposes a generic bypass for arbitrary files inside the knowledge root.

Canonical `SourceRef` values point to the immutable rendered promotion source and its spans. The
additional promotion map preserves upstream event/evidence provenance for audit and report
drill-down. Its source manifest repeats the committed source and provenance-map digests; missing
or mismatched assets make the canonical bundle non-current and non-retrievable. Schema and
repository versions must make `journal-promotion` identity distinct from external document
collections and prevent source-ID collisions.

`promotion/candidate-case.json` remains a disposable derived preview. Canonical provenance never
points to that preview.

The case compiler derives:

- initial and evolving context;
- applicability and environmental constraints;
- strategic steps and pivots;
- useful negative evidence;
- retry and reactivation conditions;
- alternate paths considered;
- command examples where useful;
- provenance back to journal events and evidence;
- a generalizability assessment.

Promotion sanitization:

- removes flag values;
- replaces runtime credentials with symbolic references when possible;
- removes target-specific IPs and identifiers that are not strategically meaningful;
- preserves credential discovery and reuse as strategy;
- excludes provider and host-runtime secrets;
- prevents the single successful route from becoming a universal rule.

The candidate passes through the existing semantic compiler pattern with critic and at most one
repair through this adapter. Only a verified canonical result enters global retrieval. A later
reopen or revoked verification can supersede or revoke the promoted case while retaining history.

Engagement-local outcomes never directly mutate confidence or scores on historical canonical
artifacts.

## Concurrency and Recovery

### Append and evidence order

For a tool result:

1. persist original evidence;
2. compute its digest and metadata;
3. append the referencing event;
4. flush the authoritative journal durably;
5. update rebuildable projections.

Evidence saved before an event is recoverable as an orphan. An event saved before projection
completion is replayed. A partial final JSONL record is isolated without discarding preceding
valid events.

### Idempotency

Host correlation identifiers and a stable hook-call key prevent duplicate pre/post events from
retries. Duplicate delivery must not duplicate evidence, outcomes, or planner inputs.

### Engagement locking

Appends are serialized by engagement. Events from multiple sessions or tasks retain their own
correlation IDs while receiving one monotonic engagement sequence.

No repository lock is held across a host LLM call, external search, tool call, or index operation.

### Optimistic planning commit

The planning pipeline reads a state revision, releases locks, performs LLM calls, then reacquires
the engagement lock. It commits the frontier only if the state revision is unchanged. If new
events arrived, the stale output is recorded only as failed planning metadata or discarded safely,
and planning restarts against the new revision within a bounded retry policy.

### Projection recovery

State, frontier, logbooks, and reports declare the authoritative event revision from which they
were generated. Mismatches make them stale and trigger replay rather than use.

## Security and Privacy

- Reuse the repository's existing descriptor-relative confinement, regular-file checks, and
  no-symlink storage boundaries.
- Use restrictive permissions for engagement directories and evidence files.
- Validate target syntax and authorization before creating planning or retrieval state.
- Never let Sedna expand scope inferred from scan results.
- Keep raw engagement evidence outside canonical retrieval and FTS.
- Minimize raw evidence passed to the LLM; load specific evidence only when needed.
- Permit flags and engagement credentials in private evidence, but never provider credentials or
  unrelated host secrets.
- Treat user-suggested and web-discovered sources as untrusted inputs.
- Record prompt, model, schema, and policy versions for replay and auditing.

## Failure Behavior

### Journal unavailable

If an authoritative append cannot be proven durable:

- do not claim the action was recorded;
- mark Sedna planning unavailable for that engagement;
- return a typed error from explicit Sedna tools;
- allow Hades to continue only under its own authorization and policy;
- visibly warn that subsequent work is not reliably journaled.

### Extractor, planner, critic, or repair failure

- preserve raw events and evidence;
- persist bounded failure metadata without raw provider exceptions;
- do not publish an invalid new state or frontier;
- allow an explicit retry;
- permit Hades to continue autonomously with unplanned actions recorded when the journal remains
  healthy.

### Evidence quota or unsupported data

Never silently drop original output. Spill supported content to sidecars; otherwise return a typed
storage or quota failure and retain enough metadata to explain what was not captured.

## Versioning

Version independently:

- engagement manifest schema;
- event envelope and event payload schemas;
- observation extractor prompt and schema;
- situation reducer schema;
- strategy-ledger reducer, hot/archive schemas, retry-predicate matcher, and context limits;
- planner prompt and frontier schema;
- critic prompt and finding schema;
- `ExecutionExample` schema, semantic-bundle contract, lookup projection, and drill-down API;
- execution-lane correlation and decision-binding policy;
- terminal-settlement and closure-barrier policy;
- report schema;
- promotion compiler prompt and canonical schema;
- committed promotion-source schema and event/evidence provenance-map schema.

Any version change that alters derived semantics invalidates affected projections and forces
replay or recompilation. Authoritative evidence and prior events remain immutable.

## Delivery Decomposition

### M6A — Engagement Journal

Deliver:

- engagement, session, event, evidence, and lifecycle models;
- append-only repository, hashes, idempotency, confinement, and recovery;
- per-session Markdown logbooks and evidence sidecars;
- shared `sources.md` management;
- explicit host/session/task execution-lane binding;
- Hades hook capture;
- engagement management and decision recording tools.

Success means an engagement can be named, stopped, resumed, closed, reopened, and replayed across
host sessions while retaining real commands, outputs, flags, and credentials privately.

### M6B — Adaptive LLM Planner

Deliver:

- pending-evidence observation extraction;
- current-situation projection;
- durable strategy-family, execution-variant, and attempt ledger;
- situation-conditioned canonical retrieval;
- versioned canonical `ExecutionExample` extension and source migration;
- planner, critic, and one repair;
- frontier persistence and caching;
- decision binding, deviations, command suggestions, and research strategies;
- Hades protocol instructions and mandatory terminal settlement;
- prompt-injection boundaries for every structured LLM role.

Success means a simulated multi-service machine produces sensible information gathering,
evidence-driven score changes, recovery from failed actions, and non-looping alternative choices.

### M6C — Report and Case Promotion

Deliver:

- versioned private Markdown and JSON reports;
- automatic unverified closure on required flag discovery;
- verification, rejection, and reopen flows;
- sanitized candidate-case compilation;
- internal journal-promotion adapter and event/evidence provenance map;
- verified canonical promotion, superseding, and revocation.

Success means a solved engagement produces an evidence-rich private report and a retrieval-safe
new case without leaking flags or runtime secrets.

## Acceptance Scenarios

### Multi-service adaptive path

Given FTP, SSH, and HTTP observations:

1. Sedna begins with proportionate information gathering.
2. A syntax error does not lower the strategic intent.
3. Rejected common SSH credentials lower but do not erase SSH.
4. A complete expensive brute-force failure lowers that variant more strongly.
5. Credentials later recovered elsewhere reactivate targeted SSH access.
6. The frontier cites the exact events and historical sources behind the change.
7. Low-scoring SSH families and variants remain in the strategy ledger even when absent from the
   visible top recommendations.
8. Source-backed commands are returned only through attributed execution examples; generated
   commands are labelled as model-generated.

### Applicability

- Windows-specific experience is not applied directly to a Linux target.
- Unknown architecture remains unknown rather than defaulting to x86_64.
- A new compatible observation may make a previously deferred case relevant.

### Honest knowledge gap and research

- An unsupported Android/ADB situation yields a typed knowledge gap.
- With autonomous scope, the planner may propose generic ADB research.
- It must not search for a named challenge solution or flag.
- User-suggested sources are considered but do not suppress other research.

### Persistence and recovery

- Multiple Hades sessions resume the same named engagement.
- Duplicate hooks produce one logical action.
- Concurrent session/task lanes cannot steal one another's engagement or active decision.
- A crash after evidence persistence and before projection update replays cleanly.
- A concurrent event prevents committing a frontier based on an obsolete state.
- A closure request waits for every in-flight call captured by its terminal watermark.
- A corrupted projection is rebuilt without changing the journal.

### Closure and promotion

- Finding all expected flags closes the engagement as unverified and creates a report.
- Rejecting a flag reopens it and preserves the earlier report revision.
- Verification enables promotion.
- Private reports contain the proof values.
- Promoted cases do not contain flag values or runtime credential values.
- A later engagement can retrieve the newly promoted strategy.

## Test Strategy

- model tests for every closed event and lifecycle transition;
- repository tests for confinement, permissions, hash chaining, idempotency, truncation, replay,
  quotas, and concurrency;
- hook tests using realistic Hades correlation fields and result payloads;
- concurrent execution-lane tests proving exact engagement and decision binding;
- structured host fixtures for extractor, planner, critic, repair, and failure envelopes;
- golden replay tests proving deterministic reconstruction from authoritative records;
- integration tests for current retrieval and semantic compilation;
- end-to-end simulated engagements with no real security tool execution;
- adversarial tests for invalid targets, prompt injection, active Markdown/HTML, scope expansion,
  opaque or mismatched command target bindings, solution searches, stale frontiers, silent
  strategy loss, repeated actions, false flags, terminal-barrier races, and secret leakage;
- full-suite, Ruff, formatting, and diff checks before each milestone is integrated.

## Deferred Work

- deterministic or learned semantic scorer based on accumulated journals;
- direct `/learn` capability registry and command verification handshake;
- SysReptor adapter;
- vector retrieval evaluation;
- model-weight fine-tuning;
- multi-agent distributed planning beyond correlated Hades tasks;
- strict pre-tool enforcement mode;
- Codex `sedna-adversarial-roleplay` skill.

## Adversarial Roleplay Follow-up

After M6 interfaces stabilize, a separate Codex skill will accept a solved authorized walkthrough
as hidden ground truth. Codex will act as game master, Hades + Sedna as player, and all tool
results will be simulated. The player must not see or retrieve the solution.

The skill will diagnose failures before tuning, distinguish Sedna defects from Hades `/learn` or
simulator defects, rerun fresh isolated scenarios, and reject machine-specific fixes that do not
generalize. Initially, `fine tuning` means prompt, retrieval, journal, and integration calibration;
model-weight training remains a separately approved project.

## Final Decisions

- Use an embedded Sedna specialist, not a separate autonomous daemon.
- Keep Hades as final operator and `/learn` as authoritative tool-operation knowledge.
- Permit Sedna to suggest concrete commands while requiring Hades validation and adaptation.
- Use persistent named engagements with hidden UUIDs and automatic unambiguous resume.
- Store authoritative append-only events and original private evidence.
- Generate one Markdown logbook per host session.
- Bind engagements and active decisions per explicit host/session/task execution lane.
- Retain flags and engagement secrets privately for success verification.
- Keep shared source suggestions in `<host-home>/knowledge/sedna/sources.md`.
- Use a pull-first, guided, non-coercive Hades protocol.
- Process evidence lazily at the next strategic planning request.
- Run mandatory terminal settlement before finalization, resume, closure, verification, or report.
- Let the LLM manage frontier scores under explicit rules.
- Preserve every strategy family, execution variant, and attempt in a durable strategy ledger;
  expose only a ranked active subset as the frontier.
- Use an isolated critic and at most one repair.
- Treat every external or retrieved value as untrusted data in every LLM role.
- Keep deterministic logic limited to validation, authorization, ordering, caching, and recovery.
- Add a versioned non-searchable canonical execution-example lane before claiming source-backed
  command suggestions.
- Deprecate existing Sedna Nmap pilot tools, journal them as operational calls, and exclude only
  exact control-tool names from hook capture.
- Allow generic autonomous research but prohibit current-machine solution and flag searches.
- Generate a report at unverified closure and promote knowledge only after verified success.
- Promote through a dedicated internal journal adapter rather than the public overlapping-source
  learning path.
- Keep engagement-local adaptation separate from global canonical experience.
- Deliver M6 as Journal, Planner, and Report/Promotion increments.
- Design the adversarial roleplay workflow as a later, separate Codex skill.
