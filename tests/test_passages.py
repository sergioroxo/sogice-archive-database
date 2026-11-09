"""Ported from the old tests/test_citation_units_v2.py (pydantic calls replaced)."""
import copy
import dataclasses
import json

import pytest

from archive.atomic_io import atomic_write_json
from archive.passages import (
    CITATION_UNITS_V2_FILENAME,
    V2_READY_FILENAME,
    CitationUnitsV2,
    SectionsV2,
    ValidationError,
    artifact_sha256,
    build_citation_units_v2,
    build_passages,
    build_sections,
    build_sections_v2,
    canonical_artifact_bytes,
    inspect_v2_artifact_set,
    publish_v2_artifact_set,
    sha256_text,
    unit_records,
)


def _assert_complete_partition(artifact, canonical):
    assert artifact.spans[0].char_start == 0
    assert artifact.spans[-1].char_end == len(canonical)
    assert "".join(span.text for span in artifact.spans) == canonical
    assert sum(span.char_count for span in artifact.spans) == len(canonical)
    assert artifact.canonical_text_sha256 == sha256_text(canonical)
    for left, right in zip(artifact.spans, artifact.spans[1:]):
        assert left.char_end == right.char_start


def test_sections_and_units_exactly_reconstruct_unicode_whitespace_and_crlf():
    canonical = "  Første 🙂\r\n\r\nAndre linje.\r\n\tTredje\n\nশেষ  "
    sections = build_sections_v2(canonical, doc_id="unicode-doc", target_chars=20, hard_max_chars=30)
    units = build_citation_units_v2(canonical, doc_id="unicode-doc", target_chars=12, hard_max_chars=18)

    _assert_complete_partition(sections, canonical)
    _assert_complete_partition(units, canonical)
    assert sections.spans[0].text.startswith("  ")
    assert sections.spans[-1].text.endswith("  ")
    assert "\r\n" in "".join(span.text for span in units.spans)


def test_blank_line_free_60k_text_is_bounded_and_p95_below_2000():
    canonical = "x" * 60_000
    units = build_citation_units_v2(canonical, doc_id="solid-doc")

    _assert_complete_partition(units, canonical)
    assert len(units.spans) > 1
    assert units.size_distribution.maximum <= 1_900
    assert units.size_distribution.p95 < 2_000


def test_same_input_and_policy_produce_byte_identical_artifacts():
    canonical = ("Paragraph one.\n\nParagraph two!\n" * 400) + "tail"
    first = build_citation_units_v2(canonical, doc_id="stable-doc")
    second = build_citation_units_v2(canonical, doc_id="stable-doc")

    assert canonical_artifact_bytes(first) == canonical_artifact_bytes(second)
    assert artifact_sha256(first) == artifact_sha256(second)


@pytest.mark.parametrize(
    "tamper",
    [
        lambda row: row["spans"][0].update(text="tampered"),
        lambda row: row["spans"][0].update(text_sha256="0" * 64),
        lambda row: row["spans"][0].update(char_end=row["spans"][0]["char_end"] + 1),
        lambda row: row["spans"][1].update(char_start=row["spans"][1]["char_start"] - 1),
        lambda row: row["spans"].append(copy.deepcopy(row["spans"][0])),
        lambda row: row.update(canonical_text_sha256="0" * 64),
        lambda row: row.update(extra="forbidden"),
        lambda row: row.update(source_artifact="../unsafe.txt"),
    ],
)
def test_tampered_units_are_rejected(tamper):
    artifact = build_citation_units_v2("one two three four five", doc_id="tamper-doc", target_chars=8, hard_max_chars=10)
    payload = artifact.to_dict()
    tamper(payload)
    with pytest.raises(ValidationError):
        CitationUnitsV2.from_json(json.dumps(payload, ensure_ascii=False))


def test_empty_text_stale_hash_gaps_and_unknown_section_fields_fail():
    with pytest.raises(ValueError, match="cannot be empty"):
        build_sections_v2("", doc_id="empty-doc")

    artifact = build_sections_v2("a" * 100, doc_id="section-doc", target_chars=20, hard_max_chars=30)
    payload = artifact.to_dict()
    payload["spans"][1]["char_start"] += 1
    with pytest.raises(ValidationError):
        SectionsV2.from_json(json.dumps(payload))

    payload = artifact.to_dict()
    payload["spans"][0]["unknown"] = 1
    with pytest.raises(ValidationError):
        SectionsV2.from_json(json.dumps(payload))


def test_atomic_publication_failure_never_exposes_ready_set(tmp_path):
    canonical = "Evidence. " * 1_000
    sections = build_sections_v2(canonical, doc_id="publish-doc")
    units = build_citation_units_v2(canonical, doc_id="publish-doc")
    calls = 0

    def fail_second(path, payload, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic publication failure")
        return atomic_write_json(path, payload, **kwargs)

    with pytest.raises(OSError, match="synthetic publication failure"):
        publish_v2_artifact_set(
            tmp_path, sections=sections, units=units, write_json=fail_second,
        )

    assert (tmp_path / CITATION_UNITS_V2_FILENAME).exists() is False
    assert (tmp_path / V2_READY_FILENAME).exists() is False
    assert inspect_v2_artifact_set(tmp_path) == ("held", "readiness_marker_missing")

    publish_v2_artifact_set(tmp_path, sections=sections, units=units)
    assert inspect_v2_artifact_set(tmp_path) == ("ready", "verified_complete_pair")


def test_ready_set_detects_post_publication_tampering(tmp_path):
    canonical = "Evidence. " * 500
    sections = build_sections_v2(canonical, doc_id="publish-doc")
    units = build_citation_units_v2(canonical, doc_id="publish-doc")
    publish_v2_artifact_set(tmp_path, sections=sections, units=units)

    unit_path = tmp_path / CITATION_UNITS_V2_FILENAME
    unit_path.write_bytes(unit_path.read_bytes().replace(b"Evidence", b"Tampered", 1))
    status, reason = inspect_v2_artifact_set(tmp_path)
    assert status == "held"
    assert reason in {"artifact_set_invalid", "units_hash_mismatch"}


# ---------------------------------------------------------------- new in the port

def test_round_trip_and_aliases():
    canonical = "First passage.\n\nSecond passage. " * 200
    units = build_passages(canonical, doc_id="alias-doc")
    sections = build_sections(canonical, doc_id="alias-doc")
    assert units == build_citation_units_v2(canonical, doc_id="alias-doc")
    assert sections == build_sections_v2(canonical, doc_id="alias-doc")
    assert CitationUnitsV2.from_json(canonical_artifact_bytes(units)) == units
    assert SectionsV2.from_dict(sections.to_dict()) == sections
    assert units.schema_version == "citation-units-v2.0"
    assert sections.schema_version == "sections-v2.0"


def test_artifacts_are_frozen_and_strict():
    units = build_passages("one two three four five", doc_id="strict-doc", target_chars=8, hard_max_chars=10)
    with pytest.raises(dataclasses.FrozenInstanceError):
        units.doc_id = "other"  # type: ignore[misc]
    payload = units.to_dict()
    payload["spans"][0]["sequence"] = "1"  # no coercion
    with pytest.raises(ValidationError):
        CitationUnitsV2.from_dict(payload)
    payload = units.to_dict()
    payload["schema_version"] = "sections-v2.0"
    with pytest.raises(ValidationError):
        CitationUnitsV2.from_dict(payload)
    payload = units.to_dict()
    del payload["spans"][0]["text"]
    with pytest.raises(ValidationError):
        CitationUnitsV2.from_dict(payload)


def test_unit_records_feed_the_quote_locator():
    canonical = "Alpha beta.\n\nGamma delta. " * 200
    units = build_passages(canonical, doc_id="records-doc")
    records = unit_records(units)
    assert [row["unit_id"] for row in records] == [span.span_id for span in units.spans]
    assert all(canonical[row["char_start"]:row["char_end"]] == row["text"] for row in records)
    assert unit_records(units, ids={records[1]["unit_id"]}) == [records[1]]
