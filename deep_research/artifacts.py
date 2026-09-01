"""Durable, path-safe storage for workflow artifacts.

The research runtime passes information between planner steps through files.  This
module keeps that file boundary explicit: callers can only write below the
``work/<slug>/<stage>`` or ``output/<slug>/<stage>`` trees, writes are atomic, and
each committed file is represented by a small JSON manifest entry.

Only the Python standard library is used here so the store can also be used by a
worker process before the rest of the application has finished importing.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import mimetypes
import os
import re
import tempfile
import threading
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO, TextIO, cast

__all__ = [
    "ARTIFACT_MANIFEST_SCHEMA_VERSION",
    "ArtifactError",
    "ArtifactIntegrityError",
    "ArtifactManifest",
    "ArtifactPathError",
    "ArtifactRecord",
    "ArtifactStore",
    "ArtifactValidationError",
    "Manifest",
    "ManifestError",
    "ManifestEntry",
    "PathTraversalError",
]


ARTIFACT_MANIFEST_SCHEMA_VERSION = 1
DEFAULT_MAX_BYTES = 100 * 1024 * 1024
_AREAS = frozenset({"work", "output"})
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ArtifactError(RuntimeError):
    """Base class for artifact storage errors."""


class ArtifactPathError(ArtifactError, ValueError):
    """Raised when an artifact path is outside the configured workspace."""


class PathTraversalError(ArtifactPathError):
    """Compatibility name for callers that distinguish traversal failures."""


class ArtifactValidationError(ArtifactError, ValueError):
    """Raised when an artifact fails a size or MIME validation."""


class ArtifactIntegrityError(ArtifactError):
    """Raised when a stored artifact no longer matches its manifest entry."""


class ManifestError(ArtifactError, ValueError):
    """Raised when a manifest is malformed or cannot be persisted."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: object, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"manifest {field_name} must be an ISO timestamp")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ManifestError(f"manifest {field_name} must be an ISO timestamp") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _normalise_mime(value: str | None) -> str:
    if value is None:
        return ""
    return value.split(";", 1)[0].strip().lower()


def guess_mime(name: str | os.PathLike[str]) -> str:
    """Return a stable MIME type for a filename.

    ``mimetypes`` is platform-configured on some operating systems, so the
    ``strict=False`` lookup is intentional and the binary fallback is explicit.
    """

    guessed, _encoding = mimetypes.guess_type(os.fspath(name), strict=False)
    return _normalise_mime(guessed) or "application/octet-stream"


def _mime_matches(actual: str, expected: str) -> bool:
    expected = _normalise_mime(expected)
    actual = _normalise_mime(actual)
    if not expected:
        return True
    if expected.endswith("/*"):
        return actual.startswith(expected[:-1])
    return actual == expected


def _normalise_allowed_mimes(values: str | Iterable[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = (values,)
    result = tuple(_normalise_mime(value) for value in values if _normalise_mime(value))
    return result


def _json_value(value: object, *, field_name: str) -> Any:
    """Validate metadata eagerly, before an artifact is committed."""

    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ArtifactValidationError(f"{field_name} must be JSON serializable") from exc
    return value


def _validate_component(value: object, *, label: str, max_length: int = 128) -> str:
    if not isinstance(value, str):
        raise PathTraversalError(f"{label} must be a string")
    if not value or len(value) > max_length or "\x00" in value:
        raise PathTraversalError(f"invalid {label}")
    # Check both path grammars.  A Windows drive/UNC path must not become a
    # seemingly harmless filename when the worker happens to run on POSIX.
    if value in {".", ".."} or "/" in value or "\\" in value or ":" in value:
        raise PathTraversalError(f"invalid {label}")
    if not _COMPONENT_RE.fullmatch(value):
        raise PathTraversalError(f"invalid {label}")
    return value


def _validate_relative_name(value: object, *, label: str = "artifact name") -> str:
    if not isinstance(value, str):
        value = os.fspath(value) if isinstance(value, os.PathLike) else value
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PathTraversalError(f"invalid {label}")
    # PureWindowsPath catches drive letters and UNC roots even on POSIX.  The
    # POSIX check catches rooted paths on every platform.
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or windows.root:
        raise PathTraversalError(f"invalid {label}")
    # Treat backslashes as separators regardless of the host platform.
    parts = [part for part in re.split(r"[/\\]", value) if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise PathTraversalError(f"invalid {label}")
    if any("\x00" in part for part in parts):
        raise PathTraversalError(f"invalid {label}")
    # Empty components are ambiguous and make manifest paths non-canonical.
    if any(part == "" for part in re.split(r"[/\\]", value)):
        raise PathTraversalError(f"invalid {label}")
    canonical = "/".join(parts)
    if len(canonical) > 512:
        raise PathTraversalError(f"{label} is too long")
    return canonical


def _contained(path: Path, base: Path) -> Path:
    """Resolve a path and ensure it remains below ``base``.

    Resolving existing symlinks is important: lexical ``..`` checks alone do
    not protect a stage directory that was replaced by a symlink between calls.
    """

    resolved_base = base.resolve(strict=False)
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(resolved_base)
    except ValueError as exc:
        raise PathTraversalError("artifact path escapes the workspace") from exc
    return resolved


def _fsync_directory(path: Path) -> None:
    """Best-effort directory durability after an atomic rename."""

    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        with contextlib.suppress(OSError):
            os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """Manifest metadata for one committed file.

    ``path`` is always workspace-relative and uses POSIX separators.  Use
    :meth:`ArtifactStore.absolute_path` when a filesystem path is needed.
    """

    path: str
    slug: str
    stage: str
    name: str
    area: str = "work"
    size_bytes: int = 0
    sha256: str = ""
    mime_type: str = "application/octet-stream"
    created_at: datetime = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)
    attempt: int | None = None

    def __post_init__(self) -> None:
        if self.area not in _AREAS:
            raise ManifestError(f"unknown artifact area: {self.area!r}")
        if self.size_bytes < 0:
            raise ManifestError("artifact size must be non-negative")
        if self.sha256 and not _SHA256_RE.fullmatch(self.sha256):
            raise ManifestError("artifact sha256 must be a 64-character hex digest")
        object.__setattr__(self, "mime_type", _normalise_mime(self.mime_type))
        object.__setattr__(self, "path", _validate_relative_name(self.path, label="artifact path"))
        object.__setattr__(self, "name", _validate_relative_name(self.name, label="artifact name"))
        object.__setattr__(self, "slug", _validate_component(self.slug, label="slug"))
        object.__setattr__(self, "stage", _validate_component(self.stage, label="stage"))
        expected_prefix = f"{self.area}/{self.slug}/{self.stage}/"
        if not self.path.startswith(expected_prefix):
            raise ManifestError("artifact path does not match area, slug, and stage")
        _json_value(self.metadata, field_name="artifact metadata")

    @property
    def relative_path(self) -> str:
        return self.path

    @property
    def root(self) -> str:
        """Alias used by plan adapters that call the area ``root``."""

        return self.area

    @property
    def size(self) -> int:
        return self.size_bytes

    @property
    def digest(self) -> str:
        return self.sha256

    @property
    def filename(self) -> str:
        return self.name.rsplit("/", 1)[-1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "relative_path": self.path,
            "slug": self.slug,
            "stage": self.stage,
            "area": self.area,
            "root": self.area,
            "name": self.name,
            "size_bytes": self.size_bytes,
            "size": self.size_bytes,
            "sha256": self.sha256,
            "mime_type": self.mime_type,
            "created_at": _iso(self.created_at),
            "metadata": self.metadata,
            **({"attempt": self.attempt} if self.attempt is not None else {}),
        }

    def model_dump(self, *, mode: str | None = None) -> dict[str, Any]:
        """Small Pydantic-compatible convenience for API/checkpoint adapters."""

        payload = self.to_dict()
        if mode == "json":
            return payload
        payload["created_at"] = self.created_at
        return payload

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ArtifactRecord:
        if not isinstance(raw, Mapping):
            raise ManifestError("artifact entry must be an object")
        path = raw.get("path", raw.get("relative_path"))
        area = raw.get("area", raw.get("root", "work"))
        size = raw.get("size_bytes", raw.get("size", 0))
        try:
            size_int = int(size)
        except (TypeError, ValueError) as exc:
            raise ManifestError("artifact size must be an integer") from exc
        created = raw.get("created_at")
        return cls(
            path=path,
            slug=raw.get("slug", ""),
            stage=raw.get("stage", ""),
            name=raw.get("name", path),
            area=area,
            size_bytes=size_int,
            sha256=str(raw.get("sha256", "")).lower(),
            mime_type=str(raw.get("mime_type", "application/octet-stream")),
            created_at=_parse_datetime(created, field_name="created_at")
            if created is not None
            else _utcnow(),
            metadata=dict(raw.get("metadata") or {}),
            attempt=int(raw["attempt"]) if raw.get("attempt") is not None else None,
        )


ManifestEntry = ArtifactRecord


@dataclass(slots=True)
class ArtifactManifest:
    """Versioned manifest for all artifacts belonging to one slug."""

    slug: str
    schema_version: int = ARTIFACT_MANIFEST_SCHEMA_VERSION
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.slug = _validate_component(self.slug, label="slug")
        if self.schema_version != ARTIFACT_MANIFEST_SCHEMA_VERSION:
            raise ManifestError(f"unsupported artifact manifest schema: {self.schema_version}")
        _json_value(self.metadata, field_name="manifest metadata")
        self.artifacts = list(self.artifacts)
        paths: set[str] = set()
        for artifact in self.artifacts:
            if artifact.slug != self.slug:
                raise ManifestError("artifact slug does not match manifest slug")
            if artifact.path in paths:
                raise ManifestError("manifest contains duplicate artifact paths")
            paths.add(artifact.path)

    @property
    def entries(self) -> list[ArtifactRecord]:
        return self.artifacts

    @property
    def files(self) -> list[ArtifactRecord]:
        return self.artifacts

    def get(self, path: str) -> ArtifactRecord | None:
        canonical = _validate_relative_name(path, label="artifact path")
        return next((item for item in self.artifacts if item.path == canonical), None)

    def upsert(self, artifact: ArtifactRecord) -> None:
        if artifact.slug != self.slug:
            raise ManifestError("artifact slug does not match manifest slug")
        for index, existing in enumerate(self.artifacts):
            if existing.path == artifact.path:
                self.artifacts[index] = artifact
                self.updated_at = _utcnow()
                return
        self.artifacts.append(artifact)
        self.updated_at = _utcnow()

    def remove(self, path: str) -> bool:
        canonical = _validate_relative_name(path, label="artifact path")
        before = len(self.artifacts)
        self.artifacts[:] = [item for item in self.artifacts if item.path != canonical]
        changed = len(self.artifacts) != before
        if changed:
            self.updated_at = _utcnow()
        return changed

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "slug": self.slug,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "metadata": self.metadata,
        }

    def model_dump(self, *, mode: str | None = None) -> dict[str, Any]:
        payload = self.to_dict()
        if mode != "json":
            payload["created_at"] = self.created_at
            payload["updated_at"] = self.updated_at
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ArtifactManifest:
        if not isinstance(raw, Mapping):
            raise ManifestError("artifact manifest must be an object")
        try:
            version = int(raw.get("schema_version", ARTIFACT_MANIFEST_SCHEMA_VERSION))
        except (TypeError, ValueError) as exc:
            raise ManifestError("manifest schema_version must be an integer") from exc
        slug = raw.get("slug")
        if not isinstance(slug, str):
            raise ManifestError("manifest slug is required")
        artifacts_raw = raw.get("artifacts", raw.get("entries", []))
        if not isinstance(artifacts_raw, list):
            raise ManifestError("manifest artifacts must be a list")
        created = raw.get("created_at")
        updated = raw.get("updated_at", created)
        return cls(
            slug=slug,
            schema_version=version,
            created_at=_parse_datetime(created, field_name="created_at")
            if created is not None
            else _utcnow(),
            updated_at=_parse_datetime(updated, field_name="updated_at")
            if updated is not None
            else _utcnow(),
            artifacts=[ArtifactRecord.from_dict(item) for item in artifacts_raw],
            metadata=dict(raw.get("metadata") or {}),
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> ArtifactManifest:
        try:
            raw = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ManifestError("artifact manifest is not valid JSON") from exc
        return cls.from_dict(raw)


Manifest = ArtifactManifest


class ArtifactStore:
    """Store and verify files below a workspace's two artifact trees.

    Parameters
    ----------
    workspace_root:
        Root containing ``work/``, ``output/`` and ``.framework/``.  ``root``
        and ``base_dir`` are accepted aliases for integration code.
    manifest_root:
        Directory for per-slug manifests.  Relative paths are resolved below
        ``.framework``; the default is ``.framework/manifests/<slug>.json``.
    max_bytes:
        Default maximum size for one write.  Pass ``None`` to disable the
        default limit; individual writes can still set ``max_size``.
    """

    def __init__(
        self,
        workspace_root: str | os.PathLike[str] = ".",
        *,
        root: str | os.PathLike[str] | None = None,
        base_dir: str | os.PathLike[str] | None = None,
        manifest_root: str | os.PathLike[str] | None = None,
        max_bytes: int | None = DEFAULT_MAX_BYTES,
        max_size: int | None = None,
        max_file_size: int | None = None,
        allowed_mime_types: str | Iterable[str] | None = None,
    ) -> None:
        aliases = [value for value in (root, base_dir) if value is not None]
        if aliases:
            if len(aliases) > 1 or workspace_root != ".":
                raise TypeError("provide only one of workspace_root, root, or base_dir")
            workspace_root = aliases[0]
        if max_size is not None:
            if max_bytes != DEFAULT_MAX_BYTES and max_bytes != max_size:
                raise TypeError("provide only one of max_bytes and max_size")
            max_bytes = max_size
        if max_file_size is not None:
            if max_bytes != DEFAULT_MAX_BYTES and max_bytes != max_file_size:
                raise TypeError("provide only one size limit")
            max_bytes = max_file_size
        if max_bytes is not None and (not isinstance(max_bytes, int) or max_bytes < 0):
            raise ValueError("max_bytes must be a non-negative integer or None")
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.work_root = self.workspace_root / "work"
        self.output_root = self.workspace_root / "output"
        self.framework_root = self.workspace_root / ".framework"
        if manifest_root is None:
            self.manifest_root = self.framework_root / "manifests"
            self._manifest_base = self.framework_root
        else:
            candidate = Path(manifest_root).expanduser()
            self.manifest_root = (
                candidate if candidate.is_absolute() else self.framework_root / candidate
            ).resolve()
            # Explicit locations are useful for deployments that mount a
            # manifest volume. They still must remain inside the workspace.
            self._manifest_base = self.workspace_root
        # A process-local lock prevents lost manifest updates from threads.  The
        # atomic replacement still leaves a valid manifest if a process crashes.
        self._lock = threading.RLock()
        self.max_bytes = max_bytes
        self.allowed_mime_types = _normalise_allowed_mimes(allowed_mime_types)

    # ---- path helpers -------------------------------------------------

    @staticmethod
    def _area(area: str | None = None, *, root: str | None = None, kind: str | None = None,
              destination: str | None = None, output: bool | None = None) -> str:
        values = [value for value in (area, root, kind, destination) if value is not None]
        if output is not None:
            values.append("output" if output else "work")
        if not values:
            return "work"
        normalized = str(values[0]).strip().lower()
        aliases = {"intermediate": "work", "final": "output", "out": "output"}
        normalized = aliases.get(normalized, normalized)
        if any(aliases.get(str(value).strip().lower(), str(value).strip().lower()) != normalized
               for value in values[1:]):
            raise ArtifactPathError("conflicting artifact roots")
        if normalized not in _AREAS:
            raise ArtifactPathError("artifact root must be 'work' or 'output'")
        return normalized

    def _root_for(self, area: str) -> Path:
        return self.work_root if area == "work" else self.output_root

    def stage_dir(
        self,
        slug: str,
        stage: str,
        *,
        area: str | None = None,
        root: str | None = None,
        kind: str | None = None,
        destination: str | None = None,
        output: bool | None = None,
        create: bool = True,
    ) -> Path:
        safe_slug = _validate_component(slug, label="slug")
        safe_stage = _validate_component(stage, label="stage")
        safe_area = self._area(area, root=root, kind=kind, destination=destination, output=output)
        # Validate the area root itself before resolving children.  Otherwise
        # a malicious ``work``/``output`` symlink could redirect every stage
        # outside the configured workspace while still passing a child-relative
        # containment check.
        base = _contained(self._root_for(safe_area), self.workspace_root)
        path = _contained(base / safe_slug / safe_stage, base)
        if create:
            path.mkdir(parents=True, exist_ok=True)
            # Re-check after mkdir in case a pre-existing component was a symlink.
            path = _contained(path, base)
        return path

    def work_dir(self, slug: str, stage: str, *, create: bool = True) -> Path:
        return self.stage_dir(slug, stage, area="work", create=create)

    def output_dir(self, slug: str, stage: str, *, create: bool = True) -> Path:
        return self.stage_dir(slug, stage, area="output", create=create)

    def path_for(
        self,
        slug: str,
        stage: str,
        name: str | os.PathLike[str],
        *,
        area: str | None = None,
        root: str | None = None,
        kind: str | None = None,
        destination: str | None = None,
        output: bool | None = None,
        create: bool = False,
    ) -> Path:
        safe_area = self._area(area, root=root, kind=kind, destination=destination, output=output)
        safe_name = _validate_relative_name(name)
        base = self._root_for(safe_area).resolve(strict=False)
        stage_path = self.stage_dir(
            slug,
            stage,
            area=safe_area,
            create=create,
        )
        return _contained(stage_path / Path(*safe_name.split("/")), base)

    def resolve_path(self, path: str | os.PathLike[str]) -> Path:
        """Resolve a workspace-relative path without permitting escapes."""

        raw = os.fspath(path)
        if "\x00" in raw:
            raise PathTraversalError("path contains NUL")
        candidate = Path(raw).expanduser()
        if candidate.is_absolute() or PureWindowsPath(raw).drive:
            # Absolute paths are accepted only when they point inside this
            # workspace; this makes verification of ``record.absolute_path``
            # convenient without weakening containment.
            resolved = _contained(candidate, self.workspace_root)
        else:
            relative = _validate_relative_name(raw, label="workspace path")
            resolved = _contained(
                self.workspace_root / Path(*relative.split("/")), self.workspace_root
            )
        return resolved

    def absolute_path(self, artifact: ArtifactRecord | str | os.PathLike[str]) -> Path:
        if isinstance(artifact, ArtifactRecord):
            return self.resolve_path(artifact.path)
        return self.resolve_path(artifact)

    def manifest_path(self, slug: str) -> Path:
        safe_slug = _validate_component(slug, label="slug")
        base = _contained(self.manifest_root, self._manifest_base)
        return _contained(base / f"{safe_slug}.json", self._manifest_base)

    def control_path(self, name: str | os.PathLike[str]) -> Path:
        """Return a path in the private ``.framework`` control tree.

        Control files (plans/checkpoints) are intentionally separate from
        user-visible ``work`` and ``output`` artifacts and are never exposed
        through the artifact manifest.  Only workspace-relative names are
        accepted and the path is re-resolved to defend against symlink swaps.
        """

        safe_name = _validate_relative_name(name, label="control path")
        return _contained(
            self.framework_root / Path(*safe_name.split("/")), self.framework_root
        )

    def write_control_text(
        self,
        name: str | os.PathLike[str],
        data: str,
        *,
        encoding: str = "utf-8",
        max_bytes: int | None = None,
    ) -> Path:
        """Atomically write a planner/checkpoint control file."""

        if not isinstance(data, str):
            raise TypeError("control data must be text")
        payload = data.encode(encoding)
        limit = self.max_bytes if max_bytes is None else max_bytes
        if limit is not None and len(payload) > limit:
            raise ArtifactValidationError(
                f"control file exceeds maximum size of {limit} bytes"
            )
        path = self.control_path(name)
        with self._lock:
            try:
                self._atomic_write_file(path, (payload,), max_bytes=limit)
            except (OSError, ArtifactPathError) as exc:
                raise ManifestError(f"cannot write control file: {path}") from exc
        return path

    def read_control_text(
        self, name: str | os.PathLike[str], *, encoding: str = "utf-8"
    ) -> str:
        """Read a private control file after applying the same containment check."""

        return self.control_path(name).read_text(encoding=encoding)

    def write_control_json(
        self,
        name: str | os.PathLike[str],
        value: Mapping[str, Any],
        *,
        indent: int = 2,
    ) -> Path:
        try:
            payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=indent) + "\n"
        except (TypeError, ValueError) as exc:
            raise ArtifactValidationError("control value must be JSON serializable") from exc
        return self.write_control_text(name, payload)

    def read_control_json(self, name: str | os.PathLike[str]) -> dict[str, Any]:
        try:
            value = json.loads(self.read_control_text(name))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ManifestError("control file is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ManifestError("control JSON must be an object")
        return value

    # ---- manifest I/O -------------------------------------------------

    def _new_manifest(self, slug: str) -> ArtifactManifest:
        return ArtifactManifest(slug=slug)

    def load_manifest(self, slug: str) -> ArtifactManifest:
        path = self.manifest_path(slug)
        try:
            payload = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self._new_manifest(_validate_component(slug, label="slug"))
        except OSError as exc:
            raise ManifestError(f"cannot read artifact manifest: {path}") from exc
        try:
            manifest = ArtifactManifest.from_json(payload)
        except ManifestError:
            raise
        except Exception as exc:  # defensive boundary for malformed third-party data
            raise ManifestError(f"cannot parse artifact manifest: {path}") from exc
        if manifest.slug != slug:
            raise ManifestError("manifest slug does not match requested slug")
        return manifest

    # Common aliases used by API adapters.
    read_manifest = load_manifest

    def manifest_for(self, slug: str) -> ArtifactManifest:
        return self.load_manifest(slug)

    def manifest(self, slug: str) -> ArtifactManifest:
        return self.load_manifest(slug)

    def write_manifest(self, manifest: ArtifactManifest) -> Path:
        """Compatibility alias for :meth:`save_manifest`."""

        return self.save_manifest(manifest)

    def _atomic_write_file(
        self,
        path: Path,
        chunks: Iterable[bytes],
        *,
        min_bytes: int = 0,
        max_bytes: int | None,
    ) -> tuple[int, str]:
        path.parent.mkdir(parents=True, exist_ok=True)
        _contained(path.parent, self.workspace_root)
        fd: int | None = None
        temp_name: str | None = None
        digest = hashlib.sha256()
        size = 0
        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
            )
            with os.fdopen(fd, "wb") as handle:
                fd = None
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise ArtifactValidationError("artifact chunks must be bytes")
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise ArtifactValidationError(
                            f"artifact exceeds maximum size of {max_bytes} bytes"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
                if size < min_bytes:
                    raise ArtifactValidationError(
                        f"artifact is smaller than minimum size of {min_bytes} bytes"
                    )
                handle.flush()
                os.fsync(handle.fileno())
            # Re-check the destination and parent after writing.  This catches
            # a stage symlink introduced while the temporary file was open.
            _contained(path.parent, self.workspace_root)
            os.replace(temp_name, path)
            temp_name = None
            _fsync_directory(path.parent)
            return size, digest.hexdigest()
        finally:
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
            if temp_name is not None:
                with contextlib.suppress(OSError):
                    os.unlink(temp_name)

    def _atomic_write_json(self, path: Path, payload: str) -> None:
        # Manifest files live below the workspace's framework directory, not in
        # either artifact tree, but still receive the same atomic-write treatment.
        path.parent.mkdir(parents=True, exist_ok=True)
        _contained(path.parent, self._manifest_base)
        fd: int | None = None
        temp_name: str | None = None
        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
            )
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                fd = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _contained(path.parent, self._manifest_base)
            os.replace(temp_name, path)
            temp_name = None
            _fsync_directory(path.parent)
        finally:
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
            if temp_name is not None:
                with contextlib.suppress(OSError):
                    os.unlink(temp_name)

    def save_manifest(self, manifest: ArtifactManifest) -> Path:
        if not isinstance(manifest, ArtifactManifest):
            raise TypeError("manifest must be an ArtifactManifest")
        path = self.manifest_path(manifest.slug)
        with self._lock:
            try:
                self._atomic_write_json(path, manifest.to_json())
            except (OSError, ArtifactPathError) as exc:
                raise ManifestError(f"cannot write artifact manifest: {path}") from exc
        return path

    # ---- writing and registration ------------------------------------

    @staticmethod
    def _chunks(data: object, *, encoding: str) -> Iterator[bytes]:
        if isinstance(data, str):
            yield data.encode(encoding)
            return
        if isinstance(data, bytes):
            yield data
            return
        if isinstance(data, (bytearray, memoryview)):
            yield bytes(data)
            return
        if hasattr(data, "read"):
            reader = data  # type: ignore[assignment]
            while True:
                chunk = reader.read(1024 * 1024)
                if chunk == b"" or chunk == "":
                    return
                if isinstance(chunk, str):
                    chunk = chunk.encode(encoding)
                if not isinstance(chunk, bytes):
                    raise ArtifactValidationError("artifact stream must yield bytes or text")
                yield chunk
            return
        if isinstance(data, Iterable):
            for chunk in data:
                if isinstance(chunk, str):
                    chunk = chunk.encode(encoding)
                elif isinstance(chunk, (bytearray, memoryview)):
                    chunk = bytes(chunk)
                if not isinstance(chunk, bytes):
                    raise ArtifactValidationError("artifact chunks must be bytes or text")
                yield chunk
            return
        raise TypeError("artifact data must be bytes, text, a stream, or byte chunks")

    def _validate_write_options(
        self,
        name: str,
        *,
        mime_type: str | None,
        expected_mime: str | None,
        allowed_mime_types: str | Iterable[str] | None,
        min_size: int,
        max_size: int | None,
    ) -> tuple[str, int | None]:
        if min_size < 0:
            raise ValueError("min_size must be non-negative")
        if max_size is not None and (not isinstance(max_size, int) or max_size < 0):
            raise ValueError("max_size must be a non-negative integer or None")
        if max_size is not None and min_size > max_size:
            raise ValueError("min_size cannot exceed max_size")
        actual_mime = _normalise_mime(mime_type) or guess_mime(name)
        expected = _normalise_mime(expected_mime)
        if expected and not _mime_matches(actual_mime, expected):
            raise ArtifactValidationError(
                f"artifact MIME type {actual_mime!r} does not match expected {expected!r}"
            )
        call_allowed = _normalise_allowed_mimes(allowed_mime_types)
        if self.allowed_mime_types and not any(
            _mime_matches(actual_mime, candidate) for candidate in self.allowed_mime_types
        ):
            raise ArtifactValidationError(f"artifact MIME type {actual_mime!r} is not allowed")
        if call_allowed and not any(
            _mime_matches(actual_mime, candidate) for candidate in call_allowed
        ):
            raise ArtifactValidationError(f"artifact MIME type {actual_mime!r} is not allowed")
        effective_max = max_size if max_size is not None else self.max_bytes
        return actual_mime, effective_max

    def _record_for(
        self,
        *,
        slug: str,
        stage: str,
        name: str,
        area: str,
        path: Path,
        size: int,
        digest: str,
        mime_type: str,
        metadata: Mapping[str, Any] | None,
        attempt: int | None,
        min_size: int,
    ) -> ArtifactRecord:
        if size < min_size:
            raise ArtifactValidationError(
                f"artifact is smaller than minimum size of {min_size} bytes"
            )
        # The file must have the size that was hashed.  A replaced destination
        # or a filesystem error must never produce a misleading manifest entry.
        try:
            actual_size = path.stat().st_size
        except OSError as exc:
            raise ArtifactIntegrityError(f"artifact disappeared after write: {path}") from exc
        if actual_size != size:
            raise ArtifactIntegrityError(
                f"artifact size changed during write: expected {size}, got {actual_size}"
            )
        return ArtifactRecord(
            path=path.relative_to(self.workspace_root).as_posix(),
            slug=slug,
            stage=stage,
            name=name,
            area=area,
            size_bytes=size,
            sha256=digest,
            mime_type=mime_type,
            metadata=dict(metadata or {}),
            attempt=attempt,
        )

    def write(
        self,
        slug: str,
        stage: str,
        name: str | os.PathLike[str],
        data: object,
        *,
        area: str | None = None,
        root: str | None = None,
        kind: str | None = None,
        destination: str | None = None,
        output: bool | None = None,
        encoding: str = "utf-8",
        mime_type: str | None = None,
        content_type: str | None = None,
        expected_mime: str | None = None,
        allowed_mime_types: str | Iterable[str] | None = None,
        min_size: int = 0,
        max_size: int | None = None,
        max_bytes: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        attempt: int | None = None,
        overwrite: bool = True,
        update_manifest: bool = True,
    ) -> ArtifactRecord:
        """Atomically write an artifact and update its slug manifest.

        ``area='work'`` is the default; pass ``area='output'`` (or
        ``output=True``) for final deliverables.  ``data`` may be bytes, text,
        a binary/text stream, or an iterable of chunks.
        """

        if content_type is not None:
            if mime_type is not None and _normalise_mime(mime_type) != _normalise_mime(
                content_type
            ):
                raise ArtifactValidationError("mime_type and content_type disagree")
            mime_type = content_type
        if max_bytes is not None:
            if max_size is not None and max_size != max_bytes:
                raise ValueError("max_size and max_bytes disagree")
            max_size = max_bytes
        safe_slug = _validate_component(slug, label="slug")
        safe_stage = _validate_component(stage, label="stage")
        safe_name = _validate_relative_name(name)
        safe_area = self._area(area, root=root, kind=kind, destination=destination, output=output)
        actual_mime, effective_max = self._validate_write_options(
            safe_name,
            mime_type=mime_type,
            expected_mime=expected_mime,
            allowed_mime_types=allowed_mime_types,
            min_size=min_size,
            max_size=max_size,
        )
        path = self.path_for(safe_slug, safe_stage, safe_name, area=safe_area, create=True)
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        _json_value(dict(metadata or {}), field_name="artifact metadata")

        with self._lock:
            size, digest = self._atomic_write_file(
                path,
                self._chunks(data, encoding=encoding),
                min_bytes=min_size,
                max_bytes=effective_max,
            )
            record = self._record_for(
                slug=safe_slug,
                stage=safe_stage,
                name=safe_name,
                area=safe_area,
                path=path,
                size=size,
                digest=digest,
                mime_type=actual_mime,
                metadata=metadata,
                attempt=attempt,
                min_size=min_size,
            )
            if update_manifest:
                manifest = self.load_manifest(safe_slug)
                manifest.upsert(record)
                self.save_manifest(manifest)
            return record

    def write_bytes(
        self,
        slug: str,
        stage: str,
        name: str | os.PathLike[str],
        data: bytes,
        **kwargs: Any,
    ) -> ArtifactRecord:
        return self.write(slug, stage, name, data, **kwargs)

    def write_text(
        self,
        slug: str,
        stage: str,
        name: str | os.PathLike[str],
        data: str,
        *,
        encoding: str = "utf-8",
        **kwargs: Any,
    ) -> ArtifactRecord:
        return self.write(slug, stage, name, data, encoding=encoding, **kwargs)

    def register(
        self,
        slug: str,
        stage: str,
        name: str | os.PathLike[str],
        *,
        area: str | None = None,
        root: str | None = None,
        kind: str | None = None,
        destination: str | None = None,
        output: bool | None = None,
        mime_type: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        attempt: int | None = None,
        min_size: int = 0,
        max_size: int | None = None,
        allowed_mime_types: str | Iterable[str] | None = None,
        update_manifest: bool = True,
    ) -> ArtifactRecord:
        """Register a file written by another component after verifying it."""

        safe_slug = _validate_component(slug, label="slug")
        safe_stage = _validate_component(stage, label="stage")
        safe_name = _validate_relative_name(name)
        safe_area = self._area(area, root=root, kind=kind, destination=destination, output=output)
        path = self.path_for(safe_slug, safe_stage, safe_name, area=safe_area, create=False)
        if not path.is_file():
            raise FileNotFoundError(path)
        actual_mime, effective_max = self._validate_write_options(
            safe_name,
            mime_type=mime_type,
            expected_mime=None,
            allowed_mime_types=allowed_mime_types,
            min_size=min_size,
            max_size=max_size,
        )
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                size += len(chunk)
                if effective_max is not None and size > effective_max:
                    raise ArtifactValidationError(
                        f"artifact exceeds maximum size of {effective_max} bytes"
                    )
                digest.update(chunk)
        record = self._record_for(
            slug=safe_slug,
            stage=safe_stage,
            name=safe_name,
            area=safe_area,
            path=path,
            size=size,
            digest=digest.hexdigest(),
            mime_type=actual_mime,
            metadata=metadata,
            attempt=attempt,
            min_size=min_size,
        )
        if update_manifest:
            with self._lock:
                manifest = self.load_manifest(safe_slug)
                manifest.upsert(record)
                self.save_manifest(manifest)
        return record

    register_path = register

    def copy_from(
        self,
        slug: str,
        stage: str,
        name: str | os.PathLike[str],
        source: str | os.PathLike[str],
        **kwargs: Any,
    ) -> ArtifactRecord:
        source_path = Path(source).expanduser()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        with source_path.open("rb") as handle:
            return self.write(slug, stage, name, handle, **kwargs)

    # ---- reading, listing and integrity ------------------------------

    def read_bytes(self, artifact: ArtifactRecord | str | os.PathLike[str]) -> bytes:
        return self.absolute_path(artifact).read_bytes()

    def read_text(
        self,
        artifact: ArtifactRecord | str | os.PathLike[str],
        *,
        encoding: str = "utf-8",
    ) -> str:
        return self.absolute_path(artifact).read_text(encoding=encoding)

    def open(
        self,
        artifact: ArtifactRecord | str | os.PathLike[str],
        mode: str = "rb",
        *,
        encoding: str | None = None,
    ) -> BinaryIO | TextIO:
        path = self.absolute_path(artifact)
        if "b" in mode:
            return cast(BinaryIO, path.open(mode))
        return cast(TextIO, path.open(mode, encoding=encoding or "utf-8"))

    def list_artifacts(self, slug: str) -> list[ArtifactRecord]:
        return list(self.load_manifest(slug).artifacts)

    def artifacts(self, slug: str) -> list[ArtifactRecord]:
        return self.list_artifacts(slug)

    def verify(
        self,
        artifact: ArtifactRecord | str | os.PathLike[str],
        *,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
        expected_mime: str | None = None,
        raise_on_error: bool = False,
    ) -> bool:
        """Verify a file against optional expectations or a manifest record."""

        record = artifact if isinstance(artifact, ArtifactRecord) else None
        path = self.absolute_path(artifact)
        try:
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
            actual_digest = digest.hexdigest()
            if record is not None:
                expected_sha256 = record.sha256
                expected_size = record.size_bytes
                expected_mime = record.mime_type
            if expected_sha256 and actual_digest != expected_sha256.lower():
                raise ArtifactIntegrityError("artifact SHA-256 does not match manifest")
            if expected_size is not None and size != expected_size:
                raise ArtifactIntegrityError("artifact size does not match manifest")
            if expected_mime:
                actual_mime = guess_mime(path.name)
                if not _mime_matches(actual_mime, expected_mime):
                    raise ArtifactIntegrityError("artifact MIME type does not match manifest")
            return True
        except (OSError, ArtifactIntegrityError) as exc:
            if raise_on_error:
                if isinstance(exc, ArtifactIntegrityError):
                    raise
                raise ArtifactIntegrityError(f"cannot verify artifact: {path}") from exc
            return False

    def verify_artifact(self, artifact: ArtifactRecord | str | os.PathLike[str]) -> bool:
        return self.verify(artifact, raise_on_error=True)

    def verify_manifest(self, slug: str, *, raise_on_error: bool = False) -> bool:
        manifest = self.load_manifest(slug)
        try:
            for artifact in manifest.artifacts:
                self.verify(artifact, raise_on_error=True)
            return True
        except ArtifactIntegrityError:
            if raise_on_error:
                raise
            return False

    def remove(
        self,
        artifact: ArtifactRecord | str | os.PathLike[str],
        *,
        slug: str | None = None,
        update_manifest: bool = True,
        missing_ok: bool = False,
    ) -> bool:
        path = self.absolute_path(artifact)
        try:
            path.unlink()
        except FileNotFoundError:
            if not missing_ok:
                raise
            removed = False
        else:
            removed = True
        if update_manifest:
            target_slug = slug or (artifact.slug if isinstance(artifact, ArtifactRecord) else None)
            if target_slug:
                manifest = self.load_manifest(target_slug)
                relative = path.relative_to(self.workspace_root).as_posix()
                if manifest.remove(relative):
                    self.save_manifest(manifest)
        return removed
