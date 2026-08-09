"""Regression coverage for foundation and semantic version migrations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sedna.knowledge import IngestionPipeline
from sedna.knowledge.inventory import SourceCandidate, discover_sources
from sedna.knowledge.parsing import PreparedSource
from sedna.knowledge.repository import CanonicalKnowledgeRepository
from sedna.knowledge.schema import (
    SemanticCallMetadata,
    SemanticCompilationManifest,
    SemanticKnowledgeBundle,
    SemanticVerificationRecord,
)
from sedna.knowledge.semantic import (
    SemanticCompilationResult,
    SemanticCompiler,
    SemanticIngestionService,
)
from sedna.knowledge.semantic.compiler import SEMANTIC_COMPILER_VERSION, SEMANTIC_SCHEMA_VERSION
from sedna.knowledge.semantic.llm import HadesLlmAdapter
from sedna.knowledge.semantic.prompts import (
    CRITIC_PROMPT_VERSION,
    EXTRACTOR_PROMPT_VERSION,
    REPAIR_PROMPT_VERSION,
)

_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
_SOURCE_BYTES = b"""# Evidence locator migration

## Compare observations

Inspect the documented response, preserve the observed evidence, and compare it with the current
hypothesis before selecting the next discriminating observation.

![Proof](https://alice:secret@example.test/proof.png?api_key=x#frag)

Record whether the new observation supports or weakens the hypothesis without repeating a step
that produces no additional evidence.
"""


@dataclass(frozen=True)
class _Usage:
    input_tokens: int = 11
    output_tokens: int = 7


@dataclass(frozen=True)
class _HostResult:
    parsed: object
    provider: str = "scripted-provider"
    model: str = "scripted-model"
    agent_id: str = "scripted-agent"
    usage: _Usage = field(default_factory=_Usage)
    audit: object = None


class _ScriptedHost:
    def __init__(self, responses: list[object]) -> None:
        self._responses = iter(responses)
        self.calls: list[dict[str, Any]] = []

    def complete_structured(self, **kwargs: Any) -> _HostResult:
        self.calls.append(kwargs)
        try:
            response = next(self._responses)
        except StopIteration as error:
            raise AssertionError("scripted host received an unexpected semantic call") from error
        purpose = str(kwargs["purpose"]).rsplit(".", maxsplit=1)[-1]
        return _HostResult(parsed=response, model=f"scripted-{purpose}")


def _source(tmp_path: Path) -> tuple[Path, Path, bytes, SourceCandidate]:
    source_root = tmp_path / "raw_src"
    source_path = source_root / "01_Information-Gathering/Academy/locator-migration.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(_SOURCE_BYTES)
    candidate = discover_sources(source_root)[0]
    return source_root, source_path, _SOURCE_BYTES, candidate


def _semantic_call(purpose: str, model: str) -> SemanticCallMetadata:
    return SemanticCallMetadata(
        purpose=purpose,
        provider="legacy-provider",
        model=model,
        agent_id="legacy-agent",
        input_tokens=5,
        output_tokens=3,
    )


def _seed_verified_compiler_v2(
    repository: CanonicalKnowledgeRepository,
    prepared: PreparedSource,
) -> None:
    extraction = prepared.manifest.extraction
    extractor_call = _semantic_call("sedna.semantic.extract", "legacy-extractor")
    critic_call = _semantic_call("sedna.semantic.critic", "legacy-critic")
    manifest = SemanticCompilationManifest(
        source_id=prepared.manifest.source_id,
        source_sha256=prepared.manifest.sha256,
        foundation_schema_version=extraction.schema_version,
        foundation_parser_id=extraction.parser_id,
        foundation_parser_version=extraction.parser_version,
        compiler_version="2",
        extractor_prompt_version=EXTRACTOR_PROMPT_VERSION,
        critic_prompt_version=CRITIC_PROMPT_VERSION,
        repair_prompt_version=REPAIR_PROMPT_VERSION,
        extractor_model_id=extractor_call.model,
        critic_model_id=critic_call.model,
        disposition="verified",
        repair_count=0,
        started_at=_NOW,
        completed_at=_NOW,
    )
    bundle = SemanticKnowledgeBundle(
        schema_version=SEMANTIC_SCHEMA_VERSION,
        source_id=prepared.manifest.source_id,
        source_sha256=prepared.manifest.sha256,
        compilation_manifest=manifest,
    )
    verification = SemanticVerificationRecord(
        source_id=prepared.manifest.source_id,
        source_sha256=prepared.manifest.sha256,
        critic_call=critic_call,
        adjudication="verified",
        recorded_at=_NOW,
    )
    repository.write_semantic_result(
        SemanticCompilationResult(
            disposition="verified",
            bundle=bundle,
            verification=verification,
            calls=(extractor_call, critic_call),
        )
    )


def test_extractor_v2_manifest_reprocesses_once_with_safe_asset_locator(
    tmp_path: Path,
) -> None:
    source_root, source_path, source_bytes, candidate = _source(tmp_path)
    before_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()

    with IngestionPipeline(source_root, tmp_path / "knowledge") as pipeline:
        seeded = pipeline.prepare(candidate)
        assert seeded is not None
        legacy_manifest = seeded.manifest.model_copy(
            update={
                "source_namespace": None,
                "extraction": seeded.manifest.extraction.model_copy(
                    update={"extractor_version": "2"}
                ),
            }
        )
        pipeline.repository.transition_source(legacy_manifest, None)

        migrated = pipeline.prepare(candidate)

        assert migrated is not None
        safe_assets = tuple(asset for segment in migrated.segments for asset in segment.assets)
        assert [asset.target for asset in safe_assets] == ["https://example.test/proof.png"]
        assert migrated.manifest.source_namespace == candidate.source_namespace
        assert migrated.manifest.extraction.extractor_version == "4"
        assert pipeline.repository.load_manifest(candidate.source_id) == migrated.manifest
        assert source_path.read_bytes() == source_bytes
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == before_sha256

        assert pipeline.prepare(candidate) is None
        assert pipeline.last_outcome == "unchanged"
        assert source_path.read_bytes() == source_bytes
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == before_sha256


def test_compiler_v2_bundle_recompiles_once_then_reuses_v3_without_host_calls(
    tmp_path: Path,
) -> None:
    source_root, source_path, source_bytes, candidate = _source(tmp_path)
    before_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()

    with IngestionPipeline(source_root, tmp_path / "knowledge") as pipeline:
        prepared = pipeline.prepare(candidate)
        assert prepared is not None
        _seed_verified_compiler_v2(pipeline.repository, prepared)
        host = _ScriptedHost(
            [
                {
                    "artifacts": [
                        {
                            "draft_type": "reference",
                            "local_id": "migration-reference",
                            "artifact_type": "methodology",
                            "subject": "Evidence comparison",
                            "statement": (
                                "Compare the observed response with the current hypothesis."
                            ),
                            "origin": "explicit",
                            "citations": [{"segment_indexes": [0]}],
                        }
                    ],
                    "ignored_segment_indexes": list(range(1, len(prepared.segments))),
                },
                {"accepted": True, "findings": []},
            ]
        )
        service = SemanticIngestionService(
            pipeline.repository,
            SemanticCompiler(HadesLlmAdapter(host), clock=lambda: _NOW),
        )

        recompiled = service.compile_and_store(prepared)

        assert recompiled.disposition == "verified"
        assert recompiled.bundle is not None
        assert recompiled.bundle.compilation_manifest.compiler_version == SEMANTIC_COMPILER_VERSION
        assert recompiled.bundle.references[0].assessment.independence_group == (
            prepared.manifest.sha256
        )
        assert [call["purpose"] for call in host.calls] == [
            "sedna.semantic.extract",
            "sedna.semantic.critic",
        ]
        assert (
            pipeline.repository.load_semantic_bundle(prepared.manifest.source_id)
            == recompiled.bundle
        )
        assert source_path.read_bytes() == source_bytes
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == before_sha256

        unchanged = service.compile_and_store(prepared)

        assert unchanged.disposition == "unchanged"
        assert unchanged.bundle == recompiled.bundle
        assert unchanged.calls == ()
        assert [call["purpose"] for call in host.calls] == [
            "sedna.semantic.extract",
            "sedna.semantic.critic",
        ]
        assert source_path.read_bytes() == source_bytes
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == before_sha256
