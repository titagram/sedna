"""One-source orchestration for semantic compilation and canonical persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sedna.knowledge.parsing import PreparedSource
from sedna.knowledge.semantic.compiler import (
    SEMANTIC_COMPILER_VERSION,
    SEMANTIC_SCHEMA_VERSION,
    SemanticCompiler,
)
from sedna.knowledge.semantic.drafts import SemanticCompilationResult
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
        if self._repository.semantic_result_is_current(
            prepared,
            semantic_schema_version=SEMANTIC_SCHEMA_VERSION,
            extractor_prompt_version=EXTRACTOR_PROMPT_VERSION,
            critic_prompt_version=CRITIC_PROMPT_VERSION,
            repair_prompt_version=REPAIR_PROMPT_VERSION,
            compiler_version=SEMANTIC_COMPILER_VERSION,
        ):
            source_id = prepared.manifest.source_id
            return SemanticCompilationResult(
                disposition="unchanged",
                bundle=self._repository.load_semantic_bundle(source_id),
                verification=self._repository.load_semantic_verification(source_id),
            )

        result = self._compiler.compile(prepared)
        if result.disposition in {"verified", "quarantined"}:
            self._repository.write_semantic_result(result)
        return result


__all__ = ["SemanticIngestionService"]
