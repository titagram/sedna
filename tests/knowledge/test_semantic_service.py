"""End-to-end tests for one-source semantic compilation and persistence."""

from __future__ import annotations

import copy
import hashlib
import html
import json
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import unquote

import pytest

import sedna.knowledge.semantic.compiler as compiler_module
import sedna.knowledge.semantic.service as service_module
from sedna.knowledge import IngestionPipeline
from sedna.knowledge import SemanticIngestionService as PublicSemanticService
from sedna.knowledge.inventory import discover_sources
from sedna.knowledge.parsing import PreparedSource
from sedna.knowledge.repository import CanonicalKnowledgeRepository
from sedna.knowledge.semantic import SemanticCompiler, SemanticIngestionService
from sedna.knowledge.semantic.llm import HadesLlmAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "semantic"
NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
SOURCE_CREDENTIAL = 'password: "CaseOnly!23"'
RAW_FLAG = "HTB{semantic_raw_final}"
ENCODED_FLAG = "HTB%257Bsemantic_encoded_final%257D"
ROOT_FLAG = "abcdef0123456789abcdef0123456789"
USER_FLAG = "0123456789abcdef0123456789abcdef"
SENSITIVE_SOURCE_SUFFIX = f"""

We tested the documented boundary. We observed the recorded result.
The final result was {RAW_FLAG}; an encoded copy was {ENCODED_FLAG}.
Root flag: {ROOT_FLAG}. User flag: {USER_FLAG}.
"""


@dataclass(frozen=True)
class _SourceCase:
    relative_path: str
    fixture_name: str
    markdown: str


SOURCE_CASES = {
    "reference": _SourceCase(
        relative_path="01_Information-Gathering/Academy/evidence-methodology.md",
        fixture_name="reference-methodology.json",
        markdown=(
            """# Evidence-led investigation

Record observations separately from assumptions and state a falsifiable hypothesis before
selecting the smallest discriminating observation. We inspected the available evidence before
choosing that observation.

## Negative evidence and stopping — Root flag audit

A failed observation can weaken a branch without proving that a service is absent. Stop repeating
an action when it adds no new evidence, then revisit the assumptions that could explain the result.
"""
            + SENSITIVE_SOURCE_SUFFIX
        ),
    ),
    "windows": _SourceCase(
        relative_path="Write-ups/Machines/WindowsRouting/walkthrough.md",
        fixture_name="windows-walkthrough.json",
        markdown=(
            """# Windows service observation

We observed that the server runs Windows. A generic application response changed when the recorded
host-routing context was supplied, confirming that routing was a prerequisite.

## Compatibility boundary — Root flag audit

This case is incompatible with Linux because the observed service behavior is Windows-specific.
Validate the operating system and routing behavior before transferring the strategy.
"""
            + SENSITIVE_SOURCE_SUFFIX
        ),
    ),
    "hybrid": _SourceCase(
        relative_path="Write-ups/Machines/HybridEvidence/walkthrough.md",
        fixture_name="hybrid-reference-case.json",
        markdown=(
            f"""# Credential evidence interpretation

A source-authored credential literal is historical case evidence, not a credential for another
target. This case recorded {SOURCE_CREDENTIAL} only to explain an authentication attempt.

## Failed authentication case — Root flag audit

We tried the recorded credential example; it was rejected and no session was created. We
preserved that negative evidence, stopped repeating the authentication path, and investigated a
different hypothesis without reusing the exact literal.
"""
            + SENSITIVE_SOURCE_SUFFIX
        ),
    ),
    "repair": _SourceCase(
        relative_path="Write-ups/Machines/Architecture/walkthrough.md",
        fixture_name="context-repair.json",
        markdown=(
            """# Architecture-dependent strategy

We inspected the execution context to confirm the relevant architecture before selecting an
architecture-dependent strategy.

## Required context — Root flag audit

The source explicitly records x86_64 as the required execution architecture. Strategies for an
incompatible architecture must be excluded.
"""
            + SENSITIVE_SOURCE_SUFFIX
        ),
    ),
}


@dataclass
class _Usage:
    input_tokens: int = 23
    output_tokens: int = 17


@dataclass
class _HostResult:
    parsed: object
    provider: str
    model: str
    agent_id: str = "scripted-agent"
    usage: _Usage = field(default_factory=_Usage)
    audit: object = None


class _ScriptedHost:
    """Return fixture payloads through the real Hades adapter contract."""

    def __init__(self, responses: list[object]) -> None:
        self.responses = iter(responses)
        self.calls: list[dict[str, Any]] = []

    def complete_structured(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        try:
            response = next(self.responses)
        except StopIteration as error:
            raise AssertionError("scripted host received an unexpected semantic call") from error
        if isinstance(response, Exception):
            raise response
        purpose = str(kwargs["purpose"]).rsplit(".", maxsplit=1)[-1]
        return _HostResult(
            parsed=response,
            provider="scripted-provider",
            model=f"scripted-{purpose}",
        )


class _BlockingScriptedHost(_ScriptedHost):
    """Pause the first LLM call so a competing service reaches the source guard."""

    def __init__(self, responses: list[object], entered: Event, release: Event) -> None:
        super().__init__(responses)
        self._entered = entered
        self._release = release

    def complete_structured(self, **kwargs: Any) -> object:
        if not self.calls:
            self._entered.set()
            assert self._release.wait(5)
        return super().complete_structured(**kwargs)


def _load_responses(name: str) -> list[object]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return payload


def _service(
    repository: CanonicalKnowledgeRepository,
    responses: list[object],
) -> tuple[SemanticIngestionService, _ScriptedHost]:
    host = _ScriptedHost(responses)
    compiler = SemanticCompiler(HadesLlmAdapter(host), clock=lambda: NOW)
    return SemanticIngestionService(repository, compiler), host


@contextmanager
def _prepared_case(
    tmp_path: Path,
    case_name: str,
) -> Iterator[tuple[IngestionPipeline, PreparedSource, Path, bytes]]:
    case = SOURCE_CASES[case_name]
    source_root = tmp_path / case_name / "raw_src"
    source_path = source_root / case.relative_path
    source_path.parent.mkdir(parents=True)
    source_bytes = case.markdown.encode("utf-8")
    source_path.write_bytes(source_bytes)
    candidate = discover_sources(source_root)[0]

    with IngestionPipeline(source_root, tmp_path / case_name / "knowledge") as pipeline:
        prepared = pipeline.prepare(candidate)
        assert prepared is not None
        assert len(prepared.segments) == 2
        yield pipeline, prepared, source_path, source_bytes


def _purposes(host: _ScriptedHost) -> list[str]:
    return [str(call["purpose"]) for call in host.calls]


def _recursively_decode(value: str) -> str:
    current = value
    for _ in range(12):
        decoded = unquote(html.unescape(current))
        if decoded == current:
            return decoded
        current = decoded
    return current


def test_service_is_exported_from_semantic_and_knowledge_packages() -> None:
    assert PublicSemanticService is SemanticIngestionService


def test_exact_four_fixtures_are_direct_structured_model_responses() -> None:
    fixtures = tuple(sorted(path.name for path in FIXTURES.glob("*.json")))

    assert fixtures == (
        "context-repair.json",
        "hybrid-reference-case.json",
        "reference-methodology.json",
        "windows-walkthrough.json",
    )
    for fixture in fixtures:
        responses = _load_responses(fixture)
        assert len(responses) in {2, 4}
        for response in responses:
            assert isinstance(response, dict)
            assert set(response) in (
                {"artifacts", "ignored_segment_indexes"},
                {"accepted", "findings"},
            )
            assert not {
                "schema_version",
                "source_id",
                "source_sha256",
                "compilation_manifest",
                "references",
                "cases",
                "guidance",
            } & set(response)


@pytest.mark.parametrize(
    ("case_name", "expected_document_type", "expected_purposes"),
    (
        ("reference", "lesson", ["sedna.semantic.extract", "sedna.semantic.critic"]),
        ("windows", "machine_walkthrough", ["sedna.semantic.extract", "sedna.semantic.critic"]),
        ("hybrid", "machine_walkthrough", ["sedna.semantic.extract", "sedna.semantic.critic"]),
        (
            "repair",
            "machine_walkthrough",
            [
                "sedna.semantic.extract",
                "sedna.semantic.critic",
                "sedna.semantic.repair",
                "sedna.semantic.critic",
            ],
        ),
    ),
)
def test_real_pipeline_compiler_and_repository_store_each_golden_response(
    tmp_path: Path,
    case_name: str,
    expected_document_type: str,
    expected_purposes: list[str],
) -> None:
    case = SOURCE_CASES[case_name]
    with _prepared_case(tmp_path, case_name) as (pipeline, prepared, _, _):
        service, host = _service(pipeline.repository, _load_responses(case.fixture_name))

        result = service.compile_and_store(prepared)

        assert result.disposition == "verified"
        assert prepared.manifest.document_type == expected_document_type
        assert result.bundle is not None
        assert result.verification is not None
        assert _purposes(host) == expected_purposes
        assert (
            pipeline.repository.load_semantic_bundle(prepared.manifest.source_id) == result.bundle
        )
        assert (
            pipeline.repository.load_semantic_verification(prepared.manifest.source_id)
            == result.verification
        )
        assert pipeline.repository.semantic_result_is_current(prepared)


def test_windows_case_records_windows_and_an_explicit_linux_incompatibility(
    tmp_path: Path,
) -> None:
    with _prepared_case(tmp_path, "windows") as (pipeline, prepared, _, _):
        service, _ = _service(
            pipeline.repository,
            _load_responses(SOURCE_CASES["windows"].fixture_name),
        )

        result = service.compile_and_store(prepared)

        assert result.bundle is not None
        case = result.bundle.cases[0]
        windows = case.applicability.typed_context.os_family
        assert windows is not None
        assert (windows.value, windows.relation, windows.origin) == (
            "windows",
            "observed",
            "explicit",
        )
        linux = next(
            facet.assertion
            for facet in case.applicability.facets
            if (facet.namespace, facet.key, facet.assertion.value)
            == ("platform", "os_family", "linux")
        )
        assert linux.relation == "incompatible"


def test_hybrid_response_emits_reference_and_case_and_keeps_credentials_case_local(
    tmp_path: Path,
) -> None:
    with _prepared_case(tmp_path, "hybrid") as (pipeline, prepared, _, _):
        service, host = _service(
            pipeline.repository,
            _load_responses(SOURCE_CASES["hybrid"].fixture_name),
        )

        result = service.compile_and_store(prepared)

        assert result.bundle is not None
        assert len(result.bundle.references) == len(result.bundle.cases) == 1
        extractor_call = host.calls[0]
        extractor_input = extractor_call["input"][0]["text"]
        extractor_payload = json.loads(extractor_input)
        normalized_prompt = " ".join(str(extractor_call["instructions"]).casefold().split())
        assert SOURCE_CREDENTIAL in extractor_payload["segments"][0]["text"]
        assert "case-local example" in normalized_prompt
        assert "truth is irrelevant" in normalized_prompt
        assert (
            "never promote it to a credential for a current or future target" in normalized_prompt
        )
        assert "CaseOnly!23" not in result.bundle.model_dump_json()


def test_material_architecture_omission_is_repaired_once_with_cited_context(
    tmp_path: Path,
) -> None:
    with _prepared_case(tmp_path, "repair") as (pipeline, prepared, _, _):
        service, host = _service(
            pipeline.repository,
            _load_responses(SOURCE_CASES["repair"].fixture_name),
        )

        result = service.compile_and_store(prepared)

        assert result.bundle is not None
        assert result.bundle.compilation_manifest.repair_count == 1
        architecture = result.bundle.references[0].applicability.typed_context.cpu_architecture
        assert architecture is not None
        assert (architecture.value, architecture.relation) == ("x86_64", "required")
        assert architecture.source_refs[0].location.section == (
            "Architecture-dependent strategy > Required context — Root flag audit"
        )
        assert _purposes(host).count("sedna.semantic.repair") == 1


def test_current_second_pass_loads_typed_unchanged_result_without_an_llm_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _prepared_case(tmp_path, "reference") as (pipeline, prepared, _, _):
        service, host = _service(
            pipeline.repository,
            _load_responses(SOURCE_CASES["reference"].fixture_name),
        )
        verified = service.compile_and_store(prepared)

        def reject_split_load(*args: object, **kwargs: object) -> object:
            raise AssertionError("service must use one atomic current-result snapshot")

        monkeypatch.setattr(pipeline.repository, "semantic_result_is_current", reject_split_load)
        monkeypatch.setattr(pipeline.repository, "load_semantic_bundle", reject_split_load)
        monkeypatch.setattr(pipeline.repository, "load_semantic_verification", reject_split_load)

        unchanged = service.compile_and_store(prepared)

        assert unchanged.disposition == "unchanged"
        assert unchanged.bundle == verified.bundle
        assert unchanged.verification == verified.verification
        assert unchanged.calls == ()
        assert _purposes(host) == ["sedna.semantic.extract", "sedna.semantic.critic"]


@pytest.mark.parametrize("corrupt_field", ("document", "segments"))
def test_service_deeply_rejects_constructed_prepared_source_before_lock_or_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corrupt_field: str,
) -> None:
    """Would fail if a valid-looking manifest let corrupt nested input return unchanged."""
    with _prepared_case(tmp_path, "reference") as (pipeline, prepared, _, _):
        service, host = _service(
            pipeline.repository,
            _load_responses(SOURCE_CASES["reference"].fixture_name),
        )
        assert service.compile_and_store(prepared).disposition == "verified"
        corruption = {
            "document": {"document": "corrupt", "segments": ()},
            "segments": {"document": prepared.document, "segments": ("corrupt",)},
        }[corrupt_field]
        constructed = PreparedSource.model_construct(
            manifest=prepared.manifest,
            **corruption,
        )
        guard_calls = 0
        load_calls = 0
        real_guard = pipeline.repository.semantic_compilation_guard
        real_load = pipeline.repository.load_current_semantic_result

        @contextmanager
        def recording_guard(source_id: str) -> Iterator[None]:
            nonlocal guard_calls
            guard_calls += 1
            with real_guard(source_id):
                yield

        def recording_load(*args: object, **kwargs: object) -> object:
            nonlocal load_calls
            load_calls += 1
            return real_load(*args, **kwargs)

        monkeypatch.setattr(pipeline.repository, "semantic_compilation_guard", recording_guard)
        monkeypatch.setattr(pipeline.repository, "load_current_semantic_result", recording_load)

        result = service.compile_and_store(constructed)

        assert result.disposition == "failed"
        assert result.failure_code == "invalid_input"
        assert guard_calls == load_calls == 0
        assert _purposes(host) == ["sedna.semantic.extract", "sedna.semantic.critic"]


def test_concurrent_stale_services_compile_once_and_return_coherent_results(
    tmp_path: Path,
) -> None:
    responses = _load_responses(SOURCE_CASES["reference"].fixture_name)
    with _prepared_case(tmp_path, "reference") as (pipeline, prepared, _, _):
        competing_repository = CanonicalKnowledgeRepository(pipeline.repository.root)
        extractor_entered = Event()
        release_extractor = Event()
        competing_host = _ScriptedHost(copy.deepcopy(responses))
        winning_host = _BlockingScriptedHost(
            copy.deepcopy(responses), extractor_entered, release_extractor
        )
        winning_service = SemanticIngestionService(
            pipeline.repository,
            SemanticCompiler(HadesLlmAdapter(winning_host), clock=lambda: NOW),
        )
        competing_service = SemanticIngestionService(
            competing_repository,
            SemanticCompiler(HadesLlmAdapter(competing_host), clock=lambda: NOW),
        )
        competing_started = Event()

        def compile_competing() -> object:
            competing_started.set()
            return competing_service.compile_and_store(prepared)

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                winning_future = executor.submit(winning_service.compile_and_store, prepared)
                assert extractor_entered.wait(5)
                competing_future = executor.submit(compile_competing)
                assert competing_started.wait(5)
                assert not competing_host.calls
                assert not competing_future.done()
                release_extractor.set()
                winning = winning_future.result(timeout=5)
                competing = competing_future.result(timeout=5)
        finally:
            competing_repository.close()

        assert (winning.disposition, competing.disposition) == ("verified", "unchanged")
        assert len(winning_host.calls) + len(competing_host.calls) == 2
        assert competing.bundle == winning.bundle
        assert competing.verification == winning.verification
        assert competing.calls == ()


@pytest.mark.parametrize(
    ("service_name", "compiler_name"),
    (
        ("EXTRACTOR_PROMPT_VERSION", "EXTRACTOR_PROMPT_VERSION"),
        ("SEMANTIC_COMPILER_VERSION", "SEMANTIC_COMPILER_VERSION"),
    ),
)
def test_prompt_or_compiler_version_change_forces_one_recompile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service_name: str,
    compiler_name: str,
) -> None:
    responses = _load_responses(SOURCE_CASES["reference"].fixture_name)
    with _prepared_case(tmp_path, "reference") as (pipeline, prepared, _, _):
        service, host = _service(pipeline.repository, [*copy.deepcopy(responses), *responses])
        first = service.compile_and_store(prepared)
        assert first.bundle is not None

        monkeypatch.setattr(service_module, service_name, "next-version")
        monkeypatch.setattr(compiler_module, compiler_name, "next-version")
        recompiled = service.compile_and_store(prepared)

        assert recompiled.disposition == "verified"
        assert recompiled.bundle is not None
        manifest = recompiled.bundle.compilation_manifest
        assert (
            getattr(
                manifest,
                "extractor_prompt_version"
                if service_name == "EXTRACTOR_PROMPT_VERSION"
                else "compiler_version",
            )
            == "next-version"
        )
        assert _purposes(host) == [
            "sedna.semantic.extract",
            "sedna.semantic.critic",
            "sedna.semantic.extract",
            "sedna.semantic.critic",
        ]


def test_quarantined_result_is_persisted_without_a_bundle(tmp_path: Path) -> None:
    responses = _load_responses(SOURCE_CASES["repair"].fixture_name)
    responses[-1] = copy.deepcopy(responses[1])
    with _prepared_case(tmp_path, "repair") as (pipeline, prepared, _, _):
        service, _ = _service(pipeline.repository, responses)

        result = service.compile_and_store(prepared)

        assert result.disposition == "quarantined"
        assert result.quarantine is not None
        assert (
            pipeline.repository.load_semantic_quarantine(prepared.manifest.source_id)
            == result.quarantine
        )
        with pytest.raises(FileNotFoundError):
            pipeline.repository.load_semantic_bundle(prepared.manifest.source_id)


def test_current_quarantine_is_reused_without_host_calls(tmp_path: Path) -> None:
    responses = _load_responses(SOURCE_CASES["repair"].fixture_name)
    responses[-1] = copy.deepcopy(responses[1])
    with _prepared_case(tmp_path, "repair") as (pipeline, prepared, _, _):
        service, host = _service(pipeline.repository, responses)

        quarantined = service.compile_and_store(prepared)
        unchanged = service.compile_and_store(prepared)

        assert quarantined.disposition == "quarantined"
        assert quarantined.quarantine is not None
        assert quarantined.quarantine.compilation_manifest is not None
        assert quarantined.quarantine.compilation_manifest.started_at == NOW
        assert quarantined.quarantine.compilation_manifest.completed_at == NOW
        assert unchanged.disposition == "unchanged"
        assert unchanged.quarantine == quarantined.quarantine
        assert len(host.calls) == 4


def test_tampered_quarantine_compilation_attribution_is_not_current(
    tmp_path: Path,
) -> None:
    responses = _load_responses(SOURCE_CASES["repair"].fixture_name)
    responses[-1] = copy.deepcopy(responses[1])
    with _prepared_case(tmp_path, "repair") as (pipeline, prepared, _, _):
        service, _ = _service(pipeline.repository, responses)
        quarantined = service.compile_and_store(prepared)
        assert quarantined.quarantine is not None
        path = (
            pipeline.repository.root / "semantic_quarantine" / f"{prepared.manifest.source_id}.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["compilation_manifest"]["critic_model_id"] = "tampered-model"
        path.write_text(json.dumps(payload), encoding="utf-8")

        assert service.is_current(prepared) is False


def test_repaired_unsafe_material_quarantine_rejects_repair_count_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = _load_responses(SOURCE_CASES["repair"].fixture_name)
    with _prepared_case(tmp_path, "repair") as (pipeline, prepared, _, _):
        service, host = _service(pipeline.repository, responses)

        def reject_unsafe(*args: object, **kwargs: object) -> tuple[object, ...]:
            raise ValueError("unsafe canonical material")

        monkeypatch.setattr(compiler_module, "materialize_bundle", reject_unsafe)
        quarantined = service.compile_and_store(prepared)
        unchanged = service.compile_and_store(prepared)

        assert quarantined.disposition == "quarantined"
        assert quarantined.verification is not None
        assert quarantined.verification.repair_count == 1
        assert quarantined.quarantine is not None
        assert quarantined.quarantine.compilation_manifest is not None
        assert quarantined.quarantine.compilation_manifest.repair_count == 1
        assert unchanged.disposition == "unchanged"
        assert unchanged.quarantine == quarantined.quarantine
        assert len(host.calls) == 4

        path = (
            pipeline.repository.root / "semantic_quarantine" / f"{prepared.manifest.source_id}.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["compilation_manifest"]["repair_count"] = 0
        path.write_text(json.dumps(payload), encoding="utf-8")

        assert service.is_current(prepared) is False


def test_exclusion_waits_for_blocked_compile_then_invalidates_its_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _prepared_case(tmp_path, "reference") as (pipeline, prepared, source_path, _):
        service, _ = _service(
            pipeline.repository,
            _load_responses(SOURCE_CASES["reference"].fixture_name),
        )
        entered = Event()
        release = Event()
        real_current = pipeline.repository.load_current_semantic_result

        def block_after_current(*args: object, **kwargs: object) -> object:
            result = real_current(*args, **kwargs)
            entered.set()
            assert release.wait(5)
            return result

        monkeypatch.setattr(
            pipeline.repository,
            "load_current_semantic_result",
            block_after_current,
        )
        source_path.write_bytes(b"")
        source_root = pipeline.source_root

        def exclude_from_second_repository() -> object:
            with IngestionPipeline(source_root, pipeline.repository.root) as competing:
                candidate = discover_sources(source_root)[0]
                return competing.prepare(candidate)

        with ThreadPoolExecutor(max_workers=2) as executor:
            compiling = executor.submit(service.compile_and_store, prepared)
            assert entered.wait(5)
            excluding = executor.submit(exclude_from_second_repository)
            assert not excluding.done()
            release.set()
            assert compiling.result(timeout=5).disposition == "verified"
            assert excluding.result(timeout=5) is None

        with pytest.raises(FileNotFoundError):
            pipeline.repository.load_semantic_bundle(prepared.manifest.source_id)


def test_failed_compile_serializes_before_a_competing_foundation_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed compiler must release its invalidation before a source transition can proceed."""
    with _prepared_case(tmp_path, "reference") as (pipeline, prepared, source_path, _):
        verified_service, _ = _service(
            pipeline.repository,
            _load_responses(SOURCE_CASES["reference"].fixture_name),
        )
        assert verified_service.compile_and_store(prepared).disposition == "verified"
        source_path.write_text(source_path.read_text(encoding="utf-8") + "\nChanged evidence.\n")
        source_root = pipeline.source_root
        with IngestionPipeline(source_root, pipeline.repository.root) as refresher:
            refreshed = refresher.prepare(discover_sources(source_root)[0])
            assert refreshed is not None
        failed_service, _ = _service(
            pipeline.repository,
            [RuntimeError("host transport failure")],
        )
        entered = Event()
        release = Event()
        real_current = pipeline.repository.load_current_semantic_result

        def block_after_current(*args: object, **kwargs: object) -> object:
            result = real_current(*args, **kwargs)
            entered.set()
            assert release.wait(5)
            return result

        monkeypatch.setattr(
            pipeline.repository,
            "load_current_semantic_result",
            block_after_current,
        )
        source_path.write_bytes(b"")

        def exclude_from_second_repository() -> object:
            with IngestionPipeline(source_root, pipeline.repository.root) as competing:
                return competing.prepare(discover_sources(source_root)[0])

        with ThreadPoolExecutor(max_workers=2) as executor:
            compiling = executor.submit(failed_service.compile_and_store, refreshed)
            assert entered.wait(5)
            excluding = executor.submit(exclude_from_second_repository)
            assert not excluding.done()
            release.set()
            assert compiling.result(timeout=5).disposition == "failed"
            assert excluding.result(timeout=5) is None

        assert (
            pipeline.repository.load_manifest(prepared.manifest.source_id).ingestion_status.value
            == "excluded"
        )
        with pytest.raises(FileNotFoundError):
            pipeline.repository.load_semantic_bundle(prepared.manifest.source_id)


def test_failed_result_remains_run_local_and_is_retried_by_another_instance(
    tmp_path: Path,
) -> None:
    with _prepared_case(tmp_path, "reference") as (pipeline, prepared, _, _):
        failed_service, _ = _service(
            pipeline.repository,
            [RuntimeError("raw response HTB{must-not-persist}")],
        )

        failed = failed_service.compile_and_store(prepared)

        assert failed.disposition == "failed"
        for loader in (
            pipeline.repository.load_semantic_bundle,
            pipeline.repository.load_semantic_verification,
            pipeline.repository.load_semantic_quarantine,
        ):
            with pytest.raises(FileNotFoundError):
                loader(prepared.manifest.source_id)

        retry_repository = CanonicalKnowledgeRepository(pipeline.repository.root)
        try:
            retry_service, retry_host = _service(
                retry_repository,
                _load_responses(SOURCE_CASES["reference"].fixture_name),
            )
            retried = retry_service.compile_and_store(prepared)
            assert retried.disposition == "verified"
            assert _purposes(retry_host) == ["sedna.semantic.extract", "sedna.semantic.critic"]
        finally:
            retry_repository.close()


@pytest.mark.parametrize("case_name", tuple(SOURCE_CASES))
def test_serialized_canonical_outputs_exclude_flags_and_raw_source_is_immutable(
    tmp_path: Path,
    case_name: str,
) -> None:
    case = SOURCE_CASES[case_name]
    with _prepared_case(tmp_path, case_name) as (pipeline, prepared, source_path, source_bytes):
        before_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        assert before_hash == prepared.manifest.sha256
        assert RAW_FLAG.encode() in source_bytes
        assert ENCODED_FLAG.encode() in source_bytes
        assert ROOT_FLAG.encode() in source_bytes
        assert USER_FLAG.encode() in source_bytes
        service, _ = _service(pipeline.repository, _load_responses(case.fixture_name))

        result = service.compile_and_store(prepared)

        assert result.bundle is not None
        assert result.verification is not None
        canonical = json.dumps(
            {
                "bundle": result.bundle.model_dump(mode="json"),
                "verification": result.verification.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        decoded = _recursively_decode(canonical).casefold()
        assert "htb{" not in decoded
        assert "semantic_raw_final" not in decoded
        assert "semantic_encoded_final" not in decoded
        assert ROOT_FLAG not in decoded
        assert USER_FLAG not in decoded
        assert not re.search(r"\b(?:root|user)\s+flag\W+[0-9a-f]{32}\b", decoded)
        assert source_path.read_bytes() == source_bytes
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == before_hash
