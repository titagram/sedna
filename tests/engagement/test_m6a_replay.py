"""Cross-session M6A acceptance: crash tail, resume, close, and reopen."""

from __future__ import annotations

from tests.engagement.simulated_hades import SimulatedHades, inject_partial_tail


def test_named_engagement_survives_sessions_crash_tail_closing_and_reopen(
    tmp_path,
) -> None:
    host = SimulatedHades(tmp_path / "knowledge")
    engagement_id = host.create(
        name="HTB-Orion",
        objective="Obtain user and root flags",
        target="192.0.2.44",
        required_proofs=("user-flag", "root-flag"),
        session_id="session-1",
    )
    host.decide("Enumerate exposed services", session_id="session-1")
    host.tool(
        "terminal",
        {"command": "simulated-scan 192.0.2.44"},
        "22/tcp open ssh\n80/tcp open http\nHTB{private-user-proof}",
        session_id="session-1",
        tool_call_id="call-1",
    )
    host.end_session("session-1")
    inject_partial_tail(host.knowledge_root, engagement_id, b'{"interrupted"')

    resumed = host.resume(target="192.0.2.44", session_id="session-2")
    assert resumed.engagement_id == engagement_id
    host.tool(
        "terminal",
        {"command": "simulated-read-root-flag"},
        "HTB{private-root-proof}",
        session_id="session-2",
        tool_call_id="call-2",
    )
    closing = host.close(
        reason="all expected proof observed", session_id="session-2"
    )
    reopened = host.reopen(
        reason="platform rejected the proof", session_id="session-3"
    )

    assert closing.status == "closing"
    assert closing.closure_ready is True
    assert reopened.status == "active"
    assert "HTB{private-root-proof}" in host.logbook_text(
        engagement_id, "session-2"
    )
    assert host.valid_hash_chain(engagement_id)
    assert host.recovery_tail_count(engagement_id) == 1
    assert host.global_retrieval_contains("HTB{private-root-proof}") is False
