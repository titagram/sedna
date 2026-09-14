"""The Codex host must take the model's answer from the agent_message, not from
the last JSON blob in the raw stream.

Defect 12: when `agent_message.text` was not pure JSON, `_extract_parsed` fell
back to `_last_json_object(raw)`, which scans BACKWARDS and therefore returns the
LAST JSON object in the stream. With `codex exec --json` the last object is the
`turn.completed` event carrying usage counters, so the planner received

    {'type': 'turn.completed', ..., 'output_tokens': 516}

instead of the draft, and validation failed with three unrelated errors
(proposals missing, type missing, ...). The model had answered correctly; the host
read the wrong object.

The raw stream also mixes stderr warnings into `raw`, so a backwards brace scan
can land on an unrelated object.
"""

from __future__ import annotations

import json

import pytest

from sedna.knowledge.semantic.codex_host import CodexCliHost


def _host() -> CodexCliHost:
    return CodexCliHost()


def test_agent_message_with_prose_and_json_is_extracted() -> None:
    """A message that wraps JSON in prose must still yield the JSON payload."""
    events = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": 'Sure, here it is:\n{"proposals": []}\nDone.',
            },
        },
        {"type": "turn.completed", "usage": {"output_tokens": 516}},
    ]
    raw = "\n".join(json.dumps(event) for event in events)

    parsed = _host()._extract_parsed(events, raw)

    assert parsed == {"proposals": []}, parsed


def test_turn_completed_usage_is_never_returned_as_the_answer() -> None:
    """Regression: the usage event must never become the parsed answer."""
    events = [
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "not json at all"}},
        {"type": "turn.completed", "usage": {"output_tokens": 516}, "type": "turn.completed"},
    ]
    raw = "\n".join(json.dumps(event) for event in events)

    parsed = _host()._extract_parsed(events, raw)

    if parsed is not None:
        assert "output_tokens" not in json.dumps(parsed)
        assert parsed.get("type") != "turn.completed"


def test_stderr_noise_does_not_shift_the_extracted_object() -> None:
    """Warnings in the raw stream must not change which object is returned."""
    message = '{"proposals": [{"score": 1}]}'
    events = [
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": message}},
        {"type": "turn.completed", "usage": {"output_tokens": 1}},
    ]
    stdout = "\n".join(json.dumps(event) for event in events)
    stderr_noise = "2026-09-14 WARN some unrelated notice {'detail': 'noise'}"
    raw = stdout + "\n" + stderr_noise

    parsed = _host()._extract_parsed(events, raw)

    assert parsed == {"proposals": [{"score": 1}]}, parsed


def test_first_agent_message_wins_when_several_are_emitted() -> None:
    """The answer is the agent message, not a later bookkeeping object."""
    events = [
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": '{"answer": "first"}'},
        },
        {"type": "item.completed", "item": {"type": "agent_message", "text": "chatter"}},
        {"type": "turn.completed", "usage": {"output_tokens": 3}},
    ]
    raw = "\n".join(json.dumps(event) for event in events)

    parsed = _host()._extract_parsed(events, raw)

    assert parsed == {"answer": "first"}, parsed


def test_error_event_still_raises() -> None:
    """An error event with no agent message must raise, not silently return."""
    from sedna.knowledge.semantic.codex_host import CodexCliError

    events = [{"type": "turn.failed", "message": "boom"}]
    raw = json.dumps(events[0])

    with pytest.raises(CodexCliError):
        _host()._extract_parsed(events, raw)
