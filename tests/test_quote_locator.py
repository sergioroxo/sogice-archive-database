"""Located short quotes: ported from the locator and schema parts of the old
tests/test_phase1_extraction_contract.py. The executor and registry tests
there depend on old machinery and were not ported.
"""
import socket

import pytest

from archive import quote_locator as ql
from archive.passages import build_citation_units_v2, unit_records

ARTICLE = (
    "Dr. Chris Holcombe is a consultant breast surgeon at the Royal Liverpool Hospital.\n\n"
    "He said: “The commonest cause of enlarged breasts in men is weight gain.”\n\n"
    "Joanne Highley writes that counselling helped her ministry’s clients. "
    "Joanne Highley founded the ministry in 1988."
)

# Shape of the old pydantic FamilyResponse JSON schema, reduced to what
# quote_response_schema touches.
FAMILY_RESPONSE_SCHEMA = {
    "title": "FamilyResponse",
    "type": "object",
    "properties": {
        "observations": {"type": "array", "items": {"$ref": "#/$defs/Observation"}, "maxItems": 6},
        "qualifications": {"type": "array", "items": {"type": "string"}},
        "done": {"type": "boolean"},
    },
    "required": ["observations", "done"],
    "$defs": {
        "SourceContextLocatorV1": {"type": "object", "properties": {"unit_id": {"type": "string"}}},
        "SourceAttributionV1": {
            "type": "object",
            "properties": {
                "schema_version": {"const": "source-attribution-v1"},
                "speaker_label": {"type": "string"},
                "speaker_role": {"type": "string"},
                "attribution_mode": {"type": "string"},
                "source_stance": {"type": "string"},
                "context_choice_id": {"type": ["string", "null"]},
                "context": {"$ref": "#/$defs/SourceContextLocatorV1"},
                "context_status": {"type": "string"},
                "review_state": {"type": "string"},
                "stance_target_proposal_id": {"type": ["string", "null"]},
            },
        },
        "Locator": {
            "type": "object",
            "properties": {
                "unit_id": {"type": "string"}, "quote": {"type": "string"},
                "start": {"type": "integer"}, "end": {"type": "integer"},
                "attribution": {"$ref": "#/$defs/SourceAttributionV1"},
            },
            "required": ["unit_id", "quote", "start", "end"],
        },
        "Observation": {
            "type": "object",
            "properties": {
                "label": {"type": "string"}, "language": {"type": "string"},
                "payload": {"type": "object"},
                "evidence": {"type": "array", "items": {"$ref": "#/$defs/Locator"}},
            },
            "required": ["label", "language", "payload", "evidence"],
        },
    },
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("network attempted"))


def records_for(text):
    units = build_citation_units_v2(text, doc_id="doc")
    return units, unit_records(units)


def test_locate_quote_exact_normalized_wrapped_and_missing():
    _, records = records_for(ARTICLE)
    first = records[0]["unit_id"]

    exact = ql.locate_quote("consultant breast surgeon", records, preferred_unit_id=first)
    assert exact["method"] == "exact_in_cited_unit"
    assert records[0]["text"][exact["start"]:exact["end"]] == "consultant breast surgeon"

    # Straight quotes and collapsed whitespace in the model copy still locate,
    # and the stored quote is the exact source slice with typographic quotes.
    typed = ql.locate_quote('"The commonest  cause of enlarged breasts in men is weight gain."',
                            records, preferred_unit_id=first)
    assert typed is not None and typed["method"].startswith("normalized")
    unit = next(row for row in records if row["unit_id"] == typed["unit_id"])
    assert unit["text"][typed["start"]:typed["end"]] == typed["quote"]
    assert "commonest cause" in typed["quote"]

    wrapped = ql.locate_quote("…counselling helped her ministry's clients…", records)
    assert wrapped is not None and wrapped["quote"] == "counselling helped her ministry’s clients"

    assert ql.locate_quote("a sentence that is not in the source", records) is None
    assert ql.locate_quote("", records) is None


def test_locate_quote_repeated_span_takes_first_and_says_so():
    _, records = records_for("Joanne Highley spoke. Joanne Highley wrote.")
    found = ql.locate_quote("Joanne Highley", records)
    assert found["start"] == 0
    assert found["method"].endswith("_first_of_many") and found["occurrences"] == 2


def test_locate_quote_rejects_non_strings_and_oversized_quotes():
    _, records = records_for(ARTICLE)
    assert ql.locate_quote(None, records) is None
    assert ql.locate_quote(42, records) is None
    assert ql.locate_quote("ab", records) is None
    assert ql.locate_quote("x" * (ql.MAXIMUM_QUOTE_CHARACTERS * 2 + 1), records) is None


def test_locate_quote_casefold_and_other_unit():
    text = ("Alpha section about something else entirely. " * 60) + "\n\nThe Ministry RAN a programme."
    _, records = records_for(text)
    assert len(records) > 1
    found = ql.locate_quote("the ministry ran a programme", records, preferred_unit_id=records[0]["unit_id"])
    assert found["method"] == "normalized_casefold_in_other_unit"
    assert found["quote"] == "The Ministry RAN a programme"


def test_quote_schema_asks_for_verbatim_quote_unit_and_attribution_not_offsets():
    schema = ql.quote_response_schema(FAMILY_RESPONSE_SCHEMA, ["u1", "u2"])
    locator = schema["$defs"]["Locator"]
    assert set(locator["properties"]) == {"unit_id", "quote", "attribution"}
    assert set(locator["required"]) == {"unit_id", "quote", "attribution"}
    assert locator["properties"]["quote"]["maxLength"] == ql.MAXIMUM_QUOTE_CHARACTERS
    assert locator["properties"]["unit_id"]["enum"] == ["u1", "u2"]
    attribution = schema["$defs"]["SourceAttributionV1"]
    assert {"speaker_label", "speaker_role", "attribution_mode", "source_stance"} == set(attribution["required"])
    assert "context_choice_id" not in attribution["properties"]
    assert "SourceContextLocatorV1" not in schema["$defs"]
    # The input schema is not mutated.
    assert "start" in FAMILY_RESPONSE_SCHEMA["$defs"]["Locator"]["properties"]


def test_resolver_drops_unlocated_binds_payload_quote_and_keeps_legacy_offsets():
    _, records = records_for(ARTICLE)
    unit_id = records[0]["unit_id"]
    raw = {"observations": [
        {"label": "weight gain", "language": "en", "payload": {"exact_quote": "weight gain"},
         "evidence": [{"unit_id": unit_id, "quote": "enlarged breasts in men is weight gain",
                       "attribution": {"speaker_label": "Chris Holcombe", "speaker_role": "quoted_interviewee",
                                       "attribution_mode": "direct_quote", "source_stance": "reports"}}]},
        {"label": "invented", "language": "en", "payload": {"exact_quote": "not there"},
         "evidence": [{"unit_id": unit_id, "quote": "words the source never used"}]},
        {"label": "legacy", "language": "en", "payload": {},
         "evidence": [{"unit_id": unit_id, "quote": records[0]["text"][0:3], "start": 0, "end": 3}]},
        {"label": "payload only", "language": "en", "payload": {"exact_quote": "consultant breast surgeon"},
         "evidence": [{"unit_id": unit_id, "quote": "Royal Liverpool Hospital"}]},
    ], "qualifications": [], "done": True}
    resolved, diagnostics = ql.resolve_quote_evidence(raw, records, family="terms")
    labels = [row["label"] for row in resolved["observations"]]
    assert labels == ["weight gain", "legacy", "payload only"]
    assert diagnostics["dropped_observations"][0]["label"] == "invented"
    assert len(diagnostics["unlocated_evidence"]) == 1
    first = resolved["observations"][0]["evidence"][0]
    assert first["attribution"]["source_stance"] == "reports"
    assert first["attribution"]["context_status"] == "unknown"
    assert diagnostics["location_methods"][1] == ["submitted_exact_offsets"]
    payload_only = resolved["observations"][2]
    assert [row["quote"] for row in payload_only["evidence"]] == [
        "Royal Liverpool Hospital", "consultant breast surgeon"]
    assert diagnostics["location_methods"][2][1].startswith("payload_quote_")
    assert diagnostics["citation_policy"] == ql.QUOTE_POLICY
    # The raw response is not mutated.
    assert len(raw["observations"]) == 4


def test_resolver_rejects_model_supplied_software_fields_and_bad_shapes():
    _, records = records_for(ARTICLE)
    unit_id = records[0]["unit_id"]
    smuggled = {"observations": [{"label": "x", "payload": {}, "evidence": [
        {"unit_id": unit_id, "quote": "consultant breast surgeon",
         "attribution": {"speaker_label": "unknown", "context_choice_id": "c1"}}]}]}
    with pytest.raises(ValueError, match="model_supplied_source_attribution_projection"):
        ql.resolve_quote_evidence(smuggled, records, family="terms")
    with pytest.raises(ValueError, match="not_object"):
        ql.resolve_quote_evidence([], records, family="terms")
    with pytest.raises(ValueError, match="items_not_list"):
        ql.resolve_quote_evidence({"observations": {}}, records, family="terms")


def test_resolver_keeps_interpretations_without_evidence():
    _, records = records_for(ARTICLE)
    raw = {"observations": [{"label": "reading", "payload": {}, "interpretation": True, "evidence": []}]}
    resolved, diagnostics = ql.resolve_quote_evidence(raw, records, family="claims")
    assert [row["label"] for row in resolved["observations"]] == ["reading"]
    assert diagnostics["dropped_observations"] == []
