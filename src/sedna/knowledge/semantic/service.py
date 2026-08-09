"""One-source orchestration for semantic compilation and canonical persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sedna.knowledge.parsing import PreparedSource
from sedna.knowledge.parsing.models import validate_prepared_source
from sedna.knowledge.schema import SemanticCompilationManifest
from sedna.knowledge.semantic.compiler import (
    SEMANTIC_COMPILER_VERSION,
    SEMANTIC_SCHEMA_VERSION,
    SemanticCompiler,
)
from sedna.knowledge.semantic.drafts import (
    CANONICAL_COMPILATION_FAILURE_MESSAGES,
    SemanticCompilationResult,
)
from sedna.knowledge.semantic.prompts import (
    CRITIC_PROMPT_VERSION,
    EXTRACTOR_PROMPT_VERSION,
    REPAIR_PROMPT_VERSION,
)

if TYPE_CHECKING:
    from sedna.knowledge.repository import CanonicalKnowledgeRepository


class SemanticIngestionService:
    """Compile and persist one prepared source, reusing current verified state."""

    def __init__(
        self,
        repository: CanonicalKnowledgeRepository,
        compiler: SemanticCompiler,
    ) -> None:
        self._repository = repository
        self._compiler = compiler

    def compile_and_store(self, prepared: PreparedSource) -> SemanticCompilationResult:
        """Return current canonical semantics or compile and persist one stale source."""
        try:
            prepared = validate_prepared_source(prepared)
        except (TypeError, ValueError):
            return SemanticCompilationResult(
                disposition="failed",
                failure_code="invalid_input",
                failure_message=CANONICAL_COMPILATION_FAILURE_MESSAGES["invalid_input"],
            )
        source_id = prepared.manifest.source_id
        with self._repository.semantic_compilation_guard(source_id):
            current = self._repository.load_current_semantic_result(
                prepared,
                semantic_schema_version=SEMANTIC_SCHEMA_VERSION,
                extractor_prompt_version=EXTRACTOR_PROMPT_VERSION,
                critic_prompt_version=CRITIC_PROMPT_VERSION,
                repair_prompt_version=REPAIR_PROMPT_VERSION,
                compiler_version=SEMANTIC_COMPILER_VERSION,
            )
            if current is not None:
                return current

            result = self._compiler.compile(prepared)
            if result.disposition in {"verified", "quarantined"}:
                if result.disposition == "quarantined":
                    result = self._with_quarantine_manifest(prepared, result)
                self._repository.write_semantic_result(result)
            return result

    def is_current(self, prepared: PreparedSource) -> bool:
        """Check semantic currentness against this service's exact compiler contract."""
        try:
            prepared = validate_prepared_source(prepared)
        except (TypeError, ValueError):
            return False
        return self._repository.semantic_result_is_current(
            prepared,
            semantic_schema_version=SEMANTIC_SCHEMA_VERSION,
            extractor_prompt_version=EXTRACTOR_PROMPT_VERSION,
            critic_prompt_version=CRITIC_PROMPT_VERSION,
            repair_prompt_version=REPAIR_PROMPT_VERSION,
            compiler_version=SEMANTIC_COMPILER_VERSION,
        )

    @staticmethod
    def _with_quarantine_manifest(
        prepared: PreparedSource,
        result: SemanticCompilationResult,
    ) -> SemanticCompilationResult:
        quarantine = result.quarantine
        verification = result.verification
        extractor = next(
            (call for call in result.calls if call.purpose == "sedna.semantic.extract"),
            None,
        )
        if quarantine is None or verification is None or extractor is None:
            return result
        now = datetime.now(UTC)
        manifest = SemanticCompilationManifest(
            source_id=prepared.manifest.source_id,
            source_sha256=prepared.manifest.sha256,
            foundation_schema_version=prepared.manifest.extraction.schema_version,
            foundation_parser_id=prepared.manifest.extraction.parser_id,
            foundation_parser_version=prepared.manifest.extraction.parser_version,
            compiler_version=SEMANTIC_COMPILER_VERSION,
            extractor_prompt_version=EXTRACTOR_PROMPT_VERSION,
            critic_prompt_version=CRITIC_PROMPT_VERSION,
            repair_prompt_version=REPAIR_PROMPT_VERSION,
            extractor_model_id=extractor.model,
            critic_model_id=verification.critic_call.model,
            disposition="quarantined",
            repair_count=1 if len(result.calls) == 4 else 0,
            started_at=now,
            completed_at=now,
        )
        return result.model_copy(
            update={
                "quarantine": quarantine.model_copy(
                    update={
                        "compilation_manifest": manifest,
                        "semantic_schema_version": SEMANTIC_SCHEMA_VERSION,
                    }
                )
            }
        )


__all__ = ["SemanticIngestionService"]
