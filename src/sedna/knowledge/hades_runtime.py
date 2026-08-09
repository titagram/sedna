"""Host-LLM composition root for local Sedna knowledge services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from sedna.knowledge.learning import DocumentLearningService
from sedna.knowledge.repository import CanonicalKnowledgeRepository
from sedna.knowledge.retrieval.maintenance import RetrievalMaintenanceService
from sedna.knowledge.retrieval.service import KnowledgeRetrievalService
from sedna.knowledge.retrieval.sqlite import SQLiteRetrievalIndex
from sedna.knowledge.semantic.compiler import SemanticCompiler
from sedna.knowledge.semantic.llm import HadesLlmAdapter, HostStructuredLlm
from sedna.knowledge.semantic.service import SemanticIngestionService


@dataclass(slots=True)
class HadesKnowledgeRuntime:
    """Own the repository and disposable index used by host-backed learning."""

    learning: DocumentLearningService
    retrieval: KnowledgeRetrievalService
    maintenance: RetrievalMaintenanceService
    _repository: CanonicalKnowledgeRepository
    _index: SQLiteRetrievalIndex
    _closed: bool = field(default=False, init=False, repr=False)

    @classmethod
    def create(cls, host_llm: object, knowledge_root: Path) -> HadesKnowledgeRuntime:
        """Create an owned local runtime using only the host structured-completion facade."""
        host = _require_structured_host(host_llm)
        repository: CanonicalKnowledgeRepository | None = None
        index: SQLiteRetrievalIndex | None = None
        try:
            repository = CanonicalKnowledgeRepository(Path(knowledge_root))
            adapter = HadesLlmAdapter(host)
            compiler = SemanticCompiler(adapter, clock=lambda: datetime.now(UTC))
            semantic = SemanticIngestionService(repository, compiler)
            index = SQLiteRetrievalIndex(repository.root / "indexes" / "retrieval.sqlite")
            maintenance = RetrievalMaintenanceService(repository, index)
            learning = DocumentLearningService(
                knowledge_root=repository.root,
                semantic_service=semantic,
                maintenance=maintenance,
            )
            retrieval = KnowledgeRetrievalService(index)
            return cls(
                learning=learning,
                retrieval=retrieval,
                maintenance=maintenance,
                _repository=repository,
                _index=index,
            )
        except BaseException:
            _close_owned(index, repository)
            raise

    def __enter__(self) -> HadesKnowledgeRuntime:
        if self._closed:
            raise RuntimeError("knowledge runtime is closed")
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def close(self) -> None:
        """Close the disposable index and canonical repository exactly once."""
        if self._closed:
            return
        self._closed = True
        _close_owned(self._index, self._repository)


def _require_structured_host(host_llm: object) -> HostStructuredLlm:
    """Fail closed unless the host exposes the sole allowed semantic LLM entry point."""
    try:
        complete_structured = host_llm.complete_structured  # type: ignore[attr-defined]
    except Exception:
        complete_structured = None
    if not callable(complete_structured):
        raise TypeError("host_llm must provide callable complete_structured")
    return cast(HostStructuredLlm, host_llm)


def _close_owned(
    index: SQLiteRetrievalIndex | None,
    repository: CanonicalKnowledgeRepository | None,
) -> None:
    """Attempt both owned closes once, preserving the first close failure."""
    failure: BaseException | None = None
    for resource in (index, repository):
        if resource is None:
            continue
        try:
            resource.close()
        except BaseException as error:
            if failure is None:
                failure = error
    if failure is not None:
        raise failure


__all__ = ["HadesKnowledgeRuntime"]
