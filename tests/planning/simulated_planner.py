"""Reusable no-I/O acceptance driver over Sedna's typed planning boundaries."""

from __future__ import annotations

import json
import re
from base64 import urlsafe_b64decode
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sedna.engagement import (
    EngagementJournalService,
    EngagementManifest,
    ExecutionLaneKey,
    HostIdentity,
    HostKind,
    ProofRequirement,
)
from sedna.engagement.events import (
    EvidenceAttachedPayload,
    ToolCallCompletedPayload,
)
from sedna.knowledge.retrieval import AuthorizationScope, AuthorizationState, ValidatedTarget
from sedna.knowledge.schema.common import ExtractionMetadata, SourceLocation, SourceRef
from sedna.knowledge.schema.execution import (
    ExecutionExample,
    ExecutionPlatformConstraint,
    PlaceholderKind,
)
from sedna.planning import PlanningService, SituationProjection, TerminalReconciliationResult
from sedna.planning.commands import (
    CommandBinding,
    CommandOrigin,
    CommandSuggestionDraft,
)
from sedna.planning.llm import PlanningLlmAdapter
from sedna.planning.models import (
    FacetObservationDraft,
    FrontierProposalDraft,
    InterpretationSubject,
    ObjectiveProofDraft,
    ObservationBatchDraft,
    ObservationDraft,
    OutcomeAssessmentDraft,
    OutcomeCategory,
    PlannerCriticVerdict,
    PlannerDraft,
    RetryPredicate,
    RetryPredicateKind,
    StrategyStatus,
)
from sedna.planning.retrieval import _example_applies
from sedna.planning.utility import rank_utilities, utility_input_for_proposal
from tests.engagement.simulated_hades import SimulatedHades


@dataclass(frozen=True)
class AdaptiveResult:
    strategy_ids: tuple[str, ...]
    selected_strategy_id: str
    reactivated_strategy_id: str
    outcome_kinds: tuple[str, ...]
    proof_requirement_ids: tuple[str, ...]
    terminal_calls: tuple[SituationProjection, ...]
    terminal_situation: SituationProjection
    terminal_loop_prevented: bool
    outcomes_have_terminal_completion: bool
    persisted_command_template: str
    host_adapted_command_template: str
    operational_tool_calls: tuple[str, ...]
    commands_are_placeholder_bound: bool
    source_credentials_are_unbound: bool
    research_references_are_exact: bool
    archive_references_are_exact: bool
    real_plugin_handlers_called: tuple[str, ...]
    persisted_event_types: tuple[str, ...]
    planning_service_calls: int


@dataclass(frozen=True)
class AdversarialResult:
    rejected_queries: tuple[str, ...]
    typed_gaps: tuple[str, ...]
    recovery_after_unplanned_action: bool
    false_flag_rejected: bool
    hostile_instructions_inert: bool
    request_sizes_bounded: bool
    operational_tool_calls: tuple[str, ...]
    consulted_source_ids: tuple[str, ...]
    hostile_evidence_count: int
    false_flag_evidence_observed: bool
    recovery_steps: tuple[str, ...]
    typed_research_event_types: tuple[str, ...]
    applicability_decisions: dict[str, str]


class _ScriptedPlanningHost:
    def __init__(self, scenario: dict[str, Any], *, fail_first_plan: bool = False) -> None:
        self._scenario = scenario
        self._fail_first_plan = fail_first_plan
        self.planning_calls = 0
        self.scope_reference_id = ""
        self.grounding_event_id: UUID | None = None

    def complete_structured(self, **kwargs: Any) -> object:
        purpose = kwargs["purpose"]
        if purpose == "sedna.planning.observe":
            request = json.loads(kwargs["input"][0]["text"])
            evidence = request["evidence_slices"][0]
            content = urlsafe_b64decode(evidence["content"]).decode("utf-8")
            attachment_event_id = UUID(evidence["event_id"])
            terminal_match = re.search(
                r"terminal_tool_event_id=([0-9a-f-]{36}|None)", kwargs["instructions"]
            )
            terminal_event_id = (
                UUID(terminal_match.group(1))
                if terminal_match is not None and terminal_match.group(1) != "None"
                else None
            )
            outcomes = ()
            observations = ()
            facets = ()
            objective_proofs = ()
            try:
                observed = json.loads(content)
            except json.JSONDecodeError:
                observed = None
            if isinstance(observed, dict) and "outcome" in observed:
                kind = observed["outcome"]
                category = {
                    "syntax_error": OutcomeCategory.EXECUTION_ERROR,
                    "syntax_corrected": OutcomeCategory.PROGRESS,
                    "credentials_rejected": OutcomeCategory.NEGATIVE_EVIDENCE,
                    "bounded_wordlist_exhausted": OutcomeCategory.NO_EFFECT,
                }[kind]
                outcomes = (
                    OutcomeAssessmentDraft(
                        category=category,
                        summary=kind,
                        event_ids=(attachment_event_id,),
                    ),
                )
            elif content == "credential_reference_discovered":
                observations = (
                    ObservationDraft(
                        kind="text",
                        text="credential-reference-discovered",
                        event_ids=(attachment_event_id,),
                    ),
                )
            elif content.startswith("Ignore the planner"):
                facets = (
                    FacetObservationDraft(
                        key="os_family",
                        value="linux",
                        event_ids=(attachment_event_id,),
                    ),
                )
            elif content.startswith("verified evidence for "):
                objective_proofs = (
                    ObjectiveProofDraft(
                        proof_requirement_id=content.removeprefix("verified evidence for "),
                        assessment="supported",
                        event_ids=(attachment_event_id,),
                    ),
                )
            parsed: object = ObservationBatchDraft(
                subject=InterpretationSubject(
                    attachment_event_id=attachment_event_id,
                    terminal_tool_event_id=(
                        terminal_event_id if outcomes or objective_proofs else None
                    ),
                    evidence_id=evidence["evidence_id"],
                ),
                observations=observations,
                facets=facets,
                outcomes=outcomes,
                objective_proofs=objective_proofs,
            )
        elif purpose == "sedna.planning.plan":
            self.planning_calls += 1
            if self._fail_first_plan and self.planning_calls == 1:
                raise RuntimeError("simulated llm outage")
            observed_services = {
                event["service"]
                for event in self._scenario.get("events", ())
                if event["kind"] == "service_observed"
            }
            strategy_ids = tuple(
                strategy_id
                for strategy_id in self._scenario.get(
                    "expected_strategy_ids",
                    ("safe-research", "manual-validation", "pause-and-review"),
                )
                if "expected_strategy_ids" not in self._scenario
                or any(f"-{service}-" in strategy_id for service in observed_services)
            )
            reactivation = next(
                (
                    event
                    for event in self._scenario.get("events", ())
                    if event["kind"] == "strategy_reactivated"
                ),
                None,
            )
            retry_strategy_id = reactivation["strategy_id"] if reactivation is not None else None
            command_event = next(
                (
                    event
                    for event in self._scenario.get("events", ())
                    if event["kind"] == "source_command_suggested"
                ),
                None,
            )
            proposals = []
            for index, strategy_id in enumerate(strategy_ids):
                is_retry_strategy = strategy_id == retry_strategy_id
                status = (
                    StrategyStatus.BLOCKED
                    if is_retry_strategy and self.planning_calls == 1
                    else StrategyStatus.AVAILABLE
                )
                command = None
                retry_predicates = ()
                if (
                    is_retry_strategy
                    and command_event is not None
                    and command_event["strategy_id"] == strategy_id
                ):
                    assert self.grounding_event_id is not None
                    command = CommandSuggestionDraft(
                        origin=CommandOrigin.MODEL_GENERATED,
                        command_template=command_event["command_template"],
                        placeholder_kinds=(
                            PlaceholderKind.SOURCE_CASE_CREDENTIAL,
                            PlaceholderKind.TARGET,
                        ),
                        bindings=(
                            CommandBinding(
                                placeholder_name="source_case_username",
                                source=command_event["credential_binding"],
                            ),
                            CommandBinding(
                                placeholder_name="target",
                                source="scope_reference",
                                reference_id=self.scope_reference_id,
                            ),
                        ),
                        capability_hint=command_event["source_id"],
                    )
                    if self.planning_calls == 1:
                        command = None
                        retry_predicates = (
                            RetryPredicate(
                                kind=RetryPredicateKind.FACT_PRESENT,
                                value="credential-reference-discovered",
                            ),
                        )
                proposals.append(
                    FrontierProposalDraft(
                        family_runtime_key=f"family:{strategy_id}",
                        variant_runtime_key=f"variant:{strategy_id}",
                        title=strategy_id,
                        score=90 - index,
                        confidence=80,
                        rationale="Bounded acceptance strategy from typed planner output.",
                        status=status,
                        retry_predicates=retry_predicates,
                        event_refs=(self.grounding_event_id,)
                        if status is StrategyStatus.BLOCKED
                        else (),
                        commands=(command,) if command is not None else (),
                    )
                )
            proposal_by_key = {item.variant_runtime_key: item for item in proposals}
            ranked_keys = rank_utilities(
                tuple(
                    (item.variant_runtime_key, utility_input_for_proposal(item))
                    for item in proposals
                )
            )
            parsed = PlannerDraft(
                proposals=tuple(proposal_by_key[key] for key in ranked_keys),
                research_queries=tuple(
                    self._scenario.get(
                        "planning_research_queries",
                        self._scenario.get("queries", ("Linux SSH authentication failure modes",)),
                    )
                ),
            )
        elif purpose == "sedna.planning.critic":
            parsed = PlannerCriticVerdict(accepted=True)
        else:
            raise AssertionError(f"unexpected planning purpose: {purpose}")
        return SimpleNamespace(
            parsed=parsed,
            provider="simulated",
            model="simulated",
            agent_id="simulated-planner",
            usage={"input_tokens": 1, "output_tokens": 1},
        )


class _RecordingTerminalSettlementPort:
    def __init__(self, root: Path, lane: Any) -> None:
        self._root = root
        self._lane = lane
        self.calls: list[SituationProjection] = []

    def reconcile(
        self,
        *,
        engagement_id: UUID,
        situation: SituationProjection,
        requirement_ids: tuple[str, ...],
        authoritative_revision: Any,
        reason: str,
        all_required_proofs_satisfied: bool,
    ) -> TerminalReconciliationResult:
        del requirement_ids, reason
        self.calls.append(situation)
        with EngagementJournalService.open(self._root) as journal:
            snapshot = journal.load_snapshot(engagement_id)
            assert snapshot.revision == authoritative_revision
            if all_required_proofs_satisfied:
                closed = journal.request_close(
                    engagement_id,
                    lane=self._lane,
                    reason="All required objective proofs were observed.",
                    expected_revision=snapshot.revision,
                ).snapshot
                return TerminalReconciliationResult(
                    action="proof_close_requested",
                    authoritative_journal_revision=closed.revision,
                    lifecycle_status=closed.state.status,
                )
            return TerminalReconciliationResult(
                action="unchanged",
                authoritative_journal_revision=snapshot.revision,
                lifecycle_status=snapshot.state.status,
            )


def _json_result(value: object) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else value  # type: ignore[return-value]


def _evidence_attachment(snapshot: Any, call_id: str) -> tuple[UUID, EvidenceAttachedPayload]:
    host_call_digest = sha256(call_id.encode()).hexdigest()
    completion = next(
        event
        for event in snapshot.events
        if isinstance(event.payload, ToolCallCompletedPayload)
        and event.payload.correlation.host_tool_call_id_sha256 == host_call_digest
    )
    event = max(
        (
            event
            for event in snapshot.events
            if event.sequence < completion.sequence
            and isinstance(event.payload, EvidenceAttachedPayload)
        ),
        key=lambda item: item.sequence,
    )
    return event.event_id, event.payload


def _tool_completion(snapshot: Any, call_id: str) -> Any:
    host_call_digest = sha256(call_id.encode()).hexdigest()
    return next(
        event
        for event in snapshot.events
        if isinstance(event.payload, ToolCallCompletedPayload)
        and event.payload.correlation.host_tool_call_id_sha256 == host_call_digest
    )


class SimulatedPlanner:
    """Exercise real validators/services while fixtures supply only scenario inputs."""

    def __init__(self, root: Path, scenario: dict[str, Any]) -> None:
        self._root = root / "knowledge"
        self._scenario = scenario
        self._operational_tool_calls: list[str] = []

    def run(self) -> AdaptiveResult:
        manifest = self._manifest()
        host = SimulatedHades(self._root)
        scripted = _ScriptedPlanningHost(self._scenario)
        host._context.complete_structured = scripted.complete_structured
        engagement_id = host.create(
            name=manifest.display_name,
            objective=manifest.initial_objective,
            target=self._scenario["engagement"]["target"],
            required_proofs=tuple(self._scenario["engagement"]["proof_requirements"]),
            session_id="acceptance",
        )
        with EngagementJournalService.open(self._root) as journal:
            initial = journal.load_snapshot(engagement_id)
            scripted.scope_reference_id = initial.state.scope_references[0].reference_id
            scripted.grounding_event_id = initial.events[0].event_id
            lane = ExecutionLaneKey(
                host_kind=HostKind.HADES,
                session_id="acceptance",
                task_id="root",
            )
        terminal_port = _RecordingTerminalSettlementPort(self._root, lane)
        host.decide("Gather bounded acceptance evidence", session_id="acceptance")

        plan_handler = host._tools["sedna_plan_next"]["handler"]
        first = _json_result(
            plan_handler({"max_proposals": 3}, session_id="acceptance", task_id="root")
        )
        assert first.get("status") == "success", first

        credential_event = next(
            event
            for event in self._scenario["events"]
            if event["kind"] == "credential_reference_discovered"
        )
        host.tool(
            "terminal",
            {"command": "fixture-only credential evidence"},
            result=credential_event["kind"],
            session_id="acceptance",
            tool_call_id="credential-evidence",
        )

        second = _json_result(
            plan_handler({"max_proposals": 3}, session_id="acceptance", task_id="root")
        )
        assert second.get("status") == "success", second
        selected_event = next(
            event for event in self._scenario["events"] if event["kind"] == "strategy_selected"
        )
        selected = next(
            proposal
            for proposal in second["frontier"]["proposals"]
            if proposal["title"] == selected_event["strategy_id"]
        )
        adapted_event = next(
            event for event in self._scenario["events"] if event["kind"] == "host_command_adapted"
        )
        record_handler = host._tools["sedna_record_decision"]["handler"]
        decision = _json_result(
            record_handler(
                custom_strategy=selected["title"],
                rationale="Current evidence reactivates this bounded strategy.",
                host_adapted_command={
                    "command_template": adapted_event["command_template"],
                    "placeholder_names": ["username", "target"],
                    "adaptation_note": "Host retained symbolic current-scope bindings.",
                },
                session_id="acceptance",
                task_id="root",
            )
        )
        assert decision.get("ok") is True, decision

        expected_outcomes = tuple(
            event["kind"]
            for event in self._scenario["events"]
            if event["kind"]
            in {
                "syntax_error",
                "syntax_corrected",
                "credentials_rejected",
                "bounded_wordlist_exhausted",
            }
        )
        outcome_settlement: dict[str, Any] = {}
        for index, outcome in enumerate(expected_outcomes):
            host.tool(
                "terminal",
                {"command": f"fixture-only bounded strategy attempt {index}"},
                result=json.dumps({"outcome": outcome}),
                session_id="acceptance",
                tool_call_id=f"strategy-outcome-{index}",
            )
            outcome_settlement = _json_result(
                plan_handler({"max_proposals": 3}, session_id="acceptance", task_id="root")
            )
            assert outcome_settlement.get("status") == "success", outcome_settlement

        for proof_id in self._scenario["engagement"]["proof_requirements"]:
            host.tool(
                "terminal",
                {"command": f"fixture-only proof evidence {proof_id}"},
                result=f"verified evidence for {proof_id}",
                session_id="acceptance",
                tool_call_id=f"proof-{proof_id}",
            )
        with EngagementJournalService.open(self._root) as journal:
            proof_settlement = PlanningService(
                journal=journal,
                llm=PlanningLlmAdapter(cast(Any, host._context)),
                clock=lambda: datetime.now(UTC),
                terminal_settlement_port=terminal_port,
            ).settle_pending_evidence(engagement_id, reason="plan")
        assert proof_settlement.status == "settled", proof_settlement
        planning_calls_before_close = scripted.planning_calls
        closed = _json_result(
            plan_handler({"max_proposals": 3}, session_id="acceptance", task_id="root")
        )

        with EngagementJournalService.open(self._root) as journal:
            snapshot = journal.load_snapshot(engagement_id)
        events = tuple(snapshot.events)
        event_types = tuple(str(event.type.value) for event in events)
        frontier_events = tuple(
            event.payload for event in events if event.type.value == "frontier_proposed"
        )
        titles_by_family = {
            payload.proposal.family_id: payload.proposal.title for payload in frontier_events
        }
        reactivation = next(
            event.payload for event in events if event.type.value == "strategy_reactivated"
        )
        reactivated_title = titles_by_family[reactivation.restored_snapshot.family_id]
        strategies = tuple(dict.fromkeys(payload.proposal.title for payload in frontier_events))
        source_commands = tuple(
            command
            for payload in frontier_events
            for command in payload.proposal.commands
            if command.command_template == "ssh {{source_case_username}}@{{target}}"
        )
        adapted = next(
            event.payload.host_adapted_command
            for event in reversed(events)
            if event.type.value == "decision_recorded"
            and event.payload.host_adapted_command is not None
        )
        archive = next(event.payload for event in events if event.type.value == "strategy_archived")
        persisted_outcomes = tuple(
            event.payload for event in events if event.type.value == "outcome_assessed"
        )
        completion_ids = {
            event.event_id
            for event in events
            if isinstance(event.payload, ToolCallCompletedPayload)
        }
        return AdaptiveResult(
            strategy_ids=strategies,
            selected_strategy_id=selected["title"],
            reactivated_strategy_id=reactivated_title,
            outcome_kinds=tuple(payload.summary for payload in persisted_outcomes),
            proof_requirement_ids=tuple(
                event.payload.proof_requirement_id
                for event in events
                if event.type.value == "objective_proof_observed"
            ),
            terminal_calls=tuple(terminal_port.calls),
            terminal_situation=terminal_port.calls[-1],
            terminal_loop_prevented=(
                closed.get("status") == "gap"
                and closed.get("gap", {}).get("code") == "engagement_terminal"
                and scripted.planning_calls == planning_calls_before_close
            ),
            outcomes_have_terminal_completion=bool(persisted_outcomes)
            and all(
                payload.terminal_tool_event_id in completion_ids for payload in persisted_outcomes
            ),
            persisted_command_template=source_commands[0].command_template,
            host_adapted_command_template=adapted.command_template,
            operational_tool_calls=tuple(self._operational_tool_calls),
            commands_are_placeholder_bound=bool(source_commands)
            and all(
                {binding.placeholder_name for binding in command.bindings}
                == {"source_case_username", "target"}
                for command in source_commands
            )
            and bool(adapted),
            source_credentials_are_unbound=all(
                next(
                    binding
                    for binding in command.bindings
                    if binding.placeholder_name == "source_case_username"
                ).source
                == "unresolved_source_case"
                for command in source_commands
            ),
            research_references_are_exact=all(
                command.capability_hint == "source-ssh-method" and command.knowledge_refs == ()
                for command in source_commands
            ),
            archive_references_are_exact=(
                archive.archive_record.snapshot.family_id
                == reactivation.restored_snapshot.family_id
                and reactivation.source_archive_event_id
                == next(event.event_id for event in events if event.payload is archive)
            ),
            real_plugin_handlers_called=(
                "sedna_manage_engagement",
                "sedna_plan_next",
                "sedna_record_decision",
            ),
            persisted_event_types=event_types,
            planning_service_calls=scripted.planning_calls,
        )

    def run_adversarial_cases(self) -> AdversarialResult:
        host = SimulatedHades(self._root)
        configured_recovery = tuple(self._scenario.get("failure_recovery", ()))
        scripted = _ScriptedPlanningHost(
            self._scenario,
            fail_first_plan="llm_unavailable" in configured_recovery,
        )
        host._context.complete_structured = scripted.complete_structured
        engagement_id = host.create(
            name="HTB-Orion",
            objective="Reject hostile evidence and recover planning.",
            target="10.10.10.11",
            required_proofs=("user-flag",),
            session_id="adversarial",
        )
        with EngagementJournalService.open(self._root) as journal:
            initial = journal.load_snapshot(engagement_id)
            scripted.scope_reference_id = initial.state.scope_references[0].reference_id
            scripted.grounding_event_id = initial.events[0].event_id

        plan_handler = host._tools["sedna_plan_next"]["handler"]
        first_response: dict[str, Any] | None = None
        recovered: dict[str, Any] | None = None
        hostile_instructions = tuple(self._scenario["hostile_instructions"])
        false_flag_present = "false_flag" in self._scenario
        for recovery_step in configured_recovery:
            if recovery_step == "llm_unavailable":
                first_response = _json_result(
                    plan_handler({"max_proposals": 3}, session_id="adversarial", task_id="root")
                )
                assert first_response.get("status") == "gap", first_response
                assert first_response.get("gap", {}).get("code") == "llm_unavailable", (
                    first_response
                )
            elif recovery_step == "unplanned_host_action":
                host.tool(
                    "terminal",
                    {"command": "fixture-only unplanned action"},
                    result="unplanned action recorded without execution",
                    session_id="adversarial",
                    tool_call_id="unplanned-action",
                )
                host.decide(
                    "Validate untrusted evidence without executing it",
                    session_id="adversarial",
                )
                for index, hostile in enumerate(hostile_instructions):
                    host.tool(
                        "terminal",
                        {"command": f"fixture-only hostile evidence {index}"},
                        result=hostile,
                        session_id="adversarial",
                        tool_call_id=f"hostile-evidence-{index}",
                    )
                if false_flag_present:
                    host.tool(
                        "terminal",
                        {"command": "fixture-only claimed proof"},
                        result=self._scenario["false_flag"],
                        session_id="adversarial",
                        tool_call_id="false-flag-evidence",
                    )
            elif recovery_step == "planner_recovered":
                recovered = _json_result(
                    plan_handler({"max_proposals": 3}, session_id="adversarial", task_id="root")
                )
                assert recovered.get("status") == "success", recovered
            else:
                raise ValueError(f"unsupported recovery step: {recovery_step}")
        if first_response is None:
            raise ValueError("failure recovery must exercise llm_unavailable")
        if recovered is None:
            recovered = first_response
        sources = tuple(self._scenario["sources"])
        if not sources:
            raise ValueError("the adversarial fixture must provide research sources")
        with EngagementJournalService.open(self._root) as journal:
            snapshot = journal.load_snapshot(engagement_id)
            query_id = next(
                event.payload.query_id
                for event in snapshot.events
                if event.type.value == "research_query_proposed"
                and event.payload.policy_decision == "allowed"
            )
            service = PlanningService(
                journal=journal,
                llm=PlanningLlmAdapter(cast(Any, host._context)),
                clock=lambda: datetime.now(UTC),
            )
            lane = ExecutionLaneKey(
                host_kind=HostKind.HADES,
                session_id="adversarial",
                task_id="root",
            )
            situation: SituationProjection | None = None
            paired_sources = zip(sources, hostile_instructions, strict=False)
            for index, (source, hostile) in enumerate(paired_sources):
                _, attachment = _evidence_attachment(snapshot, f"hostile-evidence-{index}")
                completion_event = _tool_completion(snapshot, f"hostile-evidence-{index}")
                situation = service.record_research_result(
                    lane,
                    query_id=query_id,
                    source_id=source["source_id"],
                    normalized_locator=source["locator"],
                    content=hostile.encode(),
                    media_type=attachment.evidence.media_type,
                    evidence_ids=(attachment.evidence.evidence_id,),
                    tool_event_ids=(completion_event.event_id,),
                    assessment="irrelevant",
                    confidence=1.0,
                    summary="Hostile instructions are untrusted evidence, not actions.",
                    related_event_ids=(completion_event.event_id,),
                )
                snapshot = journal.load_snapshot(engagement_id)
            if situation is None:
                raise ValueError("the adversarial fixture must provide hostile instructions")
            snapshot = journal.load_snapshot(engagement_id)
            research_assessments = tuple(
                event.payload
                for event in snapshot.events
                if event.type.value == "research_source_assessed"
            )
            hostile_evidence_count = sum(
                1
                for index in range(len(hostile_instructions))
                if _evidence_attachment(snapshot, f"hostile-evidence-{index}")
            )
            false_flag_evidence_observed = false_flag_present and bool(
                _evidence_attachment(snapshot, "false-flag-evidence")
            )
        decisions = self._applicability_decisions(situation)
        event_types = tuple(event.type.value for event in snapshot.events)
        consulted_source_ids = tuple(
            dict.fromkeys(
                event.payload.source_id
                for event in snapshot.events
                if event.type.value == "research_source_consulted"
            )
        )
        planning_gaps = tuple(
            event.payload.code
            for event in snapshot.events
            if event.type.value == "planning_gap_recorded"
        )
        rejected_queries = tuple(
            query
            for query in self._scenario["queries"]
            if PlanningService.evaluate_research_query(query, protected_aliases=("HTB-Orion",))[0]
            == "rejected"
        )
        recovery_steps = tuple(
            dict.fromkeys(
                {
                    "planning_gap_recorded": "llm_unavailable",
                    "unplanned_action": "unplanned_host_action",
                    "frontier_proposed": "planner_recovered",
                }[event.type.value]
                for event in snapshot.events
                if event.type.value
                in {"planning_gap_recorded", "unplanned_action", "frontier_proposed"}
            )
        )
        return AdversarialResult(
            rejected_queries=rejected_queries,
            typed_gaps=tuple(
                dict.fromkeys(
                    [value for value in decisions.values() if value != "rejected"]
                    + list(planning_gaps)
                )
            ),
            recovery_after_unplanned_action=(
                "unplanned_action" in event_types
                and scripted.planning_calls >= 2
                and recovered.get("status") == "success"
            ),
            false_flag_rejected="objective_proof_observed" not in event_types,
            hostile_instructions_inert=(
                len(research_assessments) == len(sources)
                and all(item.assessment == "irrelevant" for item in research_assessments)
                and hostile_evidence_count == len(hostile_instructions)
                and not self._operational_tool_calls
            ),
            request_sizes_bounded=max(
                len(json.dumps(first_response).encode()), len(json.dumps(recovered).encode())
            )
            < 64 * 1024,
            operational_tool_calls=tuple(self._operational_tool_calls),
            consulted_source_ids=consulted_source_ids,
            hostile_evidence_count=hostile_evidence_count,
            false_flag_evidence_observed=false_flag_evidence_observed,
            recovery_steps=recovery_steps,
            typed_research_event_types=tuple(dict.fromkeys(event_types)),
            applicability_decisions=decisions,
        )

    def _applicability_decisions(self, situation: SituationProjection) -> dict[str, str]:
        source = SourceRef(
            source_id="source-applicability",
            path="raw_src/applicability.md",
            location=SourceLocation(start_line=1, end_line=1, section="Applicability"),
        )
        constraints = {
            "windows-only-on-linux": ("os_family", "windows", "rejected"),
            "unknown-architecture": ("cpu_architecture", "x86_64", "unknown_architecture"),
            "android-adb": ("execution_environment", "android", "unsupported_android_adb"),
        }
        decisions: dict[str, str] = {}
        for case in self._scenario["applicability"]:
            dimension, value, rejection = constraints[case["case"]]
            example = ExecutionExample(
                example_id=f"example-{case['case']}",
                parent_artifact_id="reference-applicability",
                command_template="probe",
                placeholders=(),
                capability_hint="probe",
                purpose="Validate the current target context.",
                observed_role="Collected bounded evidence.",
                platform_constraints=(
                    ExecutionPlatformConstraint(
                        dimension=dimension,
                        relation="required",
                        value=value,
                        source_refs=(source,),
                    ),
                ),
                source_refs=(source,),
                extraction=ExtractionMetadata(
                    schema_version="1",
                    parser_id="acceptance",
                    parser_version="1",
                    extractor_id="acceptance",
                    extractor_version="1",
                ),
            )
            decisions[case["case"]] = (
                "accepted" if _example_applies(example, situation) else rejection
            )
        return decisions

    def _manifest(self) -> EngagementManifest:
        engagement = self._scenario["engagement"]
        return EngagementManifest(
            engagement_id=uuid5(NAMESPACE_URL, "sedna:adaptive-acceptance"),
            display_name=engagement["display_name"],
            initial_objective="Obtain both explicitly required proofs.",
            initial_scope=AuthorizationScope(
                state=AuthorizationState.AUTHORIZED,
                exact_targets=(ValidatedTarget.parse(engagement["target"]),),
            ),
            required_proofs=tuple(
                ProofRequirement(
                    proof_id=proof_id,
                    kind="flag",
                    description=f"Verify {proof_id} from local evidence.",
                )
                for proof_id in engagement["proof_requirements"]
            ),
            created_at=datetime(2026, 8, 14, tzinfo=UTC),
            created_by_host=HostIdentity(kind=HostKind.HADES, adapter_version="acceptance-v1"),
        )
