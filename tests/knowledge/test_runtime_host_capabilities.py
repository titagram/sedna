"""Regression: runtime host binding preserves declared structured-output capability."""

from __future__ import annotations

from sedna.knowledge.hades_runtime import _require_structured_host


class _SchemaHost:
    accepts_schema = True

    def complete_structured(self, **kwargs: object) -> object:
        return kwargs


def test_bound_structured_host_preserves_accepts_schema_capability() -> None:
    bound = _require_structured_host(_SchemaHost())

    assert getattr(bound, "accepts_schema", False) is True
