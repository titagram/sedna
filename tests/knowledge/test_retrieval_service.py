"""Lane-aware retrieval orchestration and explicit knowledge-gap behavior."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import cast

import pytest

from sedna.knowledge.retrieval import (
    AuthorizationScope,
    AuthorizationState,
    CurrentSituation,
    EpistemicLane,
    IndexAudit,
    IndexCandidate,
    IndexedArtifact,
    KnowledgeGapCode,
    RetrievalQuery,
    SituationFacet,
    ValidatedTarget,
)
from sedna.knowledge.retrieval import service as service_module
from sedna.knowledge.retrieval.sqlite import SQLiteRetrievalIndex
from sedna.knowledge.schema import SemanticKnowledgeBundle, VerificationStatus
from tests.knowledge.test_retrieval_ranking import (
    _applicability,
    _candidate,
    _case_step,
    _fact,
    _guidance,
    _reference,
)


class _RecordingIndex:
    def __init__(
        self,
        candidates: dict[EpistemicLane, tuple[IndexCandidate, ...]] | None = None,
        *,
        artifact: object | None = None,
        search_error: Exception | None = None,
        artifact_error: Exception | None = None,
    ) -> None:
        self.candidates = candidates or {}
        self.artifact = artifact
        self.search_error = search_error
        self.artifact_error = artifact_error
        self.calls: list[tuple[object, ...]] = []

    def search_candidates(
        self,
        query: RetrievalQuery,
        *,
        lane: EpistemicLane,
        limit: int,
    ) -> tuple[IndexCandidate, ...]:
        self.calls.append(("search_candidates", query, lane, limit))
        if self.search_error is not None:
            raise self.search_error
        return self.candidates.get(lane, ())

    def get_artifact(self, artifact_id: str):
        self.calls.append(("get_artifact", artifact_id))
        if self.artifact_error is not None:
            raise self.artifact_error
        return self.artifact

    def upsert_bundle(self, bundle: SemanticKnowledgeBundle) -> None:
        raise AssertionError("not used by retrieval service")

    def delete_source(self, source_id: str) -> None:
        raise AssertionError("not used by retrieval service")

    def rebuild(self, bundles: Iterable[SemanticKnowledgeBundle]) -> IndexAudit:
        raise AssertionError("not used by retrieval service")

    def audit(self) -> IndexAudit:
        raise AssertionError("not used by retrieval service")

    def close(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


def _authorized_query(
    *,
    target: str = "192.168.0.1",
    terms: tuple[str, ...] = ("http",),
    facts: tuple[SituationFacet, ...] = (),
    services: tuple[str, ...] = (),
    max_candidates: int = 8,
    lane_limit: int = 2,
) -> RetrievalQuery:
    parsed = ValidatedTarget.parse(target)
    return RetrievalQuery(
        situation=CurrentSituation(
            target=parsed,
            authorization=AuthorizationScope(
                state=AuthorizationState.AUTHORIZED,
                exact_targets=(parsed,),
            ),
            services=services,
            facts=facts,
        ),
        terms=terms,
        max_candidates=max_candidates,
        lane_limit=lane_limit,
    )


def _service(index: _RecordingIndex):
    from sedna.knowledge.retrieval.service import KnowledgeRetrievalService

    return KnowledgeRetrievalService(index)


def test_service_is_available_from_the_approved_public_knowledge_surfaces() -> None:
    from sedna.knowledge import KnowledgeRetrievalService as KnowledgeService
    from sedna.knowledge.retrieval import KnowledgeRetrievalService as RetrievalService

    assert KnowledgeService is RetrievalService


def test_invalid_target_is_typed_before_any_index_method_is_called() -> None:
    target = ValidatedTarget.parse("300.456.456.123")
    query = RetrievalQuery(situation=CurrentSituation(target=target), terms=("http",))
    index = _RecordingIndex(search_error=AssertionError("backend must remain untouched"))

    result = _service(index).retrieve(query)

    assert result.target == target
    assert result.knowledge_gap is not None
    assert result.knowledge_gap.code is KnowledgeGapCode.INVALID_TARGET
    assert index.calls == []


@pytest.mark.parametrize("state", (AuthorizationState.UNKNOWN, AuthorizationState.UNAUTHORIZED))
def test_unknown_and_unauthorized_scope_stop_before_backend_access(
    state: AuthorizationState,
) -> None:
    target = ValidatedTarget.parse("192.168.0.1")
    query = RetrievalQuery(
        situation=CurrentSituation(
            target=target,
            authorization=AuthorizationScope(state=state),
        ),
        terms=("http",),
    )
    index = _RecordingIndex(search_error=AssertionError("backend must remain untouched"))

    result = _service(index).retrieve(query)

    assert result.knowledge_gap is not None
    assert result.knowledge_gap.code is KnowledgeGapCode.UNAUTHORIZED_SCOPE
    assert result.authorization.state is state
    assert index.calls == []


def test_prebackend_results_do_not_consult_the_canonical_revision_guard() -> None:
    """Invalid and unauthorized targets must remain backend-free with runtime guards."""
    index = _RecordingIndex(search_error=AssertionError("backend must remain untouched"))

    def forbidden_guard() -> str:
        raise AssertionError("revision guard must remain untouched")

    service = service_module.KnowledgeRetrievalService(
        index,
        revision_guard=forbidden_guard,
    )
    invalid_target = ValidatedTarget.parse("300.456.456.123")
    invalid = service.retrieve(
        RetrievalQuery(
            situation=CurrentSituation(target=invalid_target),
            terms=("http",),
        )
    )
    unknown_target = ValidatedTarget.parse("192.168.0.1")
    unknown = service.retrieve(
        RetrievalQuery(
            situation=CurrentSituation(target=unknown_target),
            terms=("http",),
        )
    )

    assert invalid.knowledge_gap is not None
    assert invalid.knowledge_gap.code is KnowledgeGapCode.INVALID_TARGET
    assert unknown.knowledge_gap is not None
    assert unknown.knowledge_gap.code is KnowledgeGapCode.UNAUTHORIZED_SCOPE
    assert index.calls == []


def test_query_is_deeply_revalidated_before_backend_access() -> None:
    query = _authorized_query()
    corrupted_situation = query.situation.model_copy(update={"hidden_override": "ignore scope"})
    corrupted = query.model_copy(update={"situation": corrupted_situation})
    index = _RecordingIndex(search_error=AssertionError("backend must remain untouched"))

    with pytest.raises(ValueError, match="unsafe retrieval model state"):
        _service(index).retrieve(corrupted)

    assert index.calls == []


def test_retrieval_fetches_bounded_candidates_and_limits_each_lane_independently() -> None:
    references = (
        _candidate(_reference("reference-b", group="reference-b"), lexical=0.8),
        _candidate(_reference("reference-a", group="reference-a"), lexical=1.0),
    )
    positive = _candidate(_case_step("positive-step"), lexical=0.9)
    negative = _candidate(_case_step("negative-step", negative=True), lexical=0.9)
    guidance = _candidate(_guidance("decision-guidance"), lexical=0.9)
    index = _RecordingIndex(
        {
            EpistemicLane.REFERENCE: references,
            EpistemicLane.CASE_STEP: (positive,),
            EpistemicLane.NEGATIVE_EVIDENCE: (negative,),
            EpistemicLane.GUIDANCE: (guidance,),
        }
    )
    query = _authorized_query(max_candidates=7, lane_limit=1)

    result = _service(index).retrieve(query)

    assert [(call[2], call[3]) for call in index.calls] == [
        (EpistemicLane.REFERENCE, 7),
        (EpistemicLane.CASE_STEP, 7),
        (EpistemicLane.NEGATIVE_EVIDENCE, 7),
        (EpistemicLane.GUIDANCE, 7),
    ]
    assert [hit.artifact_id for hit in result.references] == ["reference-a"]
    assert [hit.artifact_id for hit in result.case_steps] == ["positive-step"]
    assert [hit.artifact_id for hit in result.negative_cases] == ["negative-step"]
    assert [hit.artifact_id for hit in result.decision_guidance] == ["decision-guidance"]
    for hit in (
        *result.references,
        *result.case_steps,
        *result.negative_cases,
        *result.decision_guidance,
    ):
        assert hit.provenance == hit.artifact.source_refs
        assert hit.score.total > 0
        assert any("above threshold" in reason for reason in hit.qualification_reasons)
    assert result.knowledge_gap is None


def test_retrieval_is_deterministic_and_preserves_incompatibility_rejections() -> None:
    incompatible = _reference(
        "windows-only",
        applicability=_applicability(source_id="source-windows-only", os_family="windows"),
    )
    applicable = _reference("linux-general")
    candidates = (
        _candidate(incompatible, lexical=1.0),
        _candidate(applicable, lexical=0.8),
    )
    index = _RecordingIndex({EpistemicLane.REFERENCE: candidates})
    query = _authorized_query(facts=(_fact("typed", "os_family", "linux"),))
    service = _service(index)

    first = service.retrieve(query)
    second = service.retrieve(query)

    assert first == second
    assert [hit.artifact_id for hit in first.references] == ["linux-general"]
    assert [item.artifact_id for item in first.rejected_candidates] == ["windows-only"]
    assert first.rejected_candidates[0].rejection_reasons == (
        "required typed.os_family=windows conflicts with observed linux",
    )


def test_no_qualifying_hit_returns_explainable_no_applicable_knowledge_gap() -> None:
    incompatible = _reference(
        "windows-only-gap",
        applicability=_applicability(source_id="source-windows-only-gap", os_family="windows"),
    )
    index = _RecordingIndex({EpistemicLane.REFERENCE: (_candidate(incompatible, lexical=1.0),)})
    query = _authorized_query(
        terms=("ssh", "information gathering"),
        facts=(_fact("typed", "os_family", "linux"),),
    )

    result = _service(index).retrieve(query)

    assert result.knowledge_gap is not None
    gap = result.knowledge_gap
    assert gap.code is KnowledgeGapCode.NO_APPLICABLE_KNOWLEDGE
    assert "information gathering" in (gap.observed_domain or "")
    assert "ssh" in (gap.observed_domain or "")
    assert gap.missing_context
    assert gap.suggested_document_ingestion == (
        "ingest relevant case studies with explicit applicability context",
        "ingest source-backed technical documentation matching the observed domain",
    )
    assert gap.research_eligible is True
    assert result.rejected_candidates[0].artifact_id == "windows-only-gap"


def test_android_adb_absence_is_an_explicit_gap_without_invented_advice() -> None:
    query = _authorized_query(
        target="android-lab-device",
        terms=("android", "adb", "connected device"),
    )

    result = _service(_RecordingIndex()).retrieve(query)

    assert result.knowledge_gap is not None
    gap = result.knowledge_gap
    assert gap.code is KnowledgeGapCode.NO_APPLICABLE_KNOWLEDGE
    assert "adb" in (gap.observed_domain or "")
    assert "android" in (gap.observed_domain or "")
    assert gap.research_eligible is True
    assert "run " not in gap.summary
    assert "use " not in gap.summary


def test_backend_failure_returns_a_safe_typed_gap_without_raw_error_text() -> None:
    index = _RecordingIndex(search_error=RuntimeError("secret backend filesystem details"))

    result = _service(index).retrieve(_authorized_query())

    assert result.knowledge_gap is not None
    assert result.knowledge_gap.code is KnowledgeGapCode.RETRIEVAL_UNAVAILABLE
    rendered = result.model_dump_json()
    assert "secret backend filesystem details" not in rendered
    assert result.knowledge_gap.summary == "knowledge retrieval is temporarily unavailable"
    assert result.knowledge_gap.missing_context == ("retrieval index availability",)
    assert result.knowledge_gap.research_eligible is False
    assert result.knowledge_gap.suggested_document_ingestion == ()


def test_revision_change_during_backend_read_discards_retrieval_and_artifact() -> None:
    """Removing either post-read check would expose a result from an obsolete revision."""
    candidate = _candidate(_reference("revision-race"))
    search_tokens = iter(("a" * 64, "b" * 64))
    search_service = service_module.KnowledgeRetrievalService(
        _RecordingIndex({EpistemicLane.REFERENCE: (candidate,)}),
        revision_guard=lambda: next(search_tokens),
    )

    result = search_service.retrieve(_authorized_query())

    assert result.knowledge_gap is not None
    assert result.knowledge_gap.code is KnowledgeGapCode.RETRIEVAL_UNAVAILABLE

    artifact_tokens = iter(("c" * 64, "d" * 64))
    artifact_service = service_module.KnowledgeRetrievalService(
        _RecordingIndex(artifact=_reference("revision-race")),
        revision_guard=lambda: next(artifact_tokens),
    )
    with pytest.raises(RuntimeError, match="knowledge artifact lookup failed"):
        artifact_service.get_artifact("revision-race")


def test_backend_over_return_is_not_ranked_or_consumed_as_unbounded_input() -> None:
    candidates = tuple(_candidate(_reference(f"over-return-{offset:02d}")) for offset in range(4))
    query = _authorized_query(max_candidates=3)
    index = _RecordingIndex({EpistemicLane.REFERENCE: candidates})

    result = _service(index).retrieve(query)

    assert result.knowledge_gap is not None
    assert result.knowledge_gap.code is KnowledgeGapCode.RETRIEVAL_UNAVAILABLE
    assert result.knowledge_gap.research_eligible is False
    assert len(index.calls) == 1


def test_corrupt_candidate_is_retrieval_unavailable_not_corpus_absence() -> None:
    candidate = _candidate(_reference("corrupt-candidate"))
    corrupt = candidate.model_copy(update={"hidden_backend_state": "private detail"})
    index = _RecordingIndex({EpistemicLane.REFERENCE: (corrupt,)})

    result = _service(index).retrieve(_authorized_query())

    assert result.knowledge_gap is not None
    assert result.knowledge_gap.code is KnowledgeGapCode.RETRIEVAL_UNAVAILABLE
    assert "private detail" not in result.model_dump_json()


def test_closed_sqlite_index_is_retrieval_unavailable_not_corpus_absence(
    tmp_path: Path,
) -> None:
    index = SQLiteRetrievalIndex(tmp_path / "closed.sqlite")
    index.close()

    result = _service(cast(_RecordingIndex, index)).retrieve(_authorized_query())

    assert result.knowledge_gap is not None
    assert result.knowledge_gap.code is KnowledgeGapCode.RETRIEVAL_UNAVAILABLE
    assert result.knowledge_gap.research_eligible is False


@pytest.mark.parametrize("failure_stage", ("ranking", "selector"))
def test_ranking_and_selector_failures_are_safe_retrieval_unavailable_gaps(
    failure_stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _RecordingIndex(
        {EpistemicLane.REFERENCE: (_candidate(_reference("pipeline-failure")),)}
    )

    def fail(*args: object, **kwargs: object):
        del args, kwargs
        raise RuntimeError("private pipeline exception")

    monkeypatch.setattr(
        service_module.ranking,
        "rank_candidates" if failure_stage == "ranking" else "select_diversified_hits",
        fail,
    )

    result = _service(index).retrieve(_authorized_query())

    assert result.knowledge_gap is not None
    assert result.knowledge_gap.code is KnowledgeGapCode.RETRIEVAL_UNAVAILABLE
    assert "private pipeline exception" not in result.model_dump_json()


def test_invalid_ranked_output_is_deeply_rejected_as_retrieval_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _authorized_query()
    candidate = _candidate(_reference("invalid-ranked-output"))
    ranked = service_module.ranking.rank_candidates(query, (candidate,))
    corrupt_hit = ranked.references[0].model_copy(update={"hidden_output": "private detail"})

    monkeypatch.setattr(
        service_module.ranking,
        "select_diversified_hits",
        lambda hits, *, limit: (corrupt_hit,) if hits else (),
    )

    result = _service(_RecordingIndex({EpistemicLane.REFERENCE: (candidate,)})).retrieve(query)

    assert result.knowledge_gap is not None
    assert result.knowledge_gap.code is KnowledgeGapCode.RETRIEVAL_UNAVAILABLE
    assert "private detail" not in result.model_dump_json()


def test_under_threshold_candidates_remain_a_real_no_applicable_knowledge_gap() -> None:
    weak = _reference(
        "under-threshold",
        applicability=_applicability(
            source_id="source-under-threshold",
            os_family="windows",
        ),
        status=VerificationStatus.DEPRECATED,
    )
    index = _RecordingIndex({EpistemicLane.REFERENCE: (_candidate(weak, lexical=0.0),)})

    result = _service(index).retrieve(_authorized_query())

    assert result.knowledge_gap is not None
    assert result.knowledge_gap.code is KnowledgeGapCode.NO_APPLICABLE_KNOWLEDGE
    assert result.knowledge_gap.research_eligible is True
    assert result.knowledge_gap.suggested_document_ingestion
    assert any(
        "below reference threshold" in reason
        for reason in result.rejected_candidates[0].rejection_reasons
    )


def test_get_artifact_strictly_validates_identifier_and_canonical_output() -> None:
    artifact = _reference("lookup-reference")
    index = _RecordingIndex(artifact=artifact)
    service = _service(index)

    loaded = service.get_artifact("lookup-reference")

    assert loaded == artifact
    assert loaded is not artifact
    assert index.calls == [("get_artifact", "lookup-reference")]

    before = tuple(index.calls)
    with pytest.raises(ValueError):
        service.get_artifact(" lookup-reference ")
    assert tuple(index.calls) == before


def test_get_artifact_masks_backend_errors_and_rejects_unsafe_or_wrong_output() -> None:
    erroring = _service(_RecordingIndex(artifact_error=RuntimeError("private database pathname")))
    with pytest.raises(RuntimeError, match="knowledge artifact lookup failed") as failure:
        erroring.get_artifact("lookup-reference")
    assert "private database pathname" not in str(failure.value)

    corrupted = _reference("lookup-reference").model_copy(update={"hidden": "unsafe"})
    with pytest.raises(RuntimeError, match="knowledge artifact lookup failed"):
        _service(_RecordingIndex(artifact=corrupted)).get_artifact("lookup-reference")

    wrong = cast(IndexedArtifact, _reference("different-reference"))
    with pytest.raises(RuntimeError, match="knowledge artifact lookup failed"):
        _service(_RecordingIndex(artifact=wrong)).get_artifact("lookup-reference")


def test_get_execution_examples_drilldown_returns_examples_or_gap() -> None:
    from sedna.knowledge.retrieval.models import (
        ExecutionExampleCoverageGap,
        ExecutionExampleDrilldown,
        ExecutionExampleLocator,
    )
    from sedna.knowledge.schema.execution import ExecutionExample

    class _DrillIndex(_RecordingIndex):
        def __init__(self, locators: tuple, *, source_capability: str) -> None:
            super().__init__()
            self.locators = locators
            self.source_capability = source_capability

        def get_execution_example_locators(self, parent_artifact_id: str):
            return self.locators

        def get_source_capability(self, source_id: str) -> str:
            return self.source_capability

    locator = ExecutionExampleLocator(
        example_id="execution-example-probe",
        parent_artifact_id="reference-http",
        source_id="source-a",
    )
    example = ExecutionExample(
        schema_version="1",
        example_id="execution-example-probe",
        parent_artifact_id="reference-http",
        command_template="curl -i {{target}}",
        placeholders=(
            {
                "name": "target",
                "kind": "target",
                "binding_policy": "authorized_scope",
                "role": "authorized HTTP target",
            },
        ),
        capability_hint="http.inspect",
        purpose="Inspect HTTP response metadata.",
        observed_role="Gathered response evidence in the source case.",
        source_refs=(
            {
                "source_id": "source-a",
                "path": "walkthrough.md",
                "location": {"page": 1},
            },
        ),
        extraction={
            "schema_version": "1",
            "parser_id": "p",
            "parser_version": "1",
            "extractor_id": "e",
            "extractor_version": "1",
            "model_id": "m",
            "model_version": "1",
        },
        requires_validation=True,
    )

    from sedna.knowledge.retrieval.service import KnowledgeRetrievalService as DrillService

    service = DrillService(
        index=_DrillIndex((locator,), source_capability="1"),
        execution_example_loader=lambda source_id, parent_artifact_id, example_ids: (
            (example,) if parent_artifact_id == "reference-http" else ()
        ),
    )
    drilldown = service.get_execution_examples("reference-http")
    assert isinstance(drilldown, ExecutionExampleDrilldown)
    assert [item.example_id for item in drilldown.examples] == [example.example_id]

    legacy = DrillService(
        index=_DrillIndex((), source_capability="2.4.0"),
    )
    gap = legacy.get_execution_examples("reference-http")
    assert gap.coverage_gap is not None
    assert gap.coverage_gap.code.value == "legacy_bundle_without_examples"
    assert isinstance(gap.coverage_gap, ExecutionExampleCoverageGap)
