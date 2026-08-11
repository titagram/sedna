# Sedna adversarial roleplay skill — design note

Status: idea captured for later skill design. This is not yet an implementation specification.

## Purpose

Create a Codex skill for evaluating and improving the combined Hades + Sedna decision loop without operating against a live target or invoking real tools such as Nmap, curl, Metasploit, or SSH.

The user supplies the walkthrough of an already solved, authorized machine. Codex acts as the game master; Hades with Sedna acts as the player. The walkthrough remains hidden ground truth. Hades must discover the route progressively by consulting Sedna, selecting strategies, and reacting to simulated observations.

The skill is intended to reveal whether Sedna behaves like a useful mentor rather than merely replaying a known solution, and to guide transferable calibration of prompts, planning rules, retrieval, and journal behavior.

## Core roleplay model

- **Game master — Codex:** reads the complete walkthrough, constructs the hidden scenario state, simulates the environment, returns evidence consistent with requested actions, and evaluates behavior.
- **Player — Hades/Hermes:** interprets the task, starts or resumes a Sedna engagement, asks Sedna for strategic guidance, validates suggested commands using its `/learn` tool knowledge, and chooses actions.
- **Mentor — Sedna:** retrieves relevant experience, maintains the journal, produces and reassesses a weighted frontier, and records why priorities changed.
- **Ground truth — supplied walkthrough:** determines real services, findings, prerequisites, successful paths, dead ends, flags, and proof of completion. It is never exposed wholesale to the player.

## Required isolation

The evaluation is invalid if Hades or Sedna can retrieve the supplied walkthrough, the machine solution, or its flags.

- Do not ingest the target walkthrough into the knowledge store visible to the player.
- Build a disposable filtered knowledge root for the run. Exclude the supplied walkthrough and
  every already-ingested copy matched by source hash, source identity, machine aliases, canonical
  relationships, or retrieval independence group; then rebuild the disposable index.
- Do not include the machine name in web searches or retrieval queries that could reveal its solution.
- Replace operational tool and web-search surfaces with a simulator adapter. Real network,
  reconnaissance, exploitation, shell, and web-search calls must be technically unavailable, not
  merely prohibited by the prompt.
- Use a fresh engagement and isolated test state for every run.
- Give the player a synthetic machine name, target address, domain, usernames, and flag values.
  Preserve strategically relevant behavior while hiding the real machine identity; optionally
  perturb nonessential banners that would uniquely identify a famous scenario.
- Give the player only the initial briefing, authorized scope, target metadata intentionally disclosed by the scenario, and simulated results earned through its actions.
- Keep flags available to the game master so flag detection and closure can be verified.
- Treat any unearned identification of the original machine or solution-only fact as possible
  parametric-memory leakage and invalidate or separately label that run.

## Interaction loop

1. Parse the walkthrough into a private scenario graph containing starting facts, discoverable observations, prerequisites, successful transitions, alternate paths, dead ends, and completion proofs.
2. Give Hades only a re-skinned initial task, for example: `This is Lab-Aster and its target is
   192.0.2.42; proceed.` The original platform and machine name remain game-master-only metadata.
3. Require Hades to initialize or resume a named Sedna engagement and request a plan.
4. Let Hades select a strategy and state the concrete command or operation it would perform.
5. Simulate the tool result from the private scenario. When the walkthrough and environment model
   do not establish the result, return a typed `scenario_unknown`; do not invent a negative result
   or score the player's creative path as a Sedna failure.
6. Feed the simulated result through the same journal and outcome-processing boundaries used by the real plugin.
7. Continue until the objective is reached, the player explicitly stops, a bounded turn budget is exhausted, or a reproducible loop is detected.
8. Generate an adversarial evaluation report and classify every material failure before proposing changes.

## Adversarial variations

The skill should be able to replay the same machine with controlled variations rather than overfit to one transcript:

- misleading or incomplete service banners;
- timeouts and tool execution errors that must not penalize the strategy itself;
- a valid action that produces no useful result;
- partial enumeration followed by a more complete retry;
- missing prerequisites that become available later;
- a tempting but low-value branch;
- architecture or operating-system incompatibility;
- a false or decoy flag followed by engagement reopening;
- an alternative valid route not used by the supplied walkthrough;
- a technical knowledge gap that should trigger generic web research without searching for the machine solution.

## Evaluation dimensions

At minimum, evaluate whether Hades and Sedna:

- initialize and maintain the engagement correctly;
- consult Sedna before material strategic decisions;
- begin with proportionate information gathering;
- retrieve applicable experience without treating case studies as immutable rules;
- keep strategies creative when the walkthrough path is not the only plausible route;
- distinguish strategic failure, negative evidence, incompatibility, ambiguity, and tool error;
- lower a failed strategy without erasing it when retry conditions remain plausible;
- avoid repeating the same deterministic action in an unchanged state;
- recognize when new evidence should reactivate an earlier path;
- keep tool syntax ownership with Hades `/learn` while allowing Sedna to offer concrete command suggestions;
- cite journal events and knowledge sources when changing frontier scores;
- respect authorization and avoid solution/flag searches;
- capture the expected flag and close or reopen the engagement correctly;
- produce a coherent, evidence-backed operational report;
- create a sanitized case candidate that does not leak flags or runtime secrets.

## Diagnosis before tuning

Do not label every poor decision as a Sedna ranking defect. Classify the root cause first:

- missing or incorrectly ingested knowledge;
- retrieval miss or applicability mismatch;
- planner prompt or frontier-quality failure;
- critic failure;
- journal observation extraction failure;
- outcome interpretation or score-reassessment failure;
- Hades failure to invoke Sedna;
- Hades `/learn` tool-knowledge failure;
- simulator ambiguity or insufficient walkthrough evidence;
- integration, persistence, or session-resume failure.

## Calibration policy

In this context, `fine tuning` initially means evidence-driven calibration of Sedna prompts, schemas, retrieval instructions, journal reduction, and Hades integration. It does not imply training model weights unless a later, separately approved project establishes a dataset and evaluation protocol.

- Prefer the smallest transferable correction.
- Never add machine-name-specific rules or replay the walkthrough verbatim.
- Preserve the distinction between engagement-local adaptation and global knowledge.
- Validate a proposed correction on the original scenario and on unrelated control scenarios.
- Reject changes that improve one walkthrough while degrading general behavior.
- Record before/after transcripts, metrics, and the exact reason for every accepted change.
- Use a fresh roleplay run after each material revision so the player cannot reuse leaked context.
- Count `scenario_unknown` as scenario-coverage debt rather than a bad player decision. Expand the
  environment model or add a controlled variation before evaluating that path.

## Expected artifacts

Each run should produce:

- initial scenario briefing;
- hidden scenario manifest for the game master;
- complete simulated interaction transcript;
- Sedna journal and frontier revisions;
- decision-by-decision evaluation;
- loop and dead-end analysis;
- success/flag verification result;
- categorized failure report;
- proposed calibration changes;
- before/after comparison for accepted changes;
- reusable regression cases that contain no target solution leakage.

## Relationship to the Sedna roadmap

This skill depends on the Event Journal and Adaptive Planner because it must exercise the same engagement, planning, outcome, and reporting boundaries used in production. It should therefore be designed as a separate Codex skill after those interfaces stabilize.

A minimal first version should support one supplied walkthrough, one isolated Hades + Sedna player session, deterministic simulated tool responses, bounded turns, and an evaluation report. Later versions may add scenario mutations, repeated trials, automated regression matrices, and statistical comparison across planner or prompt versions.

## Tentative skill shape

Potential skill name: `sedna-adversarial-roleplay`.

When implemented, keep `SKILL.md` concise and place detailed schemas, scoring rubrics, simulator rules, and report templates in one-level `references/` or `assets/` resources. Forward-test it with fresh agents that receive the raw scenario artifacts but not the expected diagnosis or proposed fix.
