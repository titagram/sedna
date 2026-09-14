"""The planner must be told which credential labels it may cite.

Defect 14. `validate_command_suggestion` requires a credential_ref binding to name
a label from `secret_references` (`command_secret_reference_not_current`
otherwise), but the payload the planner receives exposed no credential list
anywhere: `PlannerKnowledgeContext` had no such field and `CurrentSituation`
carried only `secret_references` with no planner-facing enumeration. The planner
was therefore asked to cite an identifier it had no way to discover, which is why
it invented `jharris-current-secret` and every draft was rejected.

`AvailableCredential` is the planner-facing view: the label (what a binding must
equal) and the kind, never the value.
"""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID, uuid4

from sedna.engagement import JournalRevision
from sedna.planning import ObjectiveProgress, SituationProjection
from sedna.planning.models import AvailableCredential, SecretReference


def _sha(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _evidence_id() -> str:
    return "evidence-sha256-" + "a" * 64


def test_available_credential_exposes_only_a_label_and_kind() -> None:
    """The planner-facing credential view must never carry a secret value."""
    credential = AvailableCredential(
        event_ids=(UUID("00000000-0000-0000-0000-000000000001"),),
        label="operator-session",
        secret_kind="password",
    )

    dumped = credential.model_dump(mode="json")

    assert set(dumped) == {"event_ids", "label", "secret_kind"}
    for forbidden in ("value", "value_sha256", "secret_value", "password", "digest"):
        assert forbidden not in dumped


def test_situation_carries_available_credentials() -> None:
    """A situation with secret references must enumerate them for the planner."""
    event_id = UUID("00000000-0000-0000-0000-000000000001")
    situation = SituationProjection(
        engagement_id=uuid4(),
        authoritative_journal_revision=JournalRevision(sequence=0, event_hash=_sha("head")),
        material_event_revision=0,
        state_digest=_sha("state"),
        objective_progress=ObjectiveProgress(),
        secret_references=(
            SecretReference(
                event_ids=(event_id,),
                label="operator-session",
                evidence_id=_evidence_id(),
                value_sha256=_sha("value"),
            ),
        ),
        available_credentials=(
            AvailableCredential(
                event_ids=(event_id,),
                label="operator-session",
                secret_kind="other",
            ),
        ),
    )

    labels = [item.label for item in situation.available_credentials]
    assert labels == ["operator-session"]

    # And the label the planner is shown is exactly what a binding must cite.
    assert set(labels) == {item.label for item in situation.secret_references}


def test_available_credentials_default_to_empty() -> None:
    """A situation without credentials must not fabricate any."""
    situation = SituationProjection(
        engagement_id=uuid4(),
        authoritative_journal_revision=JournalRevision(sequence=0, event_hash=_sha("head")),
        material_event_revision=0,
        state_digest=_sha("state"),
        objective_progress=ObjectiveProgress(),
    )

    assert situation.available_credentials == ()
