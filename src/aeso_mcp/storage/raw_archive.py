# SPDX-License-Identifier: MIT
"""Opt-in content-addressed source archive for reproducible local research."""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from aeso_mcp.errors import DataValidationError

_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class RawSourceObservation:
    """Identity and information time of one retrieved source object."""

    dataset: str
    source_id: str
    source_name: str
    sha256: str
    size_bytes: int
    retrieved_at: str
    source_updated_at: str | None
    schema_version: int = 1
    source_variant: str | None = None


def _utc_text(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("Archive timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _digest_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _atomic_write_once(path: Path, payload: bytes, digest: str) -> None:
    """Publish a complete immutable file; concurrent identical writes are safe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _digest_file(path) != digest:
            raise DataValidationError("Raw source archive integrity check failed.")
        return
    with NamedTemporaryFile(dir=path.parent, prefix=".partial-", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            with suppress(FileNotFoundError):
                temporary.unlink()
            raise
    try:
        # Linking publishes atomically without replacing an existing observation.
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _digest_file(path) != digest:
                raise DataValidationError("Raw source archive integrity check failed.") from None
    finally:
        temporary.unlink(missing_ok=True)


class RawSourceArchive:
    """Store original source bytes separately from mutable normalized tables.

    This is a local filesystem archive, not an external shared warehouse.
    Only fixed provider code should write it; no arbitrary URLs are accepted.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def record(
        self,
        payload: bytes,
        *,
        dataset: str,
        source_id: str,
        source_name: str,
        retrieved_at: datetime,
        source_updated_at: datetime | None = None,
        source_variant: str | None = None,
    ) -> RawSourceObservation:
        digest = hashlib.sha256(payload).hexdigest()
        observation = RawSourceObservation(
            dataset=dataset,
            source_id=source_id,
            source_name=source_name,
            sha256=digest,
            size_bytes=len(payload),
            retrieved_at=_utc_text(retrieved_at),
            source_updated_at=_utc_text(source_updated_at) if source_updated_at else None,
            source_variant=source_variant,
        )
        manifest = json.dumps(asdict(observation), sort_keys=True, separators=(",", ":")).encode()
        manifest_digest = hashlib.sha256(manifest).hexdigest()
        _atomic_write_once(self._object_path(digest), payload, digest)
        _atomic_write_once(
            self.root / "observations" / f"{manifest_digest}.json", manifest, manifest_digest
        )
        return observation

    def select_as_of(
        self,
        *,
        dataset: str,
        source_id: str,
        as_of: datetime,
        source_variant: str | None = None,
    ) -> RawSourceObservation | None:
        """Select the latest explicitly observed revision, without network access.

        A source update timestamp is not an information-time substitute. Old manifests
        without a variant cannot satisfy a variant-specific request.
        """
        cutoff = datetime.fromisoformat(_utc_text(as_of))
        best: RawSourceObservation | None = None
        best_time: datetime | None = None
        ambiguous = False
        for path in (self.root / "observations").glob("*.json"):
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != path.stem:
                raise DataValidationError("Raw source manifest integrity check failed.")
            try:
                observation = RawSourceObservation(**json.loads(payload))
                retrieved = datetime.fromisoformat(observation.retrieved_at)
                _utc_text(retrieved)
                updated = (
                    datetime.fromisoformat(observation.source_updated_at)
                    if observation.source_updated_at is not None
                    else None
                )
                if updated is not None:
                    _utc_text(updated)
                if observation.schema_version != 1:
                    raise ValueError("Unsupported manifest schema")
            except (ValueError, TypeError) as exc:
                raise DataValidationError("Invalid raw source observation manifest.") from exc
            if (
                observation.dataset != dataset
                or observation.source_id != source_id
                or observation.source_variant != source_variant
                or retrieved > cutoff
                or (updated is not None and updated > cutoff)
            ):
                continue
            if best_time is None or retrieved > best_time:
                best, best_time = observation, retrieved
                ambiguous = False
            elif retrieved == best_time and observation != best:
                ambiguous = True
        if ambiguous:
            raise DataValidationError("Ambiguous raw source revisions at the same retrieval time.")
        return best

    def read(self, digest: str) -> bytes:
        """Read exactly one content-addressed object and verify its bytes."""
        payload = self._object_path(digest).read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise DataValidationError("Raw source archive integrity check failed.")
        return payload

    def _object_path(self, digest: str) -> Path:
        if not _DIGEST.fullmatch(digest):
            raise ValueError("Expected a lowercase SHA-256 digest")
        return self.root / "objects" / digest[:2] / digest
