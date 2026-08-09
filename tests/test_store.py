from __future__ import annotations

from uuid import uuid4

import pytest

from sedna.models import Finding, FindingType, KnowledgeChunk, Machine, Phase, Severity
from sedna.store import SednaStore


def test_store_round_trips_records_across_reopen(tmp_path):
    db_path = tmp_path / "sedna.db"
    machine = Machine(name="Lame", ip="10.10.10.3", tags=("htb",))
    finding = Finding(
        machine_id=machine.id,
        type=FindingType.SERVICE,
        severity=Severity.MEDIUM,
        phase=Phase.ENUM,
        title="Samba service",
        description="Samba 3.0.20 is exposed on TCP 445",
        tool="nmap",
        raw_output="445/tcp open netbios-ssn Samba smbd 3.0.20",
    )

    with SednaStore(db_path) as store:
        store.save(machine)
        store.save(finding)

    with SednaStore(db_path) as store:
        assert store.get(Machine, machine.id) == machine
        assert store.get(Finding, finding.id) == finding
        assert store.get(Finding, uuid4()) is None


def test_full_text_search_indexes_and_updates_records(tmp_path):
    machine = Machine(name="Lame", ip="10.10.10.3")
    finding = Finding(
        machine_id=machine.id,
        type=FindingType.VULN,
        severity=Severity.HIGH,
        phase=Phase.ENUM,
        title="Outdated Samba",
        description="Initial hypothesis",
        tool="nmap",
    )

    with SednaStore(tmp_path / "sedna.db") as store:
        store.save(machine)
        store.save(finding)
        assert [hit.id for hit in store.search("outdated samba")] == [finding.id]

        updated = finding.model_copy(
            update={"description": "Confirmed username map script command execution"}
        )
        store.save(updated)

        assert store.search("initial hypothesis") == []
        hits = store.search("command execution")
        assert len(hits) == 1
        assert hits[0].id == finding.id
        assert hits[0].kind == "Finding"
        assert "execution" in hits[0].snippet.lower()


def test_search_can_be_scoped_by_kind_and_machine(tmp_path):
    first = Machine(name="First", ip="10.10.10.1")
    second = Machine(name="Second", ip="10.10.10.2")
    first_finding = Finding(
        machine_id=first.id,
        type=FindingType.PORT,
        phase=Phase.RECON,
        title="SSH exposed",
        description="OpenSSH detected",
        tool="nmap",
    )
    second_finding = first_finding.model_copy(update={"id": uuid4(), "machine_id": second.id})
    knowledge = KnowledgeChunk(
        source_path="academy/ssh.md",
        source_type="markdown",
        title="SSH enumeration",
        content="Enumerate SSH algorithms before testing authentication.",
        phase=Phase.ENUM,
    )

    with SednaStore(tmp_path / "sedna.db") as store:
        for record in (first, second, first_finding, second_finding, knowledge):
            store.save(record)

        machine_hits = store.search("OpenSSH", machine_id=first.id)
        assert [hit.id for hit in machine_hits] == [first_finding.id]

        knowledge_hits = store.search("SSH", kinds=("KnowledgeChunk",))
        assert [hit.id for hit in knowledge_hits] == [knowledge.id]


def test_search_rejects_empty_queries(tmp_path):
    with SednaStore(tmp_path / "sedna.db") as store, pytest.raises(ValueError, match="query"):
        store.search("  ")
