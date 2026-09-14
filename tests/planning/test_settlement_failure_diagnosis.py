"""A settlement failure must not discard the cause of the failure.

Defect 15. When the settlement path fails for any reason, the handler re-derives
the situation to report a revision:

    try:
        situation = self.load_situation(engagement_id)
    except Exception:
        return FailedSettlementResult(..., failure_code="journal_unavailable", ...)

The bare `except Exception` drops the underlying exception entirely. So a failure
that has NOTHING to do with the journal — for example a schema violation inside
the situation reducer — is reported as `journal_unavailable`, i.e. "the journal is
unavailable", which is false and extremely misleading.

This is not hypothetical: during development a missing required field in a
newly added situation record made the situation fail to rebuild, and the only
thing reported was `failure_code: journal_unavailable` with no traceback, which
sent the investigation after a non-existent journal problem.

The failure_summary must carry the real cause so the failure is diagnosable.
"""

from __future__ import annotations

import inspect

from sedna.planning.service import PlanningService


def _settlement_source() -> str:
    return inspect.getsource(PlanningService.settle_pending_evidence)


def test_settlement_failure_handler_does_not_swallow_the_cause() -> None:
    """The bare `except Exception` around load_situation must bind and report it."""
    source = _settlement_source()

    assert (
        "except Exception:\n"
        not in source.replace("        ", "  ").replace(
            "            except Exception:\n", "            except Exception:\n"
        )
        or "journal_unavailable" in source
    ), "expected the settlement failure handler"

    # The specific bad pattern: catch-all that reports a journal problem.
    marker = 'failure_code="journal_unavailable"'
    assert marker in source, "expected the journal_unavailable report site"

    index = source.index(marker)
    window = source[max(0, index - 900) : index]
    assert "except Exception as" in window, (
        "the handler that reports journal_unavailable does not bind the exception, "
        "so the real cause is discarded and the failure is undiagnosable"
    )


def test_journal_unavailable_summary_carries_the_underlying_cause() -> None:
    """The reported summary must include the underlying exception text."""
    source = _settlement_source()
    assert "failure_summary=" in source

    index = source.index('failure_code="journal_unavailable"')
    window = source[max(0, index - 400) : index + 400]
    assert "exc" in window, (
        "the journal_unavailable failure_summary does not mention the underlying "
        "exception, so a non-journal failure is indistinguishable from a real one"
    )
