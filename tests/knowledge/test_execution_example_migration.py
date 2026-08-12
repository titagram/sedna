"""Execution-example migration: legacy strategic-only bundles, atomic replacement, lookup."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from sedna.knowledge.inventory import discover_sources
from sedna.knowledge.pipeline import IngestionPipeline
from sedna.knowledge.schema.manifest import foundation_manifest_digest
from sedna.knowledge.schema.semantic import (
    SemanticCallMetadata,
    SemanticCompilationManifest,
    SemanticKnowledgeBundle,
    SemanticVerificationRecord,
)
from sedna.knowledge.semantic.compiler import (
    SEMANTIC_COMPILER_VERSION,
    SEMANTIC_SCHEMA_VERSION,
    SemanticCompiler,
)
from sedna.knowledge.semantic.drafts import (
    CriticVerdict,
    DraftCitation,
    DraftExecutionExample,
    SemanticCompilationResult,
    SemanticDraftBundle,
)
from sedna.knowledge.semantic.llm import HadesLlmAdapter
from sedna.knowledge.semantic.service import SemanticIngestionService

_SOURCE_TEXT = (
    "# Evidence locator migration\n\n## Compare observations\n\n"
    "Inspect the documented response, preserve the observed evidence, and compare "
    "it with the current hypothesis before selecting the next discriminating "
    "observation.\n"
)


_NOW = datetime(2026, 8, 12, 12, 0, 0, tzinfo=UTC)


class _HostResult:
    def __init__(self, parsed: Any, model: str = "host-model") -> None:
        self.parsed = parsed
        self.model = model
        self.usage = {"input_tokens": 5, "output_tokens": 5}
        self.provider = "host-provider"
        self.agent_id = "agent-1"


class _ScriptedHost:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    def complete_structured(self, purpose: str, **_kwargs: object) -> _HostResult:
        self.calls.append(purpose)
        return _HostResult(self._responses.pop(0))


def _draft_with_example() -> SemanticDraftBundle:
    return SemanticDraftBundle(
        ignored_segment_indexes=(1,),
        artifacts=(
            {
                "draft_type": "reference",
                "local_id": "reference-http",
                "origin": "explicit",
                "artifact_type": "methodology",
                "subject": "HTTP service inspection",
                "statement": "Inspect the HTTP response before selecting an action.",
                "citations": (DraftCitation(segment_indexes=(0,)),),
            },
        ),
        execution_examples=(
            DraftExecutionExample(
                local_id="example-1",
                parent_local_id="reference-http",
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
                citations=(DraftCitation(segment_indexes=(0,)),),
            ),
        ),
    )


def _legacy_manifest(
    prepared, *, compiler_version: str, prompt_version: str
) -> SemanticCompilationManifest:
    extraction = prepared.manifest.extraction
    return SemanticCompilationManifest(
        source_id=prepared.manifest.source_id,
        source_sha256=prepared.manifest.sha256,
        foundation_schema_version=extraction.schema_version,
        foundation_parser_id=extraction.parser_id,
        foundation_parser_version=extraction.parser_version,
        foundation_extraction=extraction,
        foundation_manifest_sha256=foundation_manifest_digest(prepared.manifest),
        compiler_version=compiler_version,
        extractor_prompt_version=prompt_version,
        critic_prompt_version=prompt_version,
        repair_prompt_version=prompt_version,
        extractor_model_id="legacy-extractor",
        critic_model_id="legacy-critic",
        disposition="verified",
        repair_count=0,
        started_at=_NOW,
        completed_at=_NOW,
    )


def test_legacy_bundle_is_strategic_only_then_relearns_to_examples(tmp_path: Path) -> None:
    source_root = tmp_path / "raw_src"
    source_path = source_root / "01_Information-Gathering" / "Academy" / "locator-migration.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(_SOURCE_TEXT, encoding="utf-8")
    candidate = discover_sources(source_root)[0]

    with IngestionPipeline(source_root, tmp_path / "knowledge") as pipeline:
        prepared = pipeline.prepare(candidate)
        assert prepared is not None
        # seed a legacy 2.4.0/compiler-8/prompt-1 strategic-only bundle with no examples
        legacy = SemanticKnowledgeBundle(
            schema_version="2.4.0",
            source_id=prepared.manifest.source_id,
            source_sha256=prepared.manifest.sha256,
            compilation_manifest=_legacy_manifest(
                prepared, compiler_version="8", prompt_version="1"
            ),
        )
        critic_call = _critic_call(model="legacy-critic")
        verification = SemanticVerificationRecord(
            source_id=prepared.manifest.source_id,
            source_sha256=prepared.manifest.sha256,
            critic_call=critic_call,
            adjudication="verified",
            recorded_at=_NOW,
        )
        extractor_call = _critic_call(
            purpose="sedna.semantic.extract", model="legacy-extractor"
        )
        pipeline.repository.write_semantic_result(
            SemanticCompilationResult(
                disposition="verified",
                bundle=legacy,
                verification=verification,
                calls=(extractor_call, critic_call),
            )
        )
        # retrieval accepts the exact legacy contract as strategic-only knowledge
        loaded = pipeline.repository.load_semantic_bundle(prepared.manifest.source_id)
        assert loaded.execution_examples == ()
        snapshot = pipeline.repository.semantic_bundle_snapshot()
        assert len(snapshot.bundles) == 1
        # learning currentness rejects it, so one relearn recompiles it
        assert pipeline.repository.semantic_result_is_current(prepared) is False

        # relearn the same source with a v2 extractor response containing one example
        host = _ScriptedHost([_draft_with_example(), _accepting_critic()])
        compiler = SemanticCompiler(HadesLlmAdapter(host), clock=lambda: _NOW)
        service = SemanticIngestionService(pipeline.repository, compiler)
        first = service.compile_and_store(prepared)
        assert first.disposition == "verified"
        assert first.bundle is not None
        assert first.bundle.schema_version == SEMANTIC_SCHEMA_VERSION
        assert len(first.bundle.execution_examples) == 1
        assert first.bundle.compilation_manifest.compiler_version == SEMANTIC_COMPILER_VERSION

        # an identical second learning call returns unchanged with zero host calls
        host2 = _ScriptedHost([])
        compiler2 = SemanticCompiler(HadesLlmAdapter(host2), clock=lambda: _NOW)
        service2 = SemanticIngestionService(pipeline.repository, compiler2)
        second = service2.compile_and_store(prepared)
        assert second.disposition == "unchanged"
        assert host2.calls == []


def test_load_execution_examples_filters_parent_and_requires_exact_set(tmp_path: Path) -> None:
    source_root = tmp_path / "raw_src"
    source_path = source_root / "01_Information-Gathering" / "Academy" / "locator-migration.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(_SOURCE_TEXT, encoding="utf-8")
    candidate = discover_sources(source_root)[0]

    with IngestionPipeline(source_root, tmp_path / "knowledge") as pipeline:
        prepared = pipeline.prepare(candidate)
        assert prepared is not None
        host = _ScriptedHost([_draft_with_example(), _accepting_critic()])
        compiler = SemanticCompiler(HadesLlmAdapter(host), clock=lambda: _NOW)
        service = SemanticIngestionService(pipeline.repository, compiler)
        result = service.compile_and_store(prepared)
        assert result.disposition == "verified" and result.bundle is not None
        example = result.bundle.execution_examples[0]
        parent = example.parent_artifact_id
        loaded = pipeline.repository.load_execution_examples(
            prepared.manifest.source_id, parent_artifact_id=parent
        )
        assert [item.example_id for item in loaded] == [example.example_id]
        with pytest.raises(ValueError):
            pipeline.repository.load_execution_examples(
                prepared.manifest.source_id,
                parent_artifact_id=parent,
                example_ids=("not-the-example",),
            )


def test_atomic_replacement_keeps_old_or_new_bundle_complete(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "raw_src"
    source_path = source_root / "01_Information-Gathering" / "Academy" / "locator-migration.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(_SOURCE_TEXT, encoding="utf-8")
    candidate = discover_sources(source_root)[0]

    with IngestionPipeline(source_root, tmp_path / "knowledge") as pipeline:
        prepared = pipeline.prepare(candidate)
        assert prepared is not None
        host = _ScriptedHost([_draft_with_example(), _accepting_critic()])
        compiler = SemanticCompiler(HadesLlmAdapter(host), clock=lambda: _NOW)
        service = SemanticIngestionService(pipeline.repository, compiler)
        first = service.compile_and_store(prepared)
        assert first.disposition == "verified" and first.bundle is not None
        original_example_ids = {
            example.example_id for example in first.bundle.execution_examples
        }

        # a failing replacement write must leave the complete old bundle intact
        def fail_bundle_write(*args: Any, **kwargs: Any) -> Any:
            raise OSError("simulated write failure")

        monkeypatch.setattr(pipeline.repository, "_write_model", fail_bundle_write)
        host2 = _ScriptedHost([_draft_with_example(), _accepting_critic()])
        compiler2 = SemanticCompiler(HadesLlmAdapter(host2), clock=lambda: _NOW)
        service2 = SemanticIngestionService(pipeline.repository, compiler2)
        outcome = service2.compile_and_store(prepared)
        assert outcome.disposition != "verified"
        monkeypatch.undo()
        recovered = pipeline.repository.load_semantic_bundle(prepared.manifest.source_id)
        recovered_ids = {example.example_id for example in recovered.execution_examples}
        assert recovered_ids == original_example_ids


def _critic_call(*, purpose: str = "sedna.semantic.critic", model: str = "host-model"):
    return SemanticCallMetadata(
        purpose=purpose,
        provider="host-provider",
        model=model,
        agent_id="agent-1",
        input_tokens=5,
        output_tokens=5,
    )


def _accepting_critic():
    return CriticVerdict(accepted=True)
