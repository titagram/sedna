# Sedna Knowledge Ingestion Design

Date: 2026-08-06

## Context

Sedna needs to turn a heterogeneous HTB/CTF corpus into strategic knowledge for an agent. The goal is not to teach individual tool syntax: Hades skills already own that responsibility. Sedna must help the agent decide what to investigate, why an action is justified, which evidence to seek, how evidence changes the current state, and when an approach should stop.

The current corpus contains 175 Markdown files:

- 19 scraped HTB Academy pages under `raw_src/01_information-gathering`;
- 71 Academy notes, lessons, and cheatsheets;
- 60 challenge files, mostly descriptions and final flags without a solution path;
- 25 machine files ranging from complete walkthroughs to flags, external links, and exploit references.

The corpus also contains hundreds of linked images and a small number of PDFs, archives, and other assets. Its quality and structure vary substantially.

The existing ingestion code recognizes only Setext level-one titles, removes image references, assigns a phase from one path fragment, and splits content by paragraph length. This loses operational sequence and cannot distinguish a lesson from a historical case.

## Goals

1. Preserve every original source and its provenance.
2. Extract strategic reference knowledge from lessons and technical documentation.
3. Extract ordered, evidence-backed case studies from complete walkthroughs.
4. Prevent case-specific details from becoming universal rules.
5. Support incremental ingestion of the current corpus and future writeups.
6. Produce human-reviewable, version-controlled canonical artifacts.
7. Build a disposable retrieval index from those artifacts.
8. Let the agent research technical knowledge on the web when local knowledge is insufficient, without searching for flags or direct machine solutions.

## Non-goals

- Teaching command syntax, flags, payload construction, or detailed tool operation; Hades skills own this.
- Fine-tuning a model directly on the raw corpus.
- Indexing final flags as strategic knowledge.
- Automatically treating a successful walkthrough step as a reusable rule.
- Adding a vector database or knowledge graph before retrieval tests justify it.
- Mirroring arbitrary web pages into the canonical knowledge base without review.

## Responsibility Boundary

Sedna owns strategic intent:

- current-state interpretation;
- hypotheses and alternatives;
- action intents;
- prerequisites and transfer conditions;
- expected evidence;
- success, failure, and stop transitions;
- analogous cases, counterexamples, and knowledge gaps.

Hades owns execution knowledge:

- selecting an appropriate installed skill or tool;
- command syntax and parameters;
- safe execution;
- raw output parsing;
- returning normalized evidence to the agent state.

Sedna records `capability_ref` identifiers such as `hades.skill.web_vhost_discovery`; it does not duplicate a Hades skill's operating instructions.

## Two-axis Taxonomy

Every source has a document type, while every extracted record has an independent epistemic role and artifact type.

### Document types

- `lesson`: narrative technical or methodological documentation;
- `machine_walkthrough`: an ordered machine-solving account;
- `challenge_walkthrough`: a challenge solution containing a genuine method;
- `cheatsheet_reference`: concise reference material;
- `external_stub`: a link or reference without locally available substance;
- `excluded`: empty, flag-only, malformed, or otherwise unsuitable for decision extraction.

Document type selects the parser profile and extraction route. It does not determine whether an individual claim is true.

### Knowledge roles

- `reference`: concepts, constraints, methodology, and evidence expectations used to orient the agent;
- `case_study`: a historical sequence used by analogy, never as an instruction to replay blindly;
- `negative_case`: a failed or invalidated path that helps the agent avoid unjustified repetition.

### Artifact types

- `concept`;
- `methodology`;
- `decision_rule`;
- `case`;
- `case_step`;
- `negative_evidence`;
- `anti_pattern`.

There is intentionally no `tool_recipe` artifact. Tool recipes belong in Hades skills.

## Epistemic Contract

References orient; cases inspire.

A reference artifact describes terminology, prerequisites, constraints, valid checks, evidence, and known exceptions. It remains versioned because technical documentation can become outdated.

A case study records what was observed and what happened in one environment. Each case step declares its transfer conditions and case-specific details. The planner must validate those conditions against the current target before acting.

Retrieval must preserve these roles instead of merging all records into one undifferentiated ranking. A normal response contains:

1. one or more relevant reference anchors;
2. two or three analogous case steps when available;
3. a failed case or counterexample when available;
4. missing prerequisites and unresolved knowledge gaps;
5. plausible action intents and the evidence that would support or reject them.

The planner may create alternatives not present in retrieved cases. Retrieved examples constrain and stimulate reasoning; they do not define the complete search space.

## Physical Storage

Original files remain immutable in their existing formats. Generated knowledge is stored separately.

```text
raw_src/                              # immutable source material

knowledge/
├── manifests/<source_id>.json        # classification and ingestion metadata
├── references/<source_id>.jsonl      # extracted reference artifacts
├── cases/<case_id>.json              # complete ordered case studies
├── rules/
│   ├── drafts/<rule_id>.yaml          # automatically proposed rules
│   └── approved/<rule_id>.yaml        # reviewed rules
├── quarantine/<source_id>.json       # rejected or ambiguous extraction details
└── ingestion_reports/<run_id>.json   # run-level metrics and failures

indexes/
└── sedna.sqlite                       # disposable FTS5 and metadata projection
```

JSON and JSONL are canonical for machine-produced artifacts. YAML is canonical for human-reviewed decision rules. SQLite is not a source of truth; it can be deleted and rebuilt entirely from `knowledge/`.

Images remain beside their original source. Manifests store asset path, hash, alt text, and caption. OCR is performed only when an image contains material evidence that is absent from surrounding text.

## Common Record Metadata

All canonical artifacts include:

- stable `id`;
- `schema_version`;
- `artifact_type`;
- `knowledge_role`;
- `source_refs` with source ID, path, and line, section, page, or asset location;
- `origin`: `explicit`, `inferred`, or `derived`;
- `review_status`: `auto_extracted`, `draft`, `approved`, or `rejected`;
- `generalizability`: `none`, `low`, `medium`, or `high`;
- phase, platform, service, technology, and access metadata when known;
- extractor, parser, prompt, and model versions;
- creation and update timestamps.

An inferred field must be identifiable as inferred. Extraction must never present an unstated rationale as an explicit author claim.

## Canonical Models

### DocumentManifest

The manifest records source identity and processing state:

- stable source ID based on canonical source path;
- content hash and asset hashes;
- title, language, and document type;
- quality classification with reason codes;
- parser profile;
- extraction status;
- schema, parser, prompt, and extractor versions;
- emitted artifact IDs;
- warnings and quarantine reasons.

### ReferenceArtifact

A reference artifact contains:

- concise statement and artifact subtype;
- applicable situation and prerequisites;
- action intent when the reference recommends a check;
- expected evidence;
- success, failure, and stop implications;
- exceptions and warnings;
- Hades capability references;
- version or observation date when known;
- precise source references.

### Case

A case contains:

- environment metadata such as platform, OS, difficulty, and starting access;
- ordered steps;
- overall outcome without final flags;
- source quality and review status;
- case-wide transferable and non-transferable properties.

### CaseStep

Each step contains:

- ordinal position;
- relevant state before the step;
- observations with evidence references;
- hypotheses considered, each marked explicit or inferred;
- selected action intent and optional Hades capability reference;
- evidence or output category;
- result and state after the step;
- failed alternatives or negative evidence when present;
- transfer conditions;
- case-specific details excluded from strategic indexing;
- precise source references.

The raw command may remain in the original source and a review excerpt, but Sedna indexes the action intent rather than duplicating tool instructions.

### DecisionRule

A decision rule contains:

- trigger observations;
- prerequisites;
- rationale;
- action intent;
- expected evidence;
- success and failure transitions;
- stop conditions;
- exceptions and alternative hypotheses;
- Hades capability references;
- supporting and contradicting sources;
- review status.

A rule derived from a walkthrough starts as a draft. It becomes approved only after human review or consistent support from multiple independent sources followed by review.

## Parsing and Extraction Pipeline

### 1. Inventory

Discover sources and assets, calculate hashes, assign stable IDs, and compare them with existing manifests. Unchanged sources are skipped unless schema, parser, prompt, or extractor versions require reprocessing.

### 2. Classification

Classify each document by type and quality. Deterministic signals include path, file size, heading structure, code blocks, flag patterns, external links, and source family. Semantic classification resolves ambiguous cases. Empty, flag-only, and external-link-only files produce manifests but no decision artifacts.

### 3. Structural parsing

Use a CommonMark-compatible structural parser and source-specific cleanup adapters. Preserve headings, paragraphs, ordered and unordered lists, tables, code blocks, links, images, and source spans.

Parser profiles are:

- HTB scrape cleanup: remove navigation, questions, billing, Pwnbox controls, footer, and modal text;
- Academy/Obsidian: preserve wiki links, cheatsheet tables, and local assets;
- GitHub walkthrough: preserve chronological headings, code blocks, outputs, links, and screenshots;
- stub/excluded: emit classification metadata only.

### 4. Logical segmentation

Segment by semantic and structural boundaries, not a fixed token window. A segment should keep an observation, action, output, and conclusion together whenever the source supports that relationship. Long sections may contain several logical segments; short adjacent sections may belong to one step.

### 5. Semantic extraction

Reference sources produce concepts, methodologies, constraints, warnings, and candidate decision rules. Walkthroughs produce one case with ordered case steps, including failed attempts.

The extractor may infer a hypothesis needed to connect an observation to an action, but it must mark the hypothesis as inferred and provide the source material from which it was inferred.

### 6. Normalization

Normalize phases, access levels, platforms, services, technologies, action intents, and capability references. Normalize accidental values:

- target IPs to `TARGET_IP`;
- attacker IPs to `ATTACKER_IP`;
- callbacks to semantic callback placeholders;
- hostnames to base or discovered-host roles when their literal value is not methodologically significant;
- target-specific credentials to credential classes;
- final flags to excluded secret markers.

Ports and versions remain when they materially identify a service or constraint.

### 7. Validation

Validate Pydantic schemas and provenance. Every explicit claim requires a source reference. Every inferred claim must be marked. Reject final flags from canonical strategic artifacts and the retrieval index. Preserve contradictions instead of overwriting one source with another.

### 8. Deduplication and rule synthesis

Detect exact duplicates by content hash and near duplicates by normalized structure. Multiple copied walkthroughs do not count as independent support. Case steps may propose draft rules, but no rule is automatically approved.

### 9. Emission and indexing

Write canonical artifacts atomically, produce an ingestion report, and update the disposable index only after validation succeeds.

## Retrieval Index

The initial index uses SQLite FTS5 plus structured columns. It stores only materialized retrieval fields, the artifact ID, and the canonical file path. After ranking a hit, Sedna loads the full record from its canonical JSON or YAML file. Every row points back to its canonical file and source references, so the index cannot become an independent copy of record truth.

Structured filters include:

- knowledge role and artifact type;
- phase;
- service and technology;
- platform;
- access before and after;
- observation and action-intent tags;
- generalizability and review status.

Retrieval first applies state-derived filters, then BM25/FTS ranking within separate reference, case, and negative-case lanes. It does not compare a reference score directly with a case score.

Embedding search is deferred. It is introduced only if golden retrieval tests show that lexical search and normalized tags fail to retrieve semantically analogous cases expressed with different terminology.

## Web Research Fallback

When local retrieval leaves a material knowledge gap, the agent may perform technical web research using:

- product and version;
- protocol or technology;
- observed error or behavior;
- configuration;
- CVE or advisory identifiers.

Queries must not combine the exact machine name with terms such as `writeup`, `solution`, or `flag`, and must not search for flag values. Primary documentation, vendor advisories, standards, and original research are preferred.

Web findings remain session evidence with URL, retrieval date, and relevant version. They are promoted into canonical knowledge only through the same validation and review process as local sources.

## Failure Handling

- Ambiguous classification creates a manifest and quarantine record without indexing decision artifacts.
- Malformed source structure produces a partial report and does not stop the ingestion run.
- Invalid records are quarantined with schema and provenance errors.
- Missing source spans remove the unsupported field or force an explicit `inferred` designation.
- Contradictory sources remain linked as supporting and contradicting evidence.
- A changed source triggers reprocessing only for that source and draft rules that depend on it.
- Schema and extractor version changes are recorded and can trigger controlled re-extraction.

## Testing Strategy

### Golden corpus

Create a manually reviewed set of approximately 15 documents containing:

- narrative Academy lessons;
- cheatsheets;
- scraped HTB pages with interface boilerplate;
- complete machine walkthroughs;
- walkthroughs with failed attempts;
- external-link stubs;
- empty files;
- flag-only challenges.

### Test layers

1. Structural parser tests for headings, tables, code blocks, images, and source spans.
2. Classifier tests against the manually assigned document type and quality.
3. Schema and provenance validation tests.
4. Normalization tests for IPs, callbacks, hostnames, credentials, and flags.
5. Idempotency tests for unchanged sources.
6. Incremental reprocessing tests for content and version changes.
7. Retrieval scenarios with expected references, analogous cases, and counterexamples.
8. Anti-leakage tests proving that flags do not enter canonical strategic artifacts or the index.

### Acceptance criteria

- Every accepted artifact has valid provenance.
- Every inferred hypothesis is labeled.
- No final flag appears in canonical strategic artifacts or the index.
- Re-ingesting unchanged input produces identical canonical artifacts.
- Excluded and ambiguous files produce explainable manifest statuses.
- Golden retrieval scenarios return the expected epistemic lanes.
- The index can be deleted and rebuilt without loss of canonical knowledge.

## Rollout

1. Build and manually validate the golden corpus.
2. Implement manifests, canonical models, parser profiles, and validation.
3. Extract references and cases for the golden corpus.
4. Build and test the derived FTS5 index.
5. Process the remaining current corpus incrementally.
6. Add automated ingestion for newly added writeups.
7. Evaluate retrieval failures before considering embeddings or a knowledge graph.

## Final Design Decisions

- Use the two-axis source/artifact taxonomy.
- Keep references and case studies in separate epistemic lanes.
- Preserve raw Markdown, PDFs, and assets unchanged.
- Store generated records as JSON/JSONL and reviewed rules as YAML.
- Treat SQLite only as a rebuildable retrieval projection.
- Keep Hades tool skills and Sedna strategic knowledge separate.
- Allow technical web research for knowledge gaps while blocking flag and direct-solution searches.
- Start with a reviewed golden corpus and lexical retrieval before adding semantic infrastructure.
