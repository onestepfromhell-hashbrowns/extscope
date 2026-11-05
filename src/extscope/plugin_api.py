"""Versioned neutral preview contracts; trusted in-process plugins are not sandboxes."""
from __future__ import annotations
from dataclasses import dataclass, field
from .binding import SourceRoots
from .config.paths import _path

API_VERSION = 2
SCHEMA_VERSION = 2


def validate_path(value):
    """Validate an explicit absolute normalized POSIX path, without filesystem I/O."""
    return _path(value)


@dataclass(frozen=True)
class PluginContext:
    sources: SourceRoots
    target_roots: tuple[str, ...]
    states: object | None = field(default=None, repr=False)

    def read_target(self, target):
        if self.states is None:
            raise ValueError('target state read requires explicit grants')
        return self.states.read(target)


@dataclass(frozen=True)
class TargetOperation:
    operation: str
    source: str
    target: str
    source_sha256: str
    source_kind: str
    source_mode: int
    algorithm: str
    block_id: str | None = None


@dataclass(frozen=True)
class SourceRequirement:
    source: str
    source_kind: str
    algorithm: str
    sha256: str


@dataclass(frozen=True)
class PluginPlan:
    operations: tuple[TargetOperation | "WriteOperation", ...]
    dependencies: tuple[SourceRequirement, ...] = ()


@dataclass(frozen=True)
class TargetExpectation:
    target: str
    exists: bool
    sha256: str | None
    mode: int | None
    fingerprint: tuple[int, ...] | None


@dataclass(frozen=True)
class TargetState:
    expected: TargetExpectation
    content: bytes | None = field(repr=False)


@dataclass(frozen=True)
class WriteOperation:
    target: str
    data: bytes = field(repr=False)
    mode: int
    expected: TargetExpectation
    block_ids: tuple[str, ...] = ()

    @property
    def operation(self):
        return 'write_file'
