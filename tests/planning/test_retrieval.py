"""Situation-conditioned planner retrieval contracts."""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID, uuid4

import sedna.planning.retrieval as retrieval_module
from sedna.engagement import JournalRevision, ScopeReference
from sedna.knowledge.retrieval.models import (
    ExecutionExampleCoverageCode,
    ExecutionExampleCoverageGap,
    KnowledgeGap,
    KnowledgeGapCode,
    RetrievalResult,
)
from sedna.planning import ObjectiveProgress, SituationProjection
from sedna.planning.models import (
    AccessState,
    AttemptState,
    ObservedFacet,
    ObservedFact,
    OutcomeCategory,
    SituationHypothesis,
    UnresolvedInformation,
)
from sedna.planning.retrieval import (
    PlannerKnowledgeContext,
    assemble_planner_knowledge,
    build_retrieval_queries,
)


def _sha(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _situation(*, with_evidence: bool = False) -> SituationProjection:
    event_id = UUID("00000000-0000-0000-0000-000000000001")
    return SituationProjection(
        engagement_id=uuid4(),
        authoritative_journal_revision=JournalRevision(sequence=0, event_hash=_sha("head")),
        material_event_revision=0,
        state_digest=_sha("state"),
        objective_progress=ObjectiveProgress(),
        facts=(ObservedFact(event_ids=(event_id,), text="HTTP service exposed"),)
        if with_evidence
        else (),
        facets=(ObservedFacet(event_ids=(event_id,), key="os_family", value="linux"),)
        if with_evidence
        else (),
        hypotheses=(
            SituationHypothesis(
                event_ids=(event_id,), text="virtual host routing may apply", confidence=0.7
            ),
        )
        if with_evidence
        else (),
        access_states=(
            AccessState(event_ids=(event_id,), subject="ssh", state="credential available"),
        )
        if with_evidence
        else (),
    )


def test_build_retrieval_queries_emits_one_authorized_query_per_active_target() -> None:
    scopes = (
        ScopeReference(reference_id="scope-" + "1" * 32, kind="exact_target", value="10.10.10.10"),
        ScopeReference(
            reference_id="scope-" + "2" * 32,
            kind="url_origin",
            value="https://example.test",
        ),
    )

    queries = build_retrieval_queries(_situation(), scopes)

    assert tuple(query.situation.target.normalized for query in queries) == (
        "10.10.10.10",
        "https://example.test",
    )
    assert all(
        query.situation.authorization.authorizes(query.situation.target) for query in queries
    )
    assert all(query.synonyms == () for query in queries)
    assert all(query.max_candidates == 32 and query.lane_limit == 5 for query in queries)


def test_build_retrieval_queries_drops_invalid_scope_targets() -> None:
    scopes = (
        ScopeReference(reference_id="scope-" + "1" * 32, kind="exact_target", value="999.1.1.1"),
    )

    assert build_retrieval_queries(_situation(), scopes) == ()


def test_build_retrieval_queries_uses_only_evidence_backed_state() -> None:
    scope = ScopeReference(
        reference_id="scope-" + "3" * 32,
        kind="hostname",
        value="box.example.test",
    )

    (query,) = build_retrieval_queries(_situation(with_evidence=True), (scope,))

    assert query.situation.terms == ("http service exposed",)
    assert query.situation.services == ("http",)
    assert query.situation.access == ("ssh: credential available",)
    assert query.situation.hypotheses == ("virtual host routing may apply",)
    assert [(item.namespace, item.key, item.value) for item in query.facets] == [
        ("observed", "os_family", "linux")
    ]
    assert query.synonyms == ()


def test_build_retrieval_queries_never_forwards_private_values_or_flags() -> None:
    situation = _situation(with_evidence=True).model_copy(
        update={
            "facts": (
                ObservedFact(
                    event_ids=(UUID("00000000-0000-0000-0000-000000000001"),),
                    text="password hunter2",
                ),
                ObservedFact(
                    event_ids=(UUID("00000000-0000-0000-0000-000000000002"),),
                    text="HTB{private-proof}",
                ),
                ObservedFact(
                    event_ids=(UUID("00000000-0000-0000-0000-000000000003"),),
                    text="SSH service exposed",
                ),
            )
        }
    )
    scope = ScopeReference(
        reference_id="scope-" + "4" * 32,
        kind="exact_target",
        value="10.10.10.10",
    )

    (query,) = build_retrieval_queries(situation, (scope,))

    serialized = query.model_dump_json().casefold()
    assert "hunter2" not in serialized
    assert "private-proof" not in serialized
    assert query.situation.terms == ("ssh service exposed",)


def test_build_retrieval_queries_filters_private_material_from_every_text_lane() -> None:
    event_id = UUID("00000000-0000-0000-0000-000000000001")
    situation = _situation(with_evidence=True).model_copy(
        update={
            "access_states": (
                AccessState(event_ids=(event_id,), subject="ssh", state="password hunter2"),
                AccessState(event_ids=(event_id,), subject="http", state="reachable"),
            ),
            "hypotheses": (
                SituationHypothesis(
                    event_ids=(event_id,), text="token abc123 may work", confidence=0.4
                ),
                SituationHypothesis(
                    event_ids=(event_id,), text="virtual host routing may apply", confidence=0.7
                ),
            ),
            "attempts": (
                AttemptState(
                    attempt_event_id=event_id,
                    outcome=OutcomeCategory.NO_EFFECT,
                    summary="credential super-secret was rejected",
                ),
            ),
            "unresolved_information": (
                UnresolvedInformation(
                    event_ids=(event_id,), question="Is HTB{private-proof} valid?"
                ),
                UnresolvedInformation(
                    event_ids=(event_id,), question="Which HTTP route is active?"
                ),
            ),
        }
    )
    scope = ScopeReference(
        reference_id="scope-" + "5" * 32,
        kind="exact_target",
        value="10.10.10.10",
    )

    (query,) = build_retrieval_queries(situation, (scope,))

    serialized = query.model_dump_json().casefold()
    assert "hunter2" not in serialized
    assert "abc123" not in serialized
    assert "super-secret" not in serialized
    assert "private-proof" not in serialized
    assert query.situation.access == ("http: reachable",)
    assert query.situation.hypotheses == ("virtual host routing may apply",)
    assert query.situation.tried_outcomes == ()
    assert query.situation.unresolved_questions == ("which http route is active?",)


def test_planner_knowledge_context_is_frozen_bounded_and_digest_bound() -> None:
    context = PlannerKnowledgeContext(
        canonical_revision=_sha("canonical"),
        situation_digest=_sha("state"),
        source_registry_digest=_sha("registry"),
        context_digest=_sha("empty-context"),
    )

    assert context.references == ()
    assert context.execution_examples == ()
    assert context.retrieval_unavailable is False
    assert len(context.model_dump_json().encode()) <= 512 * 1024


def test_assemble_planner_knowledge_revision_binds_empty_context() -> None:
    class RetrievalSpy:
        def retrieve(self, query):
            raise AssertionError("no authorized query should access retrieval")

    class RegistryStub:
        def list_planner_hints(self, *, topic_tokens=()):
            from sedna.engagement import PlannerSourceHintPage

            assert topic_tokens == ()
            return PlannerSourceHintPage(
                registry_sha256=_sha("registry"),
                total_count=0,
                entries=(),
                truncated=False,
                canonical_bytes=2,
            )

    context = assemble_planner_knowledge(
        _situation(),
        (),
        retrieval=RetrievalSpy(),
        source_registry=RegistryStub(),
        canonical_revision=lambda: _sha("canonical"),
    )

    assert context.canonical_revision == _sha("canonical")
    assert context.source_registry_digest == _sha("registry")
    assert context.references == ()
    assert context.context_digest


def test_retrieval_unavailable_remains_typed_and_never_becomes_research_advice() -> None:
    class RetrievalUnavailableStub:
        def retrieve(self, query):
            return RetrievalResult(
                target=query.situation.target,
                authorization=query.situation.authorization,
                knowledge_gap=KnowledgeGap(
                    code=KnowledgeGapCode.RETRIEVAL_UNAVAILABLE,
                    summary="retrieval backend unavailable",
                ),
            )

    scope = ScopeReference(
        reference_id="scope-" + "6" * 32,
        kind="exact_target",
        value="10.10.10.10",
    )
    context = assemble_planner_knowledge(
        _situation(),
        (scope,),
        retrieval=RetrievalUnavailableStub(),
        source_registry=_empty_registry(),
        canonical_revision=lambda: _sha("canonical"),
    )

    assert context.retrieval_unavailable is True
    assert [gap.code for gap in context.knowledge_gaps] == [KnowledgeGapCode.RETRIEVAL_UNAVAILABLE]
    assert context.candidate_research_sources == ()


def test_canonical_revision_change_during_retrieval_fails_closed() -> None:
    revisions = iter((_sha("before"), _sha("after")))

    try:
        assemble_planner_knowledge(
            _situation(),
            (),
            retrieval=object(),
            source_registry=_empty_registry(),
            canonical_revision=lambda: next(revisions),
        )
    except RuntimeError as exc:
        assert str(exc) == "canonical knowledge changed during planner retrieval"
    else:
        raise AssertionError("concurrent canonical revision change must fail closed")


def _empty_registry():
    class RegistryStub:
        def list_planner_hints(self, *, topic_tokens=()):
            from sedna.engagement import PlannerSourceHintPage

            return PlannerSourceHintPage(
                registry_sha256=_sha("registry"),
                total_count=0,
                entries=(),
                truncated=False,
                canonical_bytes=2,
            )

    return RegistryStub()


def test_execution_example_coverage_gaps_are_deduplicated_and_globally_bounded() -> None:
    gaps = tuple(
        ExecutionExampleCoverageGap(
            code=ExecutionExampleCoverageCode.LEGACY_BUNDLE_WITHOUT_EXAMPLES,
            source_id=f"source-{index:02d}",
            semantic_schema_version="2.4.0",
            explanation="legacy bundle has no execution examples",
        )
        for index in range(40)
    )

    selected = retrieval_module._bounded_example_gaps((*gaps, gaps[0]))

    assert len(selected) == 32
    assert selected == gaps[:32]
