# Claude-Red Integration Assessment

Date: 2026-08-06

## Decision

Integrate Claude-Red as a pinned, untrusted third-party knowledge source for Sedna. Do not install it wholesale as Hades or Codex skills.

Sedna should use selected Claude-Red documents as technical references and methodology inputs. Hades remains the authority for tool selection, command syntax, execution, and evidence normalization. Claude-specific agent instructions and trigger phrases are source metadata, not executable instructions.

This preserves the project boundary:

- Sedna answers what to investigate, why, which evidence matters, and when to stop or change hypothesis.
- Hades answers how to operate the relevant tool safely and return normalized evidence.
- Claude-Red is an attributed external reference, never an authority that overrides either system.

## Upstream snapshot evaluated

- Repository: `SnailSploit/Claude-Red`
- License: MIT
- Default branch: `main`
- Evaluated commit: `aeb41eca7088a703c3a35fbcba3086d4a6c1aa4e`
- Corpus at that commit: 58 `SKILL.md` files in 13 categories, about 3 MB and 82,000 lines
- Format split: 26 documents begin with YAML frontmatter; 32 use an older Markdown metadata format with an `Instructions for Claude` section
- Four documents exceed 10,000 lines; the largest is about 567 KB

The repository is useful but not a clean drop-in dependency. Its content mixes strategic methodology, technical reference, detailed tool recipes, payloads, agent instructions, and high-risk operational material. Some documents are large enough to be poor direct skill/context units. The included converter also assumes a lowercase `skills/` directory while the repository uses `Skills/`, so it is not a suitable ingestion component.

## Why direct skill installation is the wrong integration

Installing the repository directly would create three problems:

1. Ownership would overlap with the individual tool skills already being developed in Hades.
2. Claude-specific instructions and triggers would enter the agent instruction plane instead of remaining reviewable source data.
3. Large tactical documents would consume excessive context and encourage replay of recipes instead of evidence-driven planning.

Even the 26 frontmatter-based files that are structurally closer to a Codex skill should not be activated automatically. Structural compatibility does not establish the correct epistemic role or safety boundary.

## Proposed source model

Add a third-party source adapter with a lock entry such as:

```yaml
source_id: third_party.claude_red
repository: https://github.com/SnailSploit/Claude-Red.git
commit: aeb41eca7088a703c3a35fbcba3086d4a6c1aa4e
license: MIT
content_root: Skills
trust: untrusted_reference
```

The adapter should support both upstream formats:

- modern YAML frontmatter followed by Markdown;
- legacy `Metadata`, `Description`, `Trigger Phrases`, and `Instructions for Claude` sections.

It should assign stable source IDs from the upstream namespace and relative path, while content hashes and the pinned commit capture revisions. An update operation must report added, changed, removed, and unchanged documents before any canonical artifacts are refreshed.

## Retrieval and safety boundary

Treat every upstream body as data-only. Do not evaluate instructions, follow embedded links automatically, register upstream triggers, or allow an upstream document to alter extraction policy.

The adapter should divide content into these lanes:

| Source content | Destination | Treatment |
|---|---|---|
| Concepts, constraints, evidence expectations | Sedna reference | Searchable after provenance and flag/secret sanitization |
| Decision points, alternatives, stop conditions | Sedna methodology or draft decision rule | Auto-extracted or draft; never auto-approved |
| Historical examples | Sedna case fragment | Case-specific, low generalizability by default |
| Tool commands, payloads, syntax | Hades capability-gap report | Not copied into Sedna strategic artifacts |
| Claude instructions and trigger phrases | Source metadata only | Non-searchable and never executable |
| High-risk operational material | Quarantine or explicit allowlist | Requires scope and human review |

Direct final flags, credentials, target-specific literals, prompt-injection text, and untrusted asset metadata must never enter searchable segments. Provenance should retain the source path, upstream commit, line span, content hash, license, and review state.

## Taxonomy change

Add `technical_reference` as a document type rather than forcing all imported documents into `lesson` or `cheatsheet_reference`. Its default knowledge role is `reference`, its default review state is `auto_extracted`, and it cannot emit an approved decision rule without independent support and review.

Use a `third_party_skill` parser profile. The word `skill` describes the upstream packaging format only; it does not grant the source skill authority inside Sedna, Hades, or Codex.

## Hades capability mapping

For each accepted source, produce a capability-gap record rather than importing operational recipes:

```json
{
  "source_id": "third_party.claude_red.web.offensive-sqli",
  "action_intents": ["test_parameter_influence", "differentiate_boolean_response"],
  "candidate_capability_refs": ["hades.skill.web.sqli_assessment"],
  "mapping_status": "mapped_or_gap",
  "missing_capabilities": []
}
```

Mappings require review. A Claude-Red tool mention is evidence that a capability may be useful, not proof that the corresponding Hades skill is correct, installed, or safe for the current target.

## Quality gates

An imported document is eligible for retrieval only when all applicable gates pass:

- repository and commit are pinned;
- license and attribution are recorded;
- source hash and parser/extractor versions are stored;
- agent-instruction sections are excluded from the instruction plane;
- final flags and secrets are absent from all searchable fields, including nested asset metadata;
- oversized documents are segmented structurally and remain under configured retrieval limits;
- reference claims retain source spans and review status;
- tactical content is separated from strategic artifacts;
- updates produce a reviewable diff and never silently track `main`;
- source removal does not delete reviewed canonical artifacts without an explicit migration decision.

External URLs, CVE claims, and product behavior should be treated as potentially stale. They may orient later research, but current technical decisions should be verified against primary sources when accuracy matters.

## Phased implementation

### Phase 1: allowlisted pilot

Import five representative, moderate-sized documents:

- `offensive-fast-checking` for prioritization methodology;
- `offensive-sqli`, `offensive-idor`, and `offensive-ssrf` as web technical references;
- `offensive-osint-methodology` as a reconnaissance reference.

The pilot validates both upstream formats, provenance, content-lane separation, capability mapping, deterministic updates, and retrieval behavior. Do not begin with the very large Windows or exploit-development monoliths.

### Phase 2: review and retrieval evaluation

Compare responses with and without the imported references. A successful integration should improve evidence selection and alternative generation without increasing command-level duplication, target-specific replay, or reliance on a single source.

Review capability gaps with the Hades skill inventory. Keep only strategic material that is complementary to Hades.

### Phase 3: category expansion

Expand by explicit category allowlists. High-risk categories such as initial access, EDR evasion, shellcode, credential capture, and persistence remain opt-in and quarantined until their intended authorized use and Hades boundary are reviewed.

### Phase 4: controlled refresh

Provide an explicit refresh command that fetches metadata, presents the upstream commit diff, runs deterministic ingestion in a temporary canonical repository, and requires acceptance before changing the pinned commit.

## Acceptance criteria

The Claude-Red adapter is ready when:

1. a clean checkout at the pinned commit produces identical manifests and segments on two runs;
2. both modern and legacy formats parse without executing their instructions;
3. all imported records have upstream provenance and MIT attribution;
4. no raw Claude trigger or instruction becomes an active Sedna/Hades/Codex instruction;
5. no searchable field contains a final flag or source secret;
6. tool recipes are absent from strategic artifacts and represented only as capability references or gaps;
7. changed, removed, and newly added upstream files are reported deterministically;
8. the five-document pilot improves retrieval tests without case replay or Hades duplication.

## Conclusion

Claude-Red is valuable as a curated external bibliography and methodology corpus, not as an agent personality or a replacement tool layer. The integration should therefore follow the same principle as the existing writeups: references orient, examples inspire, and the current evidence remains authoritative.
