from __future__ import annotations

from sedna.knowledge.ingest import ingest_markdown
from sedna.models import KnowledgeChunk, Phase
from sedna.store import SednaStore


def test_ingestion_extracts_article_chunks_and_is_idempotent(tmp_path):
    source_root = tmp_path / "raw_src"
    source = source_root / "01_information-gathering" / "DNS.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        """DNS | Hack The Box Academy
Dashboard

DNS
===

* * *

DNS maps hostnames to IP addresses. Use dig to inspect records.

Record Types
------------

A records map hosts to IPv4 addresses. MX records identify mail servers.

Previous
Section 4 / 19
""",
        encoding="utf-8",
    )

    with SednaStore(tmp_path / "sedna.db") as store:
        first = ingest_markdown(source, store=store, source_root=source_root)
        second = ingest_markdown(source, store=store, source_root=source_root)

        assert [chunk.id for chunk in first] == [chunk.id for chunk in second]
        assert all(chunk.source_path == "01_information-gathering/DNS.md" for chunk in first)
        assert all(chunk.phase == Phase.RECON for chunk in first)
        assert all("Dashboard" not in chunk.content for chunk in first)
        restored = store.get(KnowledgeChunk, first[0].id)
        assert restored is not None
        assert restored.model_dump(mode="json") == first[0].model_dump(mode="json")

        hits = store.search("mail servers", kinds=("KnowledgeChunk",))
        assert len(hits) == 1
        assert hits[0].id in {chunk.id for chunk in first}


def test_ingestion_splits_long_content_on_paragraph_boundaries(tmp_path):
    source = tmp_path / "long.md"
    source.write_text(
        """Long lesson
===========

First paragraph contains enough text to be retained as a first chunk.

Second paragraph contains a different useful concept for local searching.

Third paragraph has the final detail.
""",
        encoding="utf-8",
    )

    with SednaStore(tmp_path / "sedna.db") as store:
        chunks = ingest_markdown(source, store=store, maximum_chunk_chars=85)

    assert len(chunks) >= 2
    assert all(len(chunk.content) <= 85 for chunk in chunks)
    assert "different useful concept" in " ".join(chunk.content for chunk in chunks)
