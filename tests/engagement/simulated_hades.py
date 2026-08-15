"""Simulated Hades host driving the real Sedna plugin surface for acceptance."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sedna.engagement import EngagementJournalService
from sedna.planning.models import InterpretationSubject, ObservationBatchDraft
from sedna.plugin import register


@dataclass(frozen=True)
class _Result:
    engagement_id: UUID | None = None
    status: str | None = None
    closure_ready: bool | None = None


class _SimulatedContext:
    def __init__(self, knowledge_root: Path) -> None:
        self.sedna_knowledge_root = knowledge_root
        self.tools: list[dict[str, Any]] = []
        self.hooks: dict[str, Any] = {}
        self.llm = self

    def register_tool(self, **kwargs: Any) -> None:
        self.tools.append(kwargs)

    def register_hook(self, name: str, callback) -> None:
        self.hooks[name] = callback

    def complete_structured(self, **kwargs: Any) -> object:
        payload = json.loads(kwargs["input"][0]["text"])
        evidence = payload["evidence_slices"][0]
        return SimpleNamespace(
            parsed=ObservationBatchDraft(
                subject=InterpretationSubject(
                    attachment_event_id=evidence["event_id"],
                    evidence_id=evidence["evidence_id"],
                )
            ),
            provider="simulated",
            model="simulated",
            agent_id="simulated-hades",
            usage={"input_tokens": 1, "output_tokens": 1},
        )


class SimulatedHades:
    """Calls the real plugin handlers and hook callbacks; never shells out."""

    def __init__(self, knowledge_root: Path) -> None:
        self.knowledge_root = knowledge_root
        self._context = _SimulatedContext(knowledge_root)
        register(self._context)
        self._tools = {item["name"]: item for item in self._context.tools}
        self._hooks = self._context.hooks
        self._engagement_id: UUID | None = None

    # -- driver methods ---------------------------------------------------

    def create(
        self,
        *,
        name: str,
        objective: str,
        target: str,
        required_proofs: tuple[str, ...],
        session_id: str,
        task_id: str = "root",
    ) -> UUID:
        result = self._call(
            "sedna_manage_engagement",
            {
                "action": "create",
                "display_name": name,
                "objective": objective,
                "authorization": (target,),
                "required_proofs": tuple(
                    {
                        "proof_id": proof_id,
                        "kind": "flag",
                        "description": f"Required proof {proof_id}",
                    }
                    for proof_id in required_proofs
                ),
            },
            session_id,
            task_id,
        )
        assert result["ok"], result
        engagement_id = UUID(result["engagement"]["engagement_id"])
        self._engagement_id = engagement_id
        return engagement_id

    def decide(self, strategy: str, *, session_id: str, task_id: str = "root") -> None:
        result = self._call(
            "sedna_record_decision",
            {
                "custom_strategy": strategy,
                "rationale": "simulated host decision",
            },
            session_id,
            task_id,
        )
        assert result["ok"], result

    def tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: str,
        *,
        session_id: str,
        tool_call_id: str,
        task_id: str = "root",
    ) -> None:
        identity = {
            "session_id": session_id,
            "task_id": task_id,
            "tool_call_id": tool_call_id,
            "turn_id": f"turn-{tool_call_id}",
            "api_request_id": f"request-{tool_call_id}",
            "api_call_count": 1,
        }
        self._hooks["pre_tool_call"](tool_name=tool_name, args=args, **identity)
        self._hooks["post_tool_call"](
            tool_name=tool_name,
            args=args,
            result=result,
            duration_ms=7,
            **identity,
        )

    def end_session(self, session_id: str) -> None:
        self._hooks["on_session_finalize"](session_id=session_id)

    def resume(self, *, target: str, session_id: str, task_id: str = "root") -> _Result:
        result = self._call(
            "sedna_manage_engagement",
            {"action": "resume", "authorization": (target,)},
            session_id,
            task_id,
        )
        assert result["ok"], result
        return _Result(engagement_id=UUID(result["engagement"]["engagement_id"]))

    def close(self, *, reason: str, session_id: str, task_id: str = "root") -> _Result:
        result = self._call(
            "sedna_manage_engagement",
            {"action": "close", "reason": reason},
            session_id,
            task_id,
        )
        assert result["ok"], result
        state = self._state()
        return _Result(status=state.status, closure_ready=state.closure_ready)

    def reopen(self, *, reason: str, session_id: str, task_id: str = "root") -> _Result:
        result = self._call(
            "sedna_manage_engagement",
            {
                "action": "reopen",
                "reason": reason,
                "engagement_id": str(self._engagement_id),
            },
            session_id,
            task_id,
        )
        assert result["ok"], result
        state = self._state()
        return _Result(status=state.status)

    def logbook_text(self, engagement_id: UUID, session_id: str) -> str:
        evidence_dir = self.knowledge_root / "engagements" / str(engagement_id) / "evidence"
        for path in evidence_dir.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            if f"- Session: {session_id}" in text:
                return text
        raise AssertionError(f"no logbook for session {session_id}")

    def valid_hash_chain(self, engagement_id: UUID) -> bool:
        try:
            self._snapshot(engagement_id)
        except Exception:
            return False
        return True

    def recovery_tail_count(self, engagement_id: UUID) -> int:
        snapshot = self._snapshot(engagement_id)
        return sum(1 for event in snapshot.events if event.type.value == "recovery_warning")

    def global_retrieval_contains(self, text: str) -> bool:
        needle = text.encode("utf-8")
        for path in self.knowledge_root.rglob("*"):
            if not path.is_file():
                continue
            if _is_private_evidence_path(self.knowledge_root, path):
                continue
            try:
                if needle in path.read_bytes():
                    return True
            except OSError:
                continue
        return False

    # -- internals --------------------------------------------------------

    def _call(
        self,
        name: str,
        payload: dict[str, Any],
        session_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        tool = self._tools[name]
        invocation = dict(payload)
        invocation["session_id"] = session_id
        invocation["task_id"] = task_id
        result = tool["handler"](**invocation)
        assert type(result) is dict
        return result

    def _snapshot(self, engagement_id: UUID):
        with EngagementJournalService.open(self.knowledge_root) as service:
            return service.load_snapshot(engagement_id)

    def _state(self):
        with EngagementJournalService.open(self.knowledge_root) as service:
            identifiers = service.list_snapshot_ids()
            assert identifiers, "no published engagement"
            return service.load_snapshot(identifiers[0]).state


def _is_private_evidence_path(knowledge_root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(knowledge_root)
    except ValueError:
        return False
    parts = relative.parts
    return len(parts) >= 4 and parts[0] == "engagements" and parts[2] == "evidence"


def inject_partial_tail(knowledge_root: Path, engagement_id: UUID, tail: bytes) -> None:
    """Append a partial JSON record as if the host crashed mid-write."""
    path = knowledge_root / "engagements" / str(engagement_id) / "events.jsonl"
    flags = os.O_WRONLY | os.O_APPEND
    fd = os.open(path, flags)
    try:
        os.write(fd, tail)
        os.fsync(fd)
    finally:
        os.close(fd)
