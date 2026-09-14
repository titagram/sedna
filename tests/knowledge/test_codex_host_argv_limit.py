"""The Codex host must not pass a large prompt as a command-line argument.

Defect 11: `_run_codex` built `cmd = [binary, "exec", ..., prompt]`, putting the
entire prompt into the argument vector. A planner prompt on a real settled
engagement measured 208_114 characters, and the host then failed with

    CodexCliError: codex exec failed to start:
      [Errno 7] Argument list too long: '/home/titagram/.local/bin/codex'

which the planner adapter relabelled as `transport_failure` and surfaced as
`gap llm_unavailable` — so planning could never produce a frontier.

`codex exec` reads instructions from stdin when the prompt is omitted or given as
`-`, so the prompt must be delivered on stdin instead of argv.
"""

from __future__ import annotations

import subprocess

import pytest

from sedna.knowledge.semantic.codex_host import CodexCliError, CodexCliHost


class _Recorder:
    """Stand-in for subprocess.run that records how it was invoked."""

    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[dict[str, object]] = []
        self._returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    def __call__(self, cmd, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append({"cmd": list(cmd), **kwargs})
        return subprocess.CompletedProcess(
            args=cmd, returncode=self._returncode, stdout=self._stdout, stderr=self._stderr
        )


def _patch_run(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    monkeypatch.setattr(
        "sedna.knowledge.semantic.codex_host.subprocess.run", recorder, raising=True
    )


def test_large_prompt_is_not_placed_in_the_argument_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prompt far larger than any argv limit must not appear in cmd."""
    recorder = _Recorder()
    _patch_run(monkeypatch, recorder)

    host = CodexCliHost()
    huge_prompt = "x" * 300_000

    host._run_codex(huge_prompt, None, "gpt-5.5")

    assert recorder.calls, "subprocess.run was not invoked"
    cmd = recorder.calls[0]["cmd"]
    assert isinstance(cmd, list)
    for argument in cmd:
        assert huge_prompt not in str(argument), (
            "the prompt must not be passed as a command-line argument"
        )
    # It must be delivered on stdin instead.
    assert recorder.calls[0].get("input") == huge_prompt or recorder.calls[0].get("input") == (
        huge_prompt
    )


def test_small_prompt_also_uses_stdin_for_consistency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a small prompt goes on stdin, so behaviour does not depend on size."""
    recorder = _Recorder()
    _patch_run(monkeypatch, recorder)

    host = CodexCliHost()
    host._run_codex("a short prompt", None, "gpt-5.5")

    cmd = recorder.calls[0]["cmd"]
    assert "a short prompt" not in cmd
    assert recorder.calls[0].get("input") == "a short prompt"


def test_codex_error_message_preserves_argument_limit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An E2BIG-style OSError must surface as a CodexCliError with its detail."""

    def exploding_run(cmd, **kwargs):  # noqa: ANN001, ANN003
        raise OSError(7, "Argument list too long", "/home/titagram/.local/bin/codex")

    monkeypatch.setattr(
        "sedna.knowledge.semantic.codex_host.subprocess.run", exploding_run, raising=True
    )

    host = CodexCliHost()
    with pytest.raises(CodexCliError) as caught:
        host._run_codex("prompt", None, "gpt-5.5")

    assert "Argument list too long" in str(caught.value)
