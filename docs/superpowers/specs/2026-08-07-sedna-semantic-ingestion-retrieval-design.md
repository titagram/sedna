# Sedna Semantic Ingestion and Retrieval Design

**Date:** 2026-08-07

**Status:** Approved for implementation

**Scope:** Semantic compilation, automatic verification, canonical knowledge, local retrieval,
and the Hades-facing document-learning workflow

## Relationship to the Foundation

This design continues the deterministic ingestion foundation described in
`2026-08-06-sedna-knowledge-ingestion-design.md` and implemented on `main` through the
`PreparedSource` boundary.

The earlier design remains authoritative for inventory, source classification, structural
parsing, segmentation, sanitization, provenance, immutable raw sources, and the separation
between Sedna strategy and Hades tool-operation skills. This document supersedes its semantic
extraction, rule-review, physical-storage, retrieval, and rollout decisions where they differ.

In particular:

- human approval is not required for canonical knowledge;
- automatic verification is graded rather than binary;
- applicability is represented as typed context plus extensible facets;
- knowledge quality is multidimensional rather than stored as one immutable weight;
- SQLite FTS5 is the first rebuildable retrieval projection;
- Elasticsearch is excluded from the initial implementation;
- vector retrieval is considered only after measured retrieval failures justify it;
- the Hades host LLM performs semantic compilation through a versioned Sedna contract.

## Context

Sedna receives heterogeneous source material: technical lessons, reference documentation,
cheatsheets, machine walkthroughs, challenge walkthroughs, assets, and low-substance files.
The deterministic foundation can identify usable documents and turn them into safe,
provenance-backed logical segments, but it deliberately does not claim to understand their
strategic meaning.

The next phase must let the LLM used by Hades or Hermes understand those segments and emit
uniform, recoverable strategic knowledge. Technical documentation and walkthroughs occupy
different epistemic roles:

- technical documentation is a reference used to understand concepts, constraints, evidence,
  exceptions, and valid methodologies;
- a walkthrough is a case study recording what worked or failed in one particular environment;
- neither source type is an immutable instruction;
- case studies inspire adaptation only after their transfer conditions are checked;
- references can still be outdated, incomplete, contradicted, or inapplicable.

The system must remain autonomous. It must not require a human to approve every extracted
artifact, but it must preserve enough provenance, uncertainty, and verification evidence to
avoid silently promoting unsupported claims.

## Goals

1. Compile accepted `PreparedSource` segments into canonical strategic artifacts.
2. Preserve the context in which every observation, method, and case step applies.
3. Treat missing context as unknown, never as universal compatibility.
4. Separate source reliability, extraction confidence, generalizability, context specificity,
   and corroboration instead of collapsing them into one permanent score.
5. Verify semantic extraction automatically with an isolated critic and deterministic checks.
6. Store canonical knowledge in portable, Git-friendly JSON or JSONL files.
7. Build a disposable local index supporting full-text search and composable facets.
8. Give Hades a natural workflow for requests such as “go to this folder and learn.”
9. Return explicit knowledge gaps when no sufficiently applicable knowledge is available.
10. Keep detailed tool operation in Hades `/learn` skills and strategic intent in Sedna.
11. Preserve a clean boundary for a later Event Journal and adaptive planner.

## Non-goals

- Fine-tuning an LLM in the first release.
- Teaching Sedna exact command syntax or replacing Hades tool skills.
- Treating a case study as a universally valid playbook.
- Requiring human review for routine ingestion.
- Running Elasticsearch or another external search service.
- Adding embeddings or a vector database without retrieval evaluation evidence.
- Rewriting raw source files or replacing them with generated prose.
- Searching the web for a flag, an exact machine solution, or a named-machine walkthrough.
- Building the Event Journal, frontier planner, or weight backpropagation in this milestone.

## Responsibility Boundary

### Sedna owns

- deterministic preparation of raw knowledge sources;
- semantic extraction contracts and prompt versions;
- critic contracts and automatic adjudication;
- canonical strategic schemas;
- applicability context and facets;
- provenance, uncertainty, verification, and source relationships;
- canonical file storage and derived retrieval indexes;
- retrieval of references, cases, negative evidence, and knowledge gaps;
- strategic action intents, expected evidence, and state transitions.

### Hades or Hermes owns

- the host LLM and authentication used for structured extraction;
- user and session context;
- tool-specific skills and exact tool operation;
- authorization and safety policy for live actions;
- actual observations produced during execution;
- pre-tool and post-tool lifecycle hooks;
- the future runtime planner and Event Journal orchestration.

Sedna may refer to a Hades capability by stable semantic identifier, but it must not duplicate
the capability's operational instructions.

## High-level Architecture

```text
raw sources
    -> deterministic inventory, classification, parsing, sanitization
    -> PreparedSource and LogicalSegment records
    -> semantic extractor through the host LLM
    -> isolated semantic critic
    -> one bounded repair attempt when needed
    -> deterministic schema, provenance, and safety validation
    -> canonical JSON/JSONL artifacts
    -> rebuildable SQLite FTS5 and facet projection
    -> lane-aware retrieval for the Hades planner
```

The LLM does not produce an intermediate Markdown rewrite for Sedna to parse again. Its output
is structured data validated directly against versioned Pydantic schemas. Human-readable
Markdown may be rendered from canonical records for inspection, but it is never the source of
truth.

## Semantic Compilation

### Host LLM contract

Sedna invokes the LLM exposed by the Hades or Hermes plugin context using structured completion.
The plugin supplies no separate provider key and does not own model authentication. Each call
uses:

- a versioned extractor identifier and prompt;
- a strict output schema;
- the smallest source segment set that preserves semantic coherence;
- source IDs and spans that the model must cite;
- bounded output size;
- model and prompt metadata recorded in every emitted artifact.

An extractor request is stateless. It must not depend on an earlier conversational answer or
unrecorded chain of thought.

### Segment-level semantic routing

Routing happens per semantic segment rather than once for the whole document. A document may be
hybrid and emit several artifact classes. The extractor assigns zero or more of these roles:

- reference concept;
- reference methodology;
- constraint or prerequisite;
- evidence interpretation;
- decision guidance;
- anti-pattern;
- case or case step;
- negative case or failed attempt;
- no usable strategic knowledge.

A document-level type is useful context but never forces all of its segments into one role.

### Explicit, inferred, derived, and unknown

Every material claim records how it was obtained:

- `explicit`: directly stated or unambiguously shown by the source;
- `inferred`: a plausible interpretation needed to connect source evidence;
- `derived`: computed deterministically from canonical facts or relationships;
- `unknown`: deliberately not asserted because the source does not establish it.

An inferred claim must cite the evidence that motivated it. Unknown values are retained when
their absence affects applicability. The extractor may not fill them with a likely default.

## Applicability Context and Facets

Applicability is not represented by flat tags alone. Every applicable artifact has a context
profile containing a typed core plus extensible domain facets.

### Typed core

The initial typed core contains, when known:

- operating-system family, distribution or edition, and version;
- CPU architecture;
- execution environment: physical, VM, container, cloud, mobile, embedded, or unknown;
- system role: workstation, server, domain controller, gateway, network appliance, or unknown;
- identity context: standalone, Active Directory, LDAP, cloud identity, or unknown;
- services, protocols, products, and versions;
- initial access and current privileges;
- network position, reachability, and topology constraints;
- relevant security controls and configuration conditions;
- observation or validity date.

### Extensible facets

New domains use namespaced facets without forcing an immediate schema migration. Examples are:

```yaml
facets:
  active_directory:
    domain_role: member_server
  web_application:
    framework: unknown
  mobile:
    adb_authorized: unknown
```

Frequently used, stable facets may later be promoted into the typed core through a schema
version change.

### Applicability relations

Each context value uses one of these relations:

- `observed`: true in the source case or explicitly described by the reference;
- `required`: necessary for the method or inference to apply;
- `compatible`: known to be compatible but not required;
- `incompatible`: a material contradiction;
- `unknown`: relevant but not established.

Each value also records `origin`, `confidence`, and source references. For example:

```yaml
context:
  values:
    - namespace: platform
      key: os_family
      value: windows
      relation: observed
      origin: explicit
      confidence: 1.0
      source_refs: [segment-reference]
    - namespace: identity
      key: environment
      value: active_directory
      relation: required
      origin: inferred
      confidence: 0.74
      source_refs: [segment-reference]
```

Architecture is therefore a condition of applicability, not merely a search keyword. A Windows
case is not silently generalized to Linux. If the current OS is unknown, the case may remain a
candidate with an uncertainty penalty and a suggestion to gather discriminating evidence. If
the current OS is known to be incompatible, the case is filtered or strongly demoted.

## Multidimensional Epistemic Assessment

Canonical artifacts do not store a single permanent “weight.” They store independent dimensions:

- `source_reliability`: credibility and completeness of the source;
- `extraction_confidence`: confidence that the structured artifact matches the cited source;
- `generalizability`: expected transfer beyond the source environment;
- `context_specificity`: how narrowly the artifact depends on its original environment;
- `verification_status`: automatic verification lifecycle;
- `support_count`: number of independent supporting sources;
- `contradiction_count`: number of independent contradicting sources;
- `observed_outcome`: success, failure, mixed, informational, or not applicable;
- `freshness`: age relative to version-sensitive claims;
- `independence_group`: copied or derivative sources share a group and do not multiply support.

The runtime score is computed for a particular current situation. It may combine:

- lexical or later semantic relevance;
- positive facet matches;
- required-condition coverage;
- incompatible-condition penalties;
- missing-assumption penalties;
- information gain;
- expected cost, time, and risk;
- source reliability and extraction confidence;
- corroboration and contradiction;
- current Event Journal attempts, failures, repetition, and dead ends.

The score is query-local and disposable. Updating runtime strategy does not rewrite historical
facts or pretend that a past case had a different outcome.

## Automatic Verification Lifecycle

Human approval is not required. The lifecycle is:

- `extracted`: valid structured output from the semantic extractor;
- `verified`: an isolated critic agrees with the material claims and deterministic validation
  passes;
- `corroborated`: independent sources consistently support the artifact;
- `contested`: credible sources or critic evidence materially disagree;
- `deprecated`: a version, product, or later evidence makes the artifact obsolete;
- `rejected`: the artifact is unsafe, unsupported, malformed, or devoid of strategic value.

`approved` is not used as an epistemic synonym for truth. Existing schema values that encode a
human-review workflow must be migrated or compatibility-mapped when this design is implemented.

### Critic pass

The critic receives the source segments, source references, and extracted artifacts, but not the
extractor's hidden reasoning. It checks:

- factual fidelity to the cited source;
- omitted prerequisites and exceptions;
- architecture, platform, topology, version, and privilege constraints;
- accidental generalization from one case;
- confusion between correlation and requirement;
- explicit versus inferred classification;
- unsupported confidence;
- loss of failed attempts or negative evidence;
- unsafe or flag-bearing searchable material;
- leakage of target-specific details into transferable strategy.

The same model may serve as extractor and critic if the host exposes only one model. Separate
requests and prompts provide process isolation, not statistical independence.

### Adjudication

1. If extractor and critic agree and deterministic validation passes, emit `verified` artifacts.
2. If the critic identifies repairable defects, perform one bounded repair call with the critic
   findings.
3. Re-run critic and deterministic validation on the repaired result.
4. If material disagreement remains, quarantine the artifact or source segment.
5. A quarantine result is explainable and does not abort unrelated ingestion.

The bounded repair prevents self-review loops.

## Canonical Technical Reference Model

Technical documentation is not represented as a pseudo-walkthrough. It emits small,
source-backed artifacts, each covering one coherent claim or decision.

### Reference artifact classes

- `concept`: terminology, protocol behavior, and mental models;
- `methodology`: a strategic way to approach a class of problem;
- `constraint`: prerequisites, boundaries, and incompatibilities;
- `evidence_interpretation`: what an observation supports or weakens;
- `decision_guidance`: when an action intent is worth considering;
- `negative_evidence`: an observation that lowers a hypothesis without necessarily eliminating it;
- `anti_pattern`: premature, costly, repetitive, or misleading behavior;
- `exception`: a counterexample or condition under which common guidance fails.

### Required semantic content

A reference artifact includes, as applicable:

- subject and concise statement;
- applicability context;
- prerequisites and required evidence;
- action intent rather than exact command syntax;
- expected information gain;
- success, failure, and stop implications;
- evidence interpretation;
- exceptions, warnings, and counterexamples;
- version and observation-date bounds;
- Hades capability references;
- multidimensional assessment;
- precise source references.

The extractor must not invent a procedure merely to fill an empty field. Optional fields remain
empty or explicitly unknown.

## Canonical Walkthrough and Case Model

A walkthrough is compiled as an observed case, not a lesson that must be copied.

### Case context

The case records:

- the typed and extensible applicability profile;
- starting state and access;
- source quality;
- transferable and non-transferable properties;
- overall outcome without final flags;
- ordered case steps;
- case-wide uncertainties.

### Case step

Each step records:

- relevant state before the step;
- observations and their provenance;
- explicit or inferred hypotheses;
- selected strategic action intent;
- expected information gain when it can be inferred safely;
- Hades capability reference when available;
- actual evidence category and observed result;
- state after the step;
- failed alternatives and negative evidence;
- transfer conditions;
- case-specific details excluded from strategic indexing.

A successful action is evidence that the strategy worked in that recorded context. It is not
proof that the action is optimal or universally applicable. A failed action reduces the
plausibility of the associated hypothesis under similar conditions but does not necessarily
create a permanent dead end.

## Decision Guidance and Rule Synthesis

Case steps and references may propose decision guidance automatically. A candidate contains:

- trigger observations;
- applicability context and prerequisites;
- rationale;
- action intent;
- expected evidence and information gain;
- success, failure, and stop transitions;
- exceptions and alternative hypotheses;
- capability references;
- supporting and contradicting sources.

Single-source guidance may become `verified` but remains visibly single-source and retains lower
corroboration. Multiple copied walkthroughs count as one independence group. Corroboration raises
support without erasing contradictions.

## Physical Storage

Original files remain immutable. Generated knowledge is stored separately:

```text
raw_src/                                  # immutable and not committed by default

knowledge/
├── manifests/<source_id>.json            # deterministic source state
├── references/<source_id>.jsonl          # canonical technical artifacts
├── cases/<case_id>.json                   # canonical ordered cases
├── guidance/<artifact_id>.json            # canonical decision guidance
├── verification/<artifact_id>.json        # critic and adjudication summary
├── quarantine/<source_id>/<record_id>.json
└── ingestion_reports/<run_id>.json

indexes/
└── sedna.sqlite                           # disposable local projection
```

JSON and JSONL are canonical for machine-produced knowledge. They are deterministic, portable,
diffable, and suitable for Git. YAML may be accepted as import material or rendered for humans,
but the semantic pipeline does not require a separate human-maintained truth lane.

The SQLite file remains ignored by Git. It can be deleted and rebuilt completely from canonical
knowledge files.

## SQLite Retrieval Projection

The first implementation uses the Python standard-library SQLite binding and FTS5. It does not
run a separate service.

### Logical tables

```text
artifacts
    artifact_id, artifact_type, knowledge_role, canonical_path,
    verification_status, source_reliability, extraction_confidence,
    generalizability, context_specificity, observed_at

facet_values
    artifact_id, namespace, key, value, relation, origin, confidence

artifact_links
    from_artifact_id, relation, to_artifact_id

artifact_sources
    artifact_id, source_id, path, location, independence_group

artifact_fts
    statement, rationale, observations, action_intent,
    expected_evidence, exceptions
```

Typed fields with stable query semantics receive normal columns or indexed relational tables.
Extensible facets use `facet_values`. Searchable prose is materialized in FTS5 only after
canonical validation and sanitization.

### Index contract

The index exposes a backend-neutral `RetrievalIndex` interface. The initial implementation is
`SQLiteRetrievalIndex`. A future backend must satisfy the same behavioral contract and may not
become the source of truth.

Required operations include:

- atomic artifact upsert after canonical emission;
- source-scoped deletion and reindex;
- complete rebuild;
- facet filtering;
- lane-aware full-text search;
- retrieval by artifact ID;
- index consistency and provenance audit;
- deterministic ordering for equal scores.

## Retrieval Flow

### Situation construction

Before searching, Hades builds a `CurrentSituation` from known facts and the Event Journal when
available. It contains:

- validated target identifiers;
- observed context facets;
- current access and privileges;
- services and technologies;
- active hypotheses;
- tried actions and outcomes;
- unresolved questions;
- authorized scope.

Syntactic validity is checked before retrieval. For example, `300.456.456.123` is rejected as an
invalid IP address without inventing a penetration-testing plan.

### Query planning

The host LLM may translate the current situation into bounded normalized search terms, synonyms,
and facets. Query construction is separate from artifact scoring and cannot override hard
incompatibilities or authorization policy.

### Epistemic lanes

Retrieval returns separate lanes:

1. relevant technical references;
2. analogous successful case steps;
3. negative cases, failed attempts, and counterexamples;
4. decision-guidance candidates;
5. missing prerequisites and unresolved knowledge gaps.

Scores from unlike lanes are not compared as if they represented the same kind of evidence.

### Ranking

The first implementation combines:

- FTS5 lexical relevance;
- normalized synonym expansion;
- required facet coverage;
- positive contextual similarity;
- incompatibility and unknown-condition penalties;
- verification and source dimensions;
- freshness for version-sensitive claims;
- diversity across sources and independence groups.

The result includes score components and rejection reasons so Hades can explain why an artifact
was selected or excluded.

### Knowledge-gap behavior

If no artifact clears minimum applicability and verification thresholds, Sedna returns a typed
knowledge gap rather than generic advice. Hades can then say that the knowledge is unavailable
and offer either:

- technical web research;
- ingestion of documents supplied by the user;
- a safe observation intended only to identify the missing context, when supported by existing
  knowledge and authorization.

This is required for domains such as Android over ADB when the current knowledge base contains no
relevant verified artifacts.

## Vector Retrieval Evaluation Gate

Elasticsearch is explicitly excluded from the initial implementation. No vector database is
selected now.

Semantic or vector retrieval is introduced only if a versioned golden retrieval suite shows a
material failure of normalized lexical search plus facets. Evaluation must measure:

- recall at K for relevant references, cases, and negative evidence;
- precision at K;
- facet and hard-constraint correctness;
- rate of dangerously inapplicable retrievals;
- latency, memory, index size, and rebuild time;
- deterministic reproducibility where practical;
- operational burden on local Hades installations.

A candidate vector backend must demonstrate a meaningful improvement on paraphrased or analogous
queries without worsening platform and applicability filtering. Vector similarity never replaces
typed facets, provenance, or hard incompatibility checks. Hybrid retrieval is preferred over
vector-only retrieval if a vector backend is adopted.

The backend-neutral `RetrievalIndex` contract keeps this evaluation possible without changing
canonical artifacts.

## Web Research and Promotion

When retrieval reports a material knowledge gap, Hades may search for technical information using
protocols, products, versions, behaviors, errors, standards, advisories, or capability concepts.
It must not query for:

- a final flag or known flag value;
- an exact machine solution;
- a machine name combined with `writeup`, `walkthrough`, `solution`, or equivalent terms.

Web findings begin as session evidence. Promotion into canonical Sedna knowledge uses the same
deterministic preparation, semantic extractor, critic, validation, provenance, and indexing
pipeline. URL, retrieval time, version context, and source class are required.

## Hades-facing Document-learning Skill

Sedna needs a Hades-facing skill for requests such as:

> Go to `/path/to/documents` and learn this material.

The skill orchestrates but does not define the canonical truth. Its workflow is:

1. resolve and validate the requested path;
2. confirm readable files remain within the authorized source root;
3. invoke deterministic inventory and preparation;
4. invoke semantic extraction through the host LLM;
5. invoke the isolated critic and bounded repair;
6. validate and emit canonical artifacts;
7. update the local retrieval projection;
8. report accepted, excluded, quarantined, unchanged, and failed sources;
9. report artifact counts, covered domains, and remaining knowledge gaps.

The skill must be idempotent. Unchanged source, parser, schema, extractor, prompt, and model inputs
must not silently produce duplicate artifacts. Version changes trigger controlled re-extraction.

The prompt and examples belong to Sedna's versioned semantic compiler, not solely to prose inside
the skill. This lets Hades and Hermes invoke the same extraction contract.

## Relationship to the Event Journal

The canonical knowledge base stores historical priors and source-backed experience. The future
Event Journal stores what is happening in the current authorized execution.

Pre-tool and post-tool hooks provide deterministic facts such as tool identity, arguments,
status, duration, output category, and error. Explicit decision events record the agent's chosen
hypothesis and rationale. The journal incrementally constructs `CurrentSituation` without
reinjecting the complete history on every prompt.

Runtime outcomes modify frontier scores, not canonical source facts. After a completed session,
the journal may be compiled into a new case through the same semantic, critic, and validation
pipeline. Tool events provide stronger provenance than reconstructed prose, while inferred
rationale remains labeled as inferred.

## Failure Handling

- Extractor timeouts fail the affected segment and leave deterministic preparation intact.
- Invalid structured output receives at most the bounded repair attempt.
- Persistent extractor/critic disagreement creates a quarantine record.
- Missing source references reject the affected claim or artifact.
- Unsupported context claims are removed or marked inferred with reduced confidence.
- Unknown context stays unknown and cannot become a wildcard.
- Contradictions are retained and linked rather than overwritten.
- One malformed source does not abort a folder ingestion run.
- Canonical writes are atomic; index updates occur only after canonical success.
- Index failure leaves canonical files valid and reports that a rebuild is required.
- Rebuild verifies every canonical record before indexing it.
- Searchable fields retain final-flag protections. Source-authored credential literals are treated
  as case-local examples, never as current-target credentials; their truth is deliberately not
  inferred during ingestion, and retrieval must communicate their evidentiary role rather than
  recommend reusing their exact value.
- LLM prompt material from untrusted sources is data, never agent instruction.

## Testing Strategy

### Semantic golden corpus

Extend the deterministic golden corpus with reviewed expected artifacts for:

- a pure technical lesson;
- a pure methodology document;
- a complete Windows walkthrough;
- a complete Linux walkthrough;
- a hybrid lesson and case document;
- a failed attempt and recovery;
- an architecture-dependent strategy;
- an ambiguous architecture inference;
- contradictory sources;
- copied walkthroughs in one independence group;
- a source with instructions attempting to manipulate the extractor;
- a source with raw and encoded final flags;
- an unsupported domain that must remain a knowledge gap.

### Test layers

1. Schema tests for context, facets, assessment, and verification lifecycle.
2. Extractor-contract tests with deterministic fixture responses.
3. Critic and adjudication state-machine tests.
4. Provenance and unsupported-claim tests.
5. Canonical serialization and idempotency tests.
6. SQLite projection and complete rebuild tests.
7. Facet-composition and incompatibility tests.
8. Lane-aware retrieval tests.
9. Knowledge-gap and invalid-input behavior tests.
10. Live host-LLM integration tests behind an explicit marker.
11. Prompt-injection, flag-leakage, and path-confinement tests.

### Retrieval acceptance scenarios

- A Windows-specific case is not presented as directly applicable to a confirmed Linux target.
- An unknown OS retains conditional candidates and identifies OS discovery as useful evidence.
- A valid private IP produces relevant information-gathering references when they exist.
- An invalid IP is rejected before knowledge retrieval.
- An Android/ADB request with no qualifying artifacts returns a typed knowledge gap.
- Negative evidence and failed cases appear beside successful analogies when relevant.
- Copied sources do not create false corroboration.
- Deleting the SQLite index and rebuilding it preserves retrieval results.
- No final flag or runtime/provider credential enters canonical searchable fields or the index.
- Credential literals present in a source remain explicitly case-local/example-only and are never
  presented as valid credentials for the current target.

## Milestones

### M1 — Deterministic ingestion foundation

Status: complete on `main`.

Outcome: accepted sources become safe, provenance-backed `PreparedSource` records; excluded and
quarantined files remain explainable.

### M2 — Semantic compiler

Deliver:

- applicability and assessment schemas;
- technical-reference and enhanced case schemas;
- extractor and critic contracts;
- automatic adjudication and bounded repair;
- canonical JSON/JSONL emission;
- semantic golden corpus.

Success means representative prepared sources can be converted into verified, source-backed
strategic artifacts without human approval.

### M3 — Local retrieval

Deliver:

- backend-neutral retrieval contract;
- SQLite FTS5 and relational facet projection;
- rebuild and audit commands;
- lane-aware queries and explainable score components;
- golden retrieval evaluation.

Success means Sedna can retrieve appropriate references and cases while excluding incompatible
contexts and returning explicit gaps.

### M4 — Hades document-learning skill

Deliver:

- natural “learn this folder” workflow;
- host-LLM structured extraction integration;
- idempotent incremental ingestion;
- operator report and recovery behavior.

Success means a user can supply a folder and make its verified strategic knowledge available to
subsequent Hades sessions.

### M5 — Minimum learned-knowledge demo

Deliver an authorized hypothetical demonstration in which Hades:

- invokes Sedna explicitly;
- validates the supplied target identifier;
- retrieves technical references and analogous experience;
- states assumptions and applicability conditions;
- proposes strategic next observations rather than unsupported commands;
- distinguishes a supported answer from a knowledge gap;
- offers research or document ingestion when knowledge is missing.

### M6 — Event Journal and adaptive planner

Specify and implement separately after M2–M5. It will connect live observations, decisions,
frontier scoring, dead-end feedback, and later case promotion.

### M7 — Semantic/vector retrieval evaluation

Run only after the lexical and facet golden suite contains demonstrated recall failures. Adopt a
vector database only when the measured improvement justifies its operational and reproducibility
cost.

## Handoff Notes

This document is the durable context for continuing from another machine or a fresh Codex task.
A new agent should begin by reading, in order:

1. `README.md`;
2. `docs/superpowers/specs/2026-08-06-sedna-knowledge-ingestion-design.md`;
3. this document;
4. `docs/superpowers/plans/2026-08-06-sedna-ingestion-foundation.md`;
5. the current schemas under `src/sedna/knowledge/schema/`;
6. `src/sedna/knowledge/pipeline.py` and `src/sedna/knowledge/repository.py`.

The next action after user review is to create an implementation plan for M2 only. M3 and M4
depend on the contracts established by M2 and should receive separate implementation plans. The
Event Journal and adaptive planner remain a separate design effort.

## Final Decisions

- Preserve raw sources and deterministic preparation unchanged.
- Use the Hades or Hermes host LLM as a structured semantic compiler.
- Use an extractor plus isolated critic and one bounded repair attempt.
- Require no routine human approval.
- Store knowledge as canonical JSON/JSONL with precise provenance.
- Model applicability with typed context, extensible facets, relations, origin, and confidence.
- Keep epistemic dimensions separate and compute strategy scores at query time.
- Structure technical documentation independently from chronological walkthrough cases.
- Use SQLite FTS5 plus relational facets as the first rebuildable local index.
- Exclude Elasticsearch from the initial implementation.
- Evaluate vector retrieval only through measurable golden-suite benefit.
- Return typed knowledge gaps instead of improvising unsupported competence.
- Keep Sedna strategic and leave exact tool operation to Hades `/learn` capabilities.
- Implement the semantic compiler before retrieval, the learning skill, and the Event Journal.
