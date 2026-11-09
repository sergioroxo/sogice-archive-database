"""Deterministic, complete partitions of canonical text into sections and passages.

The builders in this module are model-free. They preserve every Python
character in the supplied canonical string and never scan configured roots.

Two partitions are cut from one canonical text:

- sections (about 16,000 characters) for model calls on long documents;
- passages, called citation units in the old code (about 1,500 characters),
  which quotes are located in.

Both are gap-free and overlap-free: joining the spans gives back the exact
text, and every span carries its own offsets and SHA-256.

Ported from the old repository (``runner/pipeline/citation_units_v2.py``).
The old version used pydantic models; this one uses frozen dataclasses with
the same fields, the same validation rules and the same JSON form, so artifact
hashes are unchanged. Validation failures raise ``ValidationError`` (a
``ValueError``). The pydantic calls map as follows:

- ``model_dump(mode="json")``        -> ``to_dict()``
- ``Model.model_validate_json(raw)`` -> ``Model.from_json(raw)``
- ``Model.model_validate(dict)``     -> ``Model.from_dict(dict)``
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, ClassVar, Literal

from .atomic_io import atomic_write_json


SECTIONS_V2_FILENAME = "sections_v2.json"
CITATION_UNITS_V2_FILENAME = "citation_units_v2.json"
V2_READY_FILENAME = "v2_artifacts.ready.json"
SECTION_POLICY_VERSION = "complete-section-partition-v2.0"
UNIT_POLICY_VERSION = "complete-citation-unit-partition-v2.0"
SECTIONS_SCHEMA_VERSION = "sections-v2.0"
UNITS_SCHEMA_VERSION = "citation-units-v2.0"


class ValidationError(ValueError):
    """A passage or section artifact broke one of its invariants."""


# ---------------------------------------------------------------- helpers
# Inlined from the old runner.models.reprocessing and extraction_quality.

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    payload = value.to_dict() if hasattr(value, "to_dict") else value
    return (json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ) + "\n").encode("utf-8")


def require_sha256(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValidationError(f"{field} must be a lowercase SHA-256 digest")
    return value


def require_safe_id(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value):
        raise ValidationError(f"{field} is not a safe portable identifier")
    return value


def require_relative_path(value: str, *, field: str) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or "\\" in value or "\x00" in value):
        raise ValidationError(f"{field} is not a safe relative path")
    path = PurePosixPath(value)
    if value.startswith("/") or path.is_absolute() or ".." in path.parts:
        raise ValidationError(f"{field} is not a safe relative path")
    if any(part in {"", "."} for part in path.parts):
        raise ValidationError(f"{field} is not a canonical relative path")
    first = path.parts[0]
    if len(first) >= 2 and first[1] == ":":
        raise ValidationError(f"{field} must not contain a drive-qualified path")
    return value


def _require_str(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")


def _require_int(value: Any, field: str, *, minimum: int) -> None:
    # Strict: no bools, floats or numeric strings (as pydantic strict mode).
    if type(value) is not int:
        raise ValidationError(f"{field} must be an integer")
    if value < minimum:
        raise ValidationError(f"{field} must be at least {minimum}")


def _fields_from_dict(cls: type, data: Any) -> dict:
    """Check keys like pydantic ``extra="forbid"``: no unknown, none missing."""
    if not isinstance(data, dict):
        raise ValidationError(f"{cls.__name__} must be an object")
    fields = {f.name: f for f in dataclasses.fields(cls)}
    unknown = set(data) - set(fields)
    if unknown:
        raise ValidationError(f"{cls.__name__} has unknown fields: {sorted(unknown)}")
    missing = [
        name for name, f in fields.items()
        if name not in data
        and f.default is dataclasses.MISSING
        and f.default_factory is dataclasses.MISSING  # type: ignore[misc]
    ]
    if missing:
        raise ValidationError(f"{cls.__name__} is missing fields: {missing}")
    return dict(data)


def _load_json(raw: str | bytes) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValidationError(f"invalid JSON: {error}") from error


# ---------------------------------------------------------------- records

@dataclass(frozen=True)
class EvidenceSpanV2:
    """One section or passage: an exact, hashed slice of the canonical text."""

    doc_id: str
    span_id: str
    sequence: int
    canonical_text_sha256: str
    char_start: int
    char_end: int
    char_count: int
    text: str
    text_sha256: str
    source_artifact: str
    source_version: str

    def __post_init__(self) -> None:
        for name in ("doc_id", "span_id", "canonical_text_sha256", "text",
                     "text_sha256", "source_artifact", "source_version"):
            _require_str(getattr(self, name), name)
        _require_int(self.sequence, "sequence", minimum=1)
        _require_int(self.char_start, "char_start", minimum=0)
        _require_int(self.char_end, "char_end", minimum=1)
        _require_int(self.char_count, "char_count", minimum=1)
        require_safe_id(self.doc_id, field="doc_id")
        require_safe_id(self.span_id, field="span_id")
        require_sha256(self.canonical_text_sha256, field="canonical_text_sha256")
        require_sha256(self.text_sha256, field="text_sha256")
        require_relative_path(self.source_artifact, field="source_artifact")
        value = self.source_version
        if not value or value != value.strip() or len(value) > 100:
            raise ValidationError("source_version is invalid")
        if self.char_end <= self.char_start:
            raise ValidationError("evidence span must have a positive range")
        if self.char_count != self.char_end - self.char_start:
            raise ValidationError("evidence span character count is inconsistent")
        if self.char_count != len(self.text):
            raise ValidationError("evidence span offsets and text disagree")
        if self.text_sha256 != sha256_text(self.text):
            raise ValidationError("evidence span text hash mismatch")

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "EvidenceSpanV2":
        return cls(**_fields_from_dict(cls, data))


@dataclass(frozen=True)
class SpanSizeDistributionV2:
    count: int
    minimum: int
    maximum: int
    p50: int
    p95: int
    total: int

    def __post_init__(self) -> None:
        for name in ("count", "minimum", "maximum", "p50", "p95", "total"):
            _require_int(getattr(self, name), name, minimum=1)
        if not self.minimum <= self.p50 <= self.p95 <= self.maximum:
            raise ValidationError("span size distribution is inconsistent")

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "SpanSizeDistributionV2":
        return cls(**_fields_from_dict(cls, data))


@dataclass(frozen=True, kw_only=True)
class _PartitionBase:
    doc_id: str
    canonical_text_sha256: str
    canonical_char_count: int
    source_artifact: str
    source_version: str
    policy_version: str
    target_chars: int
    hard_max_chars: int
    spans: tuple[EvidenceSpanV2, ...]
    size_distribution: SpanSizeDistributionV2
    schema_version: str

    SCHEMA_VERSION: ClassVar[str] = ""
    POLICY_VERSION: ClassVar[str] = ""

    def __post_init__(self) -> None:
        for name in ("doc_id", "canonical_text_sha256", "source_artifact",
                     "source_version", "policy_version", "schema_version"):
            _require_str(getattr(self, name), name)
        _require_int(self.canonical_char_count, "canonical_char_count", minimum=1)
        _require_int(self.target_chars, "target_chars", minimum=1)
        _require_int(self.hard_max_chars, "hard_max_chars", minimum=1)
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValidationError(f"schema_version must be {self.SCHEMA_VERSION!r}")
        if self.policy_version != self.POLICY_VERSION:
            raise ValidationError(f"policy_version must be {self.POLICY_VERSION!r}")
        if not isinstance(self.spans, tuple) or not all(
                isinstance(span, EvidenceSpanV2) for span in self.spans):
            raise ValidationError("spans must be a tuple of EvidenceSpanV2")
        if not isinstance(self.size_distribution, SpanSizeDistributionV2):
            raise ValidationError("size_distribution must be a SpanSizeDistributionV2")
        require_safe_id(self.doc_id, field="doc_id")
        require_sha256(self.canonical_text_sha256, field="canonical_text_sha256")
        require_relative_path(self.source_artifact, field="source_artifact")

        if self.hard_max_chars < self.target_chars:
            raise ValidationError("hard maximum must be at least the target size")
        if not self.spans:
            raise ValidationError("partition cannot be empty")
        if len({span.span_id for span in self.spans}) != len(self.spans):
            raise ValidationError("duplicate evidence span IDs")
        expected_start = 0
        for sequence, span in enumerate(self.spans, start=1):
            if span.sequence != sequence:
                raise ValidationError("evidence spans are not deterministically ordered")
            if span.doc_id != self.doc_id:
                raise ValidationError("evidence span document ID mismatch")
            if span.canonical_text_sha256 != self.canonical_text_sha256:
                raise ValidationError("evidence span canonical hash mismatch")
            if span.source_artifact != self.source_artifact:
                raise ValidationError("evidence span source artifact mismatch")
            if span.source_version != self.source_version:
                raise ValidationError("evidence span source version mismatch")
            if span.char_start != expected_start:
                raise ValidationError("evidence spans contain a gap, overlap, or reordering")
            if span.char_count > self.hard_max_chars:
                raise ValidationError("evidence span exceeds the hard maximum")
            expected_start = span.char_end
        if expected_start != self.canonical_char_count:
            raise ValidationError("evidence spans do not cover canonical text")
        reconstructed = "".join(span.text for span in self.spans)
        if len(reconstructed) != self.canonical_char_count:
            raise ValidationError("reconstructed text character count mismatch")
        if sha256_text(reconstructed) != self.canonical_text_sha256:
            raise ValidationError("reconstructed text hash mismatch")
        if self.size_distribution != _distribution(
            tuple(span.char_count for span in self.spans)
        ):
            raise ValidationError("span size distribution is inconsistent")

    def to_dict(self) -> dict:
        """JSON-ready dict (the old ``model_dump(mode="json")``)."""
        return {
            "doc_id": self.doc_id,
            "canonical_text_sha256": self.canonical_text_sha256,
            "canonical_char_count": self.canonical_char_count,
            "source_artifact": self.source_artifact,
            "source_version": self.source_version,
            "policy_version": self.policy_version,
            "target_chars": self.target_chars,
            "hard_max_chars": self.hard_max_chars,
            "spans": [span.to_dict() for span in self.spans],
            "size_distribution": self.size_distribution.to_dict(),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: Any):
        values = _fields_from_dict(cls, data)
        spans = values.get("spans")
        if not isinstance(spans, (list, tuple)):
            raise ValidationError("spans must be a list")
        values["spans"] = tuple(EvidenceSpanV2.from_dict(row) for row in spans)
        values["size_distribution"] = SpanSizeDistributionV2.from_dict(
            values.get("size_distribution"))
        return cls(**values)

    @classmethod
    def from_json(cls, raw: str | bytes):
        return cls.from_dict(_load_json(raw))


@dataclass(frozen=True, kw_only=True)
class SectionsV2(_PartitionBase):
    policy_version: str = SECTION_POLICY_VERSION
    schema_version: str = SECTIONS_SCHEMA_VERSION

    SCHEMA_VERSION: ClassVar[str] = SECTIONS_SCHEMA_VERSION
    POLICY_VERSION: ClassVar[str] = SECTION_POLICY_VERSION


@dataclass(frozen=True, kw_only=True)
class CitationUnitsV2(_PartitionBase):
    policy_version: str = UNIT_POLICY_VERSION
    schema_version: str = UNITS_SCHEMA_VERSION

    SCHEMA_VERSION: ClassVar[str] = UNITS_SCHEMA_VERSION
    POLICY_VERSION: ClassVar[str] = UNIT_POLICY_VERSION


def _distribution(sizes: tuple[int, ...]) -> SpanSizeDistributionV2:
    ordered = sorted(sizes)

    def percentile(fraction: float) -> int:
        return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]

    return SpanSizeDistributionV2(
        count=len(ordered),
        minimum=ordered[0],
        maximum=ordered[-1],
        p50=percentile(0.50),
        p95=percentile(0.95),
        total=sum(ordered),
    )


# ---------------------------------------------------------------- partitioning

_SENTENCE_END_RE = re.compile(r"[.!?](?:[\"'’”)]*)\s", re.UNICODE)


def _choose_end(text: str, start: int, target: int, hard_max: int) -> int:
    hard_end = min(len(text), start + hard_max)
    if hard_end == len(text):
        return hard_end
    ideal = min(hard_end, start + target)
    lower = start + max(1, target // 2)
    window = text[lower:hard_end]

    structural = [lower + match.end() for match in re.finditer(r"(?:\r?\n){2,}", window)]
    candidates = [value for value in structural if value <= ideal]
    if candidates:
        return candidates[-1]

    sentences = [lower + match.end() for match in _SENTENCE_END_RE.finditer(window)]
    candidates = [value for value in sentences if value <= ideal]
    if candidates:
        return candidates[-1]

    whitespace = [lower + match.end() for match in re.finditer(r"\s+", window)]
    candidates = [value for value in whitespace if value <= ideal]
    if candidates:
        return candidates[-1]
    return ideal


def _partition_offsets(text: str, *, target_chars: int, hard_max_chars: int) -> tuple[tuple[int, int], ...]:
    if not isinstance(text, str):
        raise TypeError("canonical_text must be a string")
    if not text:
        raise ValueError("canonical text cannot be empty")
    if target_chars <= 0 or hard_max_chars < target_chars:
        raise ValueError("invalid partition sizing policy")
    rows: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        end = _choose_end(text, start, target_chars, hard_max_chars)
        if end <= start:
            raise RuntimeError("partition policy failed to advance")
        rows.append((start, end))
        start = end
    return tuple(rows)


def _build_spans(
    canonical_text: str,
    *,
    doc_id: str,
    prefix: str,
    source_artifact: str,
    source_version: str,
    target_chars: int,
    hard_max_chars: int,
) -> tuple[EvidenceSpanV2, ...]:
    canonical_hash = sha256_text(canonical_text)
    rows: list[EvidenceSpanV2] = []
    for sequence, (start, end) in enumerate(
        _partition_offsets(
            canonical_text, target_chars=target_chars, hard_max_chars=hard_max_chars,
        ),
        start=1,
    ):
        span_text = canonical_text[start:end]
        text_hash = sha256_text(span_text)
        rows.append(EvidenceSpanV2(
            doc_id=doc_id,
            span_id=f"{prefix}-{sequence:06d}-{text_hash[:12]}",
            sequence=sequence,
            canonical_text_sha256=canonical_hash,
            char_start=start,
            char_end=end,
            char_count=end - start,
            text=span_text,
            text_sha256=text_hash,
            source_artifact=source_artifact,
            source_version=source_version,
        ))
    return tuple(rows)


def build_sections_v2(
    canonical_text: str,
    *,
    doc_id: str,
    source_artifact: str = "extracted.txt",
    source_version: str = "canonical-text-v2.0",
    target_chars: int = 16_000,
    hard_max_chars: int = 24_000,
) -> SectionsV2:
    spans = _build_spans(
        canonical_text,
        doc_id=doc_id,
        prefix="section",
        source_artifact=source_artifact,
        source_version=source_version,
        target_chars=target_chars,
        hard_max_chars=hard_max_chars,
    )
    return SectionsV2(
        doc_id=doc_id,
        canonical_text_sha256=sha256_text(canonical_text),
        canonical_char_count=len(canonical_text),
        source_artifact=source_artifact,
        source_version=source_version,
        policy_version=SECTION_POLICY_VERSION,
        target_chars=target_chars,
        hard_max_chars=hard_max_chars,
        spans=spans,
        size_distribution=_distribution(tuple(span.char_count for span in spans)),
    )


def build_citation_units_v2(
    canonical_text: str,
    *,
    doc_id: str,
    source_artifact: str = "extracted.txt",
    source_version: str = "canonical-text-v2.0",
    target_chars: int = 1_500,
    hard_max_chars: int = 1_900,
) -> CitationUnitsV2:
    spans = _build_spans(
        canonical_text,
        doc_id=doc_id,
        prefix="unit",
        source_artifact=source_artifact,
        source_version=source_version,
        target_chars=target_chars,
        hard_max_chars=hard_max_chars,
    )
    return CitationUnitsV2(
        doc_id=doc_id,
        canonical_text_sha256=sha256_text(canonical_text),
        canonical_char_count=len(canonical_text),
        source_artifact=source_artifact,
        source_version=source_version,
        policy_version=UNIT_POLICY_VERSION,
        target_chars=target_chars,
        hard_max_chars=hard_max_chars,
        spans=spans,
        size_distribution=_distribution(tuple(span.char_count for span in spans)),
    )


# Clearer names for the new code base.
build_passages = build_citation_units_v2
build_sections = build_sections_v2


def unit_records(units: _PartitionBase, ids=None) -> list[dict]:
    """Plain dict records for ``archive.quote_locator`` (old ``core_research.unit_records``)."""
    return [dict(document_id=units.doc_id, unit_id=u.span_id, text=u.text,
                 char_start=u.char_start, char_end=u.char_end, text_sha256=u.text_sha256,
                 canonical_sha256=units.canonical_text_sha256)
            for u in units.spans if ids is None or u.span_id in ids]


# ---------------------------------------------------------------- publication

def artifact_sha256(artifact: _PartitionBase) -> str:
    return hashlib.sha256(canonical_json_bytes(artifact)).hexdigest()


def canonical_artifact_bytes(artifact: _PartitionBase) -> bytes:
    return canonical_json_bytes(artifact)


def publish_v2_artifact_set(
    directory: Path,
    *,
    sections: SectionsV2,
    units: CitationUnitsV2,
    write_json: Callable[..., Path] = atomic_write_json,
) -> dict[str, str]:
    """Publish two artifacts and expose readiness only after both are durable."""
    if sections.doc_id != units.doc_id:
        raise ValueError("sections and units document IDs differ")
    if sections.canonical_text_sha256 != units.canonical_text_sha256:
        raise ValueError("sections and units canonical hashes differ")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    section_hash = artifact_sha256(sections)
    unit_hash = artifact_sha256(units)
    write_json(
        directory / SECTIONS_V2_FILENAME,
        sections.to_dict(),
        indent=None,
        ensure_ascii=False,
    )
    write_json(
        directory / CITATION_UNITS_V2_FILENAME,
        units.to_dict(),
        indent=None,
        ensure_ascii=False,
    )
    ready = {
        "schema_version": "v2-artifact-set-ready-v1.0",
        "doc_id": sections.doc_id,
        "canonical_text_sha256": sections.canonical_text_sha256,
        "sections_path": SECTIONS_V2_FILENAME,
        "sections_sha256": section_hash,
        "units_path": CITATION_UNITS_V2_FILENAME,
        "units_sha256": unit_hash,
    }
    write_json(directory / V2_READY_FILENAME, ready, indent=None, ensure_ascii=False)
    return ready


def inspect_v2_artifact_set(directory: Path) -> tuple[Literal["ready", "held"], str]:
    directory = Path(directory)
    marker = directory / V2_READY_FILENAME
    if not marker.is_file():
        return "held", "readiness_marker_missing"
    try:
        ready = json.loads(marker.read_text(encoding="utf-8"))
        expected_keys = {
            "schema_version", "doc_id", "canonical_text_sha256",
            "sections_path", "sections_sha256", "units_path", "units_sha256",
        }
        if not isinstance(ready, dict) or set(ready) != expected_keys:
            return "held", "readiness_marker_malformed"
        for key in ("sections_path", "units_path"):
            require_relative_path(ready[key], field=key)
        section_path = directory / ready["sections_path"]
        unit_path = directory / ready["units_path"]
        sections = SectionsV2.from_json(section_path.read_bytes())
        units = CitationUnitsV2.from_json(unit_path.read_bytes())
        if artifact_sha256(sections) != ready["sections_sha256"]:
            return "held", "sections_hash_mismatch"
        if artifact_sha256(units) != ready["units_sha256"]:
            return "held", "units_hash_mismatch"
        if sections.doc_id != units.doc_id or sections.doc_id != ready["doc_id"]:
            return "held", "document_identity_mismatch"
        if (
            sections.canonical_text_sha256 != units.canonical_text_sha256
            or sections.canonical_text_sha256 != ready["canonical_text_sha256"]
        ):
            return "held", "canonical_identity_mismatch"
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return "held", "artifact_set_invalid"
    return "ready", "verified_complete_pair"
