"""One-source orchestration for semantic compilation and canonical persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sedna.knowledge.parsing import PreparedSource
from sedna.knowledge.parsing.models import validate_prepared_source
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
                try:
                    manifest = self._repository.load_manifest(source_id)
                except (FileNotFoundError, ValueError):
                    return SemanticCompilationResult(
                        disposition="failed",
                        failure_code="internal_failure",
                        failure_message=CANONICAL_COMPILATION_FAILURE_MESSAGES["internal_failure"],
                    )
                if (
                    manifest.ingestion_status.value != "accepted"
                    or manifest.sha256 != prepared.manifest.sha256
                ):
                    return SemanticCompilationResult(
                        disposition="failed",
                        failure_code="internal_failure",
                        failure_message=CANONICAL_COMPILATION_FAILURE_MESSAGES["internal_failure"],
                    )
                if result.disposition == "quarantined":
                    result = self._with_quarantine_manifest(prepared, result)
                self._repository.write_semantic_result(result)
            elif result.disposition == "failed":
                try:
                    self._repository.invalidate_failed_semantic_result(prepared)
                except Exception:
                    return SemanticCompilationResult(
                        disposition="failed",
                        failure_code="internal_failure",
                        failure_message=CANONICAL_COMPILATION_FAILURE_MESSAGES["internal_failure"],
                    )
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
        if quarantine is not None and quarantine.compilation_manifest is not None:
            return result
        return SemanticCompilationResult(
            disposition="failed",
            failure_code="internal_failure",
            failure_message=CANONICAL_COMPILATION_FAILURE_MESSAGES["internal_failure"],
        )


__all__ = ["SemanticIngestionService"]
