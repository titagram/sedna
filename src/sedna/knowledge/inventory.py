"""Deterministic discovery of source documents and their local assets."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

_SOURCE_SUFFIXES = {".md", ".pdf"}


@dataclass(frozen=True, slots=True)
class AssetCandidate:
    """A non-document file associated with a source document."""

    path: Path
    relative_path: str
    suffix: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    """A source document and the assets stored beneath its directory."""

    source_id: str
    path: Path
    relative_path: str
    suffix: str
    sha256: str
    size_bytes: int
    assets: tuple[AssetCandidate, ...]


def stable_source_id(relative_path: str) -> str:
    """Build a content-independent source identifier from a POSIX path."""
    return f"source-{uuid5(NAMESPACE_URL, f'sedna:{relative_path}')}"


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file without loading it all into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def discover_sources(source_root: Path) -> tuple[SourceCandidate, ...]:
    """Discover supported documents in stable order with their local assets."""
    source_root = Path(source_root)

    def relative_path(path: Path) -> str:
        return path.relative_to(source_root).as_posix()

    files = tuple(
        sorted((path for path in source_root.rglob("*") if path.is_file()), key=relative_path)
    )
    source_paths = tuple(path for path in files if path.suffix.lower() in _SOURCE_SUFFIXES)
    source_path_set = set(source_paths)

    return tuple(
        SourceCandidate(
            source_id=stable_source_id(relative_path(path)),
            path=path,
            relative_path=relative_path(path),
            suffix=path.suffix.lower(),
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            assets=_assets_for(path, files, source_path_set, relative_path),
        )
        for path in source_paths
    )


def _assets_for(
    source_path: Path,
    all_files: tuple[Path, ...],
    source_paths: set[Path],
    relative_path: Callable[[Path], str],
) -> tuple[AssetCandidate, ...]:
    source_directory = source_path.parent
    return tuple(
        AssetCandidate(
            path=path,
            relative_path=relative_path(path),
            suffix=path.suffix.lower(),
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
        )
        for path in all_files
        if path != source_path
        and path not in source_paths
        and path.name != ".DS_Store"
        and source_directory in path.parents
    )
