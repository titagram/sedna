"""Hermes/Hades standalone-plugin entry point for Sedna."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

_SRC = Path(__file__).parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sedna.plugin import register as _sedna_register

_HADES_STYLE_TOOLS = {
    "sedna_manage_engagement",
    "sedna_record_decision",
    "sedna_add_source",
}


def _serialize_hermes_result(result: Any) -> str:
    try:
        return json.dumps(result, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return json.dumps(
            {"error": "tool handler returned a non-JSON-serializable result"},
            sort_keys=True,
        )


def _dual_abi_handler(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Accept both Hermes mapping dispatch and Hades keyword dispatch."""

    def wrapped(*args: object, **kwargs: Any) -> Any:
        if not args:
            return handler(**kwargs)
        if len(args) != 1 or not isinstance(args[0], Mapping):
            raise TypeError("tool handler expects one argument mapping")

        payload = dict(args[0])
        payload.pop("user_task", None)
        for trusted_key in ("session_id", "task_id"):
            if trusted_key in kwargs:
                payload[trusted_key] = kwargs[trusted_key]
        result = handler(**payload)
        return _serialize_hermes_result(result)

    return wrapped


class _HostContextAdapter:
    def __init__(self, context: Any) -> None:
        self._context = context

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)

    def register_tool(self, *args: Any, **kwargs: Any) -> Any:
        name = kwargs.get("name", args[0] if args else None)
        if name in _HADES_STYLE_TOOLS:
            if "handler" in kwargs:
                kwargs["handler"] = _dual_abi_handler(kwargs["handler"])
            elif len(args) >= 4:
                mutable_args = list(args)
                mutable_args[3] = _dual_abi_handler(mutable_args[3])
                args = tuple(mutable_args)
        return self._context.register_tool(*args, **kwargs)


def register(ctx: Any) -> None:
    """Register Sedna with handlers compatible with both host dispatch ABIs."""
    _sedna_register(_HostContextAdapter(ctx))


__all__ = ["register"]
