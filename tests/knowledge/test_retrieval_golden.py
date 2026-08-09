"""Versioned end-to-end evaluation for Sedna's lexical/facet retrieval baseline."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from sedna.knowledge.retrieval import (
    AuthorizationScope,
    AuthorizationState,
    CurrentSituation,
    EpistemicLane,
    IndexCandidate,
    KnowledgeGapCode,
    KnowledgeRetrievalService,
    RetrievalEvaluationReport,
    RetrievalQuery,
    RetrievalScenarioEvaluation,
    SituationFacet,
    ValidatedTarget,
)
from sedna.knowledge.retrieval.sqlite import SQLiteRetrievalIndex
from sedna.knowledge.schema import SemanticKnowledgeBundle

GOLDEN_SUITE = Path(__file__).parent / "fixtures" / "retrieval" / "golden.yaml"
_LANE_FIELDS = {
    "reference": "references",
    "case_step": "case_steps",
    "negative_evidence": "negative_cases",
    "guidance": "decision_guidance",
}


def _load_suite() -> dict[str, Any]:
    payload = yaml.safe_load(GOLDEN_SUITE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _bundles(payload: dict[str, Any]) -> tuple[SemanticKnowledgeBundle, ...]:
    return tuple(SemanticKnowledgeBundle.model_validate(bundle) for bundle in payload["bundles"])


def _query(scenario: dict[str, Any], *, default_lane_limit: int = 8) -> RetrievalQuery:
    target = ValidatedTarget.parse(str(scenario["target"]))
    authorization = AuthorizationScope(
        state=(AuthorizationState.AUTHORIZED if target.is_valid else AuthorizationState.UNKNOWN),
        exact_targets=(target,) if target.is_valid else (),
    )
    return RetrievalQuery(
        situation=CurrentSituation(
            target=target,
            authorization=authorization,
            terms=tuple(scenario.get("terms", ())),
            facts=tuple(SituationFacet.model_validate(item) for item in scenario.get("facts", ())),
        ),
        terms=tuple(scenario.get("terms", ())),
        max_candidates=32,
        lane_limit=int(scenario.get("lane_limit", default_lane_limit)),
    )


def _hit_ids(result: Any) -> tuple[str, ...]:
    return tuple(
        hit.artifact_id for field in _LANE_FIELDS.values() for hit in getattr(result, field)
    )


def _result_signature(result: Any) -> dict[str, Any]:
    return result.model_dump(mode="json")


@dataclass
class _CountingIndex:
    delegate: SQLiteRetrievalIndex
    search_calls: int = 0

    def search_candidates(
        self,
        query: RetrievalQuery,
        *,
        lane: EpistemicLane,
        limit: int,
    ) -> tuple[IndexCandidate, ...]:
        self.search_calls += 1
        return self.delegate.search_candidates(query, lane=lane, limit=limit)

    def get_artifact(self, artifact_id: str):
        return self.delegate.get_artifact(artifact_id)


def test_evaluation_report_derives_bounded_macro_metrics_and_latency() -> None:
    report = RetrievalEvaluationReport.from_scenarios(
        suite_version="retrieval-golden-v1",
        k=3,
        scenarios=(
            RetrievalScenarioEvaluation.from_ranked_ids(
                scenario_id="private-ip",
                k=3,
                relevant_artifact_ids=("reference-private", "step-linux"),
                returned_artifact_ids=("reference-private", "unrelated", "step-linux"),
                incompatible_artifact_ids=("windows-only",),
                reproducible=True,
                latency_seconds=0.01,
            ),
            RetrievalScenarioEvaluation.from_ranked_ids(
                scenario_id="confirmed-linux",
                k=3,
                relevant_artifact_ids=("reference-private",),
                returned_artifact_ids=("reference-private",),
                incompatible_artifact_ids=("windows-only",),
                reproducible=True,
                latency_seconds=0.02,
            ),
        ),
        index_size_bytes=4096,
    )

    assert report.recall_at_k == 1.0
    assert report.precision_at_k == pytest.approx((2 / 3 + 1.0) / 2)
    assert report.incompatibility_violations == 0
    assert report.deterministic_reproducibility is True
    assert report.latency_seconds == pytest.approx(0.03)
    assert report.maximum_scenario_latency_seconds == 0.02
    assert report.index_size_bytes == 4096


def test_evaluation_report_rejects_duplicate_scenarios_nonfinite_latency_and_bad_metrics() -> None:
    scenario = RetrievalScenarioEvaluation.from_ranked_ids(
        scenario_id="duplicate",
        k=1,
        relevant_artifact_ids=("expected",),
        returned_artifact_ids=("wrong",),
        incompatible_artifact_ids=("wrong",),
        reproducible=False,
        latency_seconds=0.1,
    )
    assert scenario.recall_at_k == 0.0
    assert scenario.precision_at_k == 0.0
    assert scenario.incompatibility_violations == ("wrong",)

    with pytest.raises(ValidationError, match="scenario IDs must be unique"):
        RetrievalEvaluationReport.from_scenarios(
            suite_version="retrieval-golden-v1",
            k=1,
            scenarios=(scenario, scenario),
            index_size_bytes=1,
        )
    with pytest.raises(ValidationError, match="finite"):
        RetrievalScenarioEvaluation(
            scenario_id="bad-latency",
            k=1,
            relevant_artifact_ids=("expected",),
            returned_artifact_ids=("expected",),
            latency_seconds=math.inf,
            reproducible=True,
        )


def test_evaluation_report_normalizes_set_like_ids_and_scenario_order() -> None:
    later = RetrievalScenarioEvaluation.from_ranked_ids(
        scenario_id="z-scenario",
        k=2,
        relevant_artifact_ids=("z-relevant", "a-relevant"),
        returned_artifact_ids=("z-relevant", "a-relevant"),
        incompatible_artifact_ids=("z-incompatible", "a-incompatible"),
        reproducible=True,
        latency_seconds=0.01,
    )
    earlier = RetrievalScenarioEvaluation.from_ranked_ids(
        scenario_id="a-scenario",
        k=2,
        relevant_artifact_ids=("only",),
        returned_artifact_ids=("only",),
        reproducible=True,
        latency_seconds=0.01,
    )

    report = RetrievalEvaluationReport.from_scenarios(
        suite_version="retrieval-golden-v1",
        k=2,
        scenarios=(later, earlier),
        index_size_bytes=1,
    )

    assert tuple(item.scenario_id for item in report.scenarios) == ("a-scenario", "z-scenario")
    assert report.scenarios[1].relevant_artifact_ids == ("a-relevant", "z-relevant")
    assert report.scenarios[1].incompatible_artifact_ids == (
        "a-incompatible",
        "z-incompatible",
    )
    assert report.scenarios[1].returned_artifact_ids == ("z-relevant", "a-relevant")


def test_golden_fixture_contains_only_directly_valid_canonical_m2_bundles() -> None:
    payload = _load_suite()

    bundles = _bundles(payload)

    assert payload["suite_version"] == "retrieval-golden-v1"
    assert [bundle.source_id for bundle in bundles] == [
        "golden-core",
        "golden-copy-a",
        "golden-copy-b",
    ]
    copied = bundles[1:]
    assert copied[0].source_sha256 == copied[1].source_sha256
    assert copied[0].references[0].statement == copied[1].references[0].statement
    assert (
        copied[0].references[0].assessment.independence_group
        == copied[1].references[0].assessment.independence_group
    )
    rendered = GOLDEN_SUITE.read_text(encoding="utf-8")
    assert re.search(r"(?i)(?:htb|flag)\s*\{", rendered) is None
    assert re.search(r"(?i)(?:password|api[_ -]?key|private[_ -]?key)\s*[:=]", rendered) is None


def test_golden_retrieval_meets_lexical_facet_baselines_and_gap_contracts(
    tmp_path: Path,
) -> None:
    payload = _load_suite()
    bundles = _bundles(payload)
    scenarios = {item["scenario_id"]: item for item in payload["scenarios"]}
    metric_evaluations: list[RetrievalScenarioEvaluation] = []
    k = int(payload["k"])
    index_path = tmp_path / "golden.sqlite"

    with SQLiteRetrievalIndex(index_path) as index:
        audit = index.rebuild(bundles)
        assert audit.rebuild_required is False
        service = KnowledgeRetrievalService(index=index)

        for scenario_id in (
            "private-ip-information-gathering",
            "unknown-os-conditional-windows",
            "confirmed-linux-excludes-windows",
            "copied-source-diversity",
        ):
            scenario = scenarios[scenario_id]
            query = _query(scenario)
            started = time.perf_counter()
            first = service.retrieve(query)
            second = service.retrieve(query)
            elapsed = time.perf_counter() - started
            assert first.knowledge_gap is None
            assert _result_signature(first) == _result_signature(second)

            for lane, expected_ids in scenario["expected_lanes"].items():
                actual = {hit.artifact_id for hit in getattr(first, _LANE_FIELDS[lane])}
                assert set(expected_ids) <= actual
            all_hit_ids = set(_hit_ids(first))
            assert not all_hit_ids.intersection(scenario.get("expected_absent_artifact_ids", ()))
            rejected_ids = {candidate.artifact_id for candidate in first.rejected_candidates}
            assert set(scenario.get("expected_rejected_artifact_ids", ())) <= rejected_ids

            missing_fragment = scenario.get("expected_missing_context_contains")
            if missing_fragment:
                missing = tuple(
                    reason
                    for field in _LANE_FIELDS.values()
                    for hit in getattr(first, field)
                    for reason in hit.missing_context
                )
                assert all(
                    any(fragment in reason for reason in missing) for fragment in missing_fragment
                )

            maximum_copy_hits = scenario.get("expected_copy_group_maximum")
            if maximum_copy_hits is not None:
                group_counts: dict[str, int] = {}
                for hit in first.references:
                    group = hit.artifact.assessment.independence_group
                    group_counts[group] = group_counts.get(group, 0) + 1
                copied_group = "2" * 64
                assert group_counts.get(copied_group, 0) <= int(maximum_copy_hits)

            metric_evaluations.append(
                RetrievalScenarioEvaluation.from_ranked_ids(
                    scenario_id=scenario_id,
                    k=k,
                    relevant_artifact_ids=tuple(scenario["relevant_artifact_ids"]),
                    returned_artifact_ids=_hit_ids(first)[:k],
                    incompatible_artifact_ids=tuple(scenario["incompatible_artifact_ids"]),
                    reproducible=True,
                    latency_seconds=elapsed,
                )
            )

        invalid = scenarios["invalid-ip-prebackend"]
        counting_index = _CountingIndex(index)
        invalid_result = KnowledgeRetrievalService(index=counting_index).retrieve(_query(invalid))
        assert invalid_result.knowledge_gap is not None
        assert invalid_result.knowledge_gap.code is KnowledgeGapCode.INVALID_TARGET
        assert counting_index.search_calls == invalid["expected_backend_calls"] == 0

        android = scenarios["android-adb-no-knowledge"]
        android_result = service.retrieve(_query(android))
        assert android_result.knowledge_gap is not None
        assert android_result.knowledge_gap.code.value == android["expected_gap"]
        assert (
            android_result.knowledge_gap.research_eligible is android["expected_research_eligible"]
        )
        assert (
            bool(android_result.knowledge_gap.suggested_document_ingestion)
            is android["expected_document_offer"]
        )

    report = RetrievalEvaluationReport.from_scenarios(
        suite_version=payload["suite_version"],
        k=k,
        scenarios=tuple(metric_evaluations),
        index_size_bytes=index_path.stat().st_size,
    )
    baselines = payload["baselines"]
    assert report.recall_at_k >= baselines["minimum_recall_at_k"]
    assert report.precision_at_k >= baselines["minimum_precision_at_k"]
    assert report.incompatibility_violations <= baselines["maximum_incompatibility_violations"]
    assert report.deterministic_reproducibility is baselines["require_reproducibility"]
    assert report.maximum_scenario_latency_seconds <= baselines["maximum_scenario_latency_seconds"]
    assert report.latency_seconds <= baselines["maximum_total_latency_seconds"]
    assert report.index_size_bytes <= baselines["maximum_index_size_bytes"]


def test_exact_lookup_and_delete_then_rebuild_are_result_equivalent(tmp_path: Path) -> None:
    payload = _load_suite()
    bundles = _bundles(payload)
    scenarios = {
        item["scenario_id"]: item
        for item in payload["scenarios"]
        if item["scenario_id"]
        in {
            "private-ip-information-gathering",
            "unknown-os-conditional-windows",
            "confirmed-linux-excludes-windows",
            "copied-source-diversity",
            "android-adb-no-knowledge",
        }
    }
    index_path = tmp_path / "golden.sqlite"

    with SQLiteRetrievalIndex(index_path) as index:
        index.rebuild(bundles)
        service = KnowledgeRetrievalService(index=index)
        artifact = service.get_artifact("case-http-success-step")
        assert artifact is not None
        assert artifact.step_id == "case-http-success-step"
        before = {
            scenario_id: _result_signature(service.retrieve(_query(scenario)))
            for scenario_id, scenario in scenarios.items()
        }

        index.delete_source("golden-core")
        assert service.get_artifact("case-http-success-step") is None
        assert service.get_artifact("ref-copied-enumeration-a") is not None
        rebuilt = index.rebuild(reversed(bundles))
        after = {
            scenario_id: _result_signature(service.retrieve(_query(scenario)))
            for scenario_id, scenario in scenarios.items()
        }

        assert rebuilt.rebuild_required is False
        assert before == after
        assert index.audit().rebuild_required is False
