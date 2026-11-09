"""Short verbatim quotes located by software (citation policy located-short-quote-v1).

The model copies a short verbatim span; this module finds it in the supplied
source units and computes exact offsets. The model never supplies offsets and
software never rewrites a quotation: a stored quote is always the exact source
slice. A quote that cannot be found is dropped from the observation and kept
in a diagnostic, never relocated by guesswork.

``records`` are plain dicts with at least ``unit_id`` and ``text`` (see
``archive.passages.unit_records``). Offsets are local to the unit text.

Ported unchanged in behaviour from the old repository
(``runner/pipeline/quote_locator.py``).
"""
from __future__ import annotations

import copy
import hashlib
import re
import unicodedata
from typing import Any

QUOTE_POLICY = "located-short-quote-v1"
MAXIMUM_QUOTE_CHARACTERS = 400
MINIMUM_QUOTE_CHARACTERS = 3
MAXIMUM_EVIDENCE_PER_OBSERVATION = 4

# Payload fields that carry an explicit source quotation, per family.
PAYLOAD_QUOTE_FIELDS = {
    "terms": ("exact_quote",),
    "entities": ("evidence_quote",),
    "tactics": ("evidence_quote",),
    "practices": ("harm_quote",),
    "relationships": ("evidence_quote",),
}

CITATION_INSTRUCTION = (
    "For each evidence item copy the shortest verbatim span from the supplied unit text "
    f"that supports the observation: one sentence or clause, at most {MAXIMUM_QUOTE_CHARACTERS} "
    "characters. Copy characters exactly; do not paraphrase, summarize, translate, join "
    "separate passages or add ellipses. Give the unit_id the span comes from. Software "
    "locates the span; never supply offsets. Payload quote fields (exact_quote, "
    "evidence_quote, harm_quote) must be verbatim spans of the same kind."
)

ATTRIBUTION_INSTRUCTION = (
    "For every evidence item report attribution: speaker_label (the person or organization "
    "whose voice the span is, as named in the source, or 'unknown'), speaker_role, "
    "attribution_mode and source_stance (the cited voice's stance toward this observation). "
    "Use 'unknown' when the source does not make it explicit; never infer identity, "
    "institution, endorsement or truth. Stance describes what the voice says, not whether "
    "it is true."
)

_TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-", " ": " ", " ": " ", " ": " ",
    "…": "...",
})


def _normalized_with_map(text: str) -> tuple[str, list[int]]:
    """Comparison form plus, for each normalized character, its source index.

    Normalization folds typographic quotes/dashes, NFKC compatibility forms and
    whitespace runs. It is used only to find a span; the stored quote is always
    re-sliced from the original text.
    """
    chars: list[str] = []
    index_map: list[int] = []
    previous_space = True
    for index, char in enumerate(text):
        folded = unicodedata.normalize("NFKC", char.translate(_TRANSLATE))
        for piece in folded:
            if piece.isspace():
                if previous_space:
                    continue
                piece = " "
                previous_space = True
            else:
                previous_space = False
            chars.append(piece)
            index_map.append(index)
    while chars and chars[-1] == " ":
        chars.pop()
        index_map.pop()
    return "".join(chars), index_map


def _strip_quote(quote: str) -> str:
    value = quote.strip()
    # Models often wrap the copied span in quotation marks or trailing ellipses.
    for _ in range(2):
        if len(value) >= 2 and value[0] in "\"'“‘" and value[-1] in "\"'”’":
            value = value[1:-1].strip()
        value = re.sub(r"^(?:\.\.\.|…)\s*|\s*(?:\.\.\.|…)$", "", value).strip()
    return value


def _find(text: str, quote: str) -> tuple[int, int, str, int] | None:
    """Return (start, end, method, occurrences) of ``quote`` in ``text``."""
    occurrences = [match.start() for match in re.finditer(re.escape(quote), text)]
    if occurrences:
        start = occurrences[0]
        return start, start + len(quote), "exact", len(occurrences)
    normalized_text, index_map = _normalized_with_map(text)
    normalized_quote, _ = _normalized_with_map(quote)
    normalized_quote = normalized_quote.strip()
    if len(normalized_quote) < MINIMUM_QUOTE_CHARACTERS:
        return None
    found = [match.start() for match in re.finditer(re.escape(normalized_quote), normalized_text)]
    if not found:
        lowered_text = normalized_text.casefold()
        lowered_quote = normalized_quote.casefold()
        if len(lowered_text) != len(normalized_text) or len(lowered_quote) != len(normalized_quote):
            return None
        found = [match.start() for match in re.finditer(re.escape(lowered_quote), lowered_text)]
        method = "normalized_casefold"
    else:
        method = "normalized"
    if not found:
        return None
    first = found[0]
    start = index_map[first]
    end = index_map[first + len(normalized_quote) - 1] + 1
    return start, end, method, len(found)


def locate_quote(quote: Any, records: list[dict], *, preferred_unit_id: Any = None) -> dict | None:
    """Locate a model-copied quote in the supplied unit records.

    Searches the cited unit first, then the other supplied units. Returns a
    unit-local locator whose ``quote`` is the exact source slice, or ``None``.
    """
    if not isinstance(quote, str):
        return None
    candidate = _strip_quote(quote)
    if not (MINIMUM_QUOTE_CHARACTERS <= len(candidate) <= MAXIMUM_QUOTE_CHARACTERS * 2):
        return None
    ordered = sorted(records, key=lambda row: row["unit_id"] != preferred_unit_id)
    for record in ordered:
        found = _find(record["text"], candidate)
        if found is None:
            continue
        start, end, method, occurrences = found
        exact = record["text"][start:end]
        if not exact.strip():
            continue
        where = "cited_unit" if record["unit_id"] == preferred_unit_id else "other_unit"
        return {
            "unit_id": record["unit_id"], "start": start, "end": end, "quote": exact,
            "method": f"{method}_in_{where}" + ("" if occurrences == 1 else "_first_of_many"),
            "occurrences": occurrences,
        }
    return None


def quote_response_schema(schema: dict, unit_ids: list[str]) -> dict:
    """Request schema: verbatim quote + unit + required attribution; no offsets."""
    schema = copy.deepcopy(schema)
    definitions = schema.setdefault("$defs", {})
    attribution = copy.deepcopy(definitions.get("SourceAttributionV1") or {})
    properties = attribution.setdefault("properties", {})
    for software_field in ("context", "schema_version", "review_state",
                           "stance_target_proposal_id", "context_choice_id", "context_status"):
        properties.pop(software_field, None)
    attribution["required"] = [field for field in (
        "speaker_label", "speaker_role", "attribution_mode", "source_stance") if field in properties]
    attribution["additionalProperties"] = False
    definitions["SourceAttributionV1"] = attribution
    definitions.pop("SourceContextLocatorV1", None)
    definitions["Locator"] = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "unit_id": {"type": "string", "enum": list(unit_ids)},
            "quote": {"type": "string", "minLength": MINIMUM_QUOTE_CHARACTERS,
                      "maxLength": MAXIMUM_QUOTE_CHARACTERS,
                      "description": "Verbatim span copied from the unit text."},
            "attribution": {"$ref": "#/$defs/SourceAttributionV1"},
        },
        "required": ["unit_id", "quote", "attribution"],
    }
    return schema


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _overlaps(locator: dict, others: list[dict]) -> bool:
    return any(other["unit_id"] == locator["unit_id"] and other["start"] < locator["end"]
               and locator["start"] < other["end"] for other in others)


def resolve_quote_evidence(raw: Any, records: list[dict], *, family: str,
                           items_key: str = "observations") -> tuple[dict, dict]:
    """Replace model quotes with software-located exact locators.

    Returns ``(resolved, diagnostics)``. Unlocatable evidence is removed and
    reported; an observation left with no evidence (and not marked as an
    interpretation) is removed and reported. A payload quote that is not inside
    any located evidence span is located and added as evidence, so the payload
    and the citation stay bound. Exact legacy locators (unit, offsets, matching
    quote) are accepted unchanged for deterministic replay and fixtures.
    """
    if not isinstance(raw, dict):
        raise ValueError("source_evidence_response_not_object")
    value = copy.deepcopy(raw)
    by_unit = {row["unit_id"]: row for row in records}
    kept_items: list[Any] = []
    methods: list[list[str]] = []
    unlocated: list[dict] = []
    dropped: list[dict] = []
    items = value.get(items_key, [])
    if not isinstance(items, list):
        raise ValueError("source_evidence_response_items_not_list")
    for item_index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError("source_evidence_response_item_not_object")
        evidence_items = item.get("evidence", [])
        if not isinstance(evidence_items, list):
            raise ValueError("source_evidence_response_evidence_not_list")
        located: list[dict] = []
        item_methods: list[str] = []
        for evidence in evidence_items:
            if not isinstance(evidence, dict):
                raise ValueError("source_evidence_locator_not_object")
            attribution = evidence.get("attribution")
            if attribution is not None:
                if not isinstance(attribution, dict):
                    raise ValueError("source_attribution_not_object")
                if {"context", "context_locator", "review_state", "schema_version",
                        "stance_target_proposal_id", "context_choice_id"}.intersection(attribution):
                    raise ValueError("model_supplied_source_attribution_projection")
                attribution = {**attribution, "context_status": "unknown"}
            unit = by_unit.get(evidence.get("unit_id"))
            start, end, quote = evidence.get("start"), evidence.get("end"), evidence.get("quote")
            if (unit is not None and type(start) is int and type(end) is int
                    and 0 <= start < end <= len(unit["text"]) and unit["text"][start:end] == quote):
                locator = {"unit_id": unit["unit_id"], "start": start, "end": end, "quote": quote}
                method = "submitted_exact_offsets"
            else:
                found = locate_quote(quote, records, preferred_unit_id=evidence.get("unit_id"))
                if found is None:
                    unlocated.append({"item_number": item_index, "label": str(item.get("label") or "")[:200],
                                      "quote_sha256": _sha(str(quote or "")),
                                      "quote_excerpt": str(quote or "")[:200]})
                    continue
                locator = {key: found[key] for key in ("unit_id", "start", "end", "quote")}
                method = found["method"]
            if attribution is not None:
                locator["attribution"] = attribution
            if any(row["unit_id"] == locator["unit_id"] and row["start"] == locator["start"]
                   and row["end"] == locator["end"] for row in located):
                continue
            located.append(locator)
            item_methods.append(method)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        for field in PAYLOAD_QUOTE_FIELDS.get(family, ()):
            payload_quote = payload.get(field)
            if not isinstance(payload_quote, str) or not payload_quote.strip():
                continue
            found = locate_quote(payload_quote, records,
                                 preferred_unit_id=located[0]["unit_id"] if located else None)
            if found is None:
                continue
            # Bind the payload to its exact source slice so payload and citation agree.
            payload[field] = found["quote"]
            locator = {key: found[key] for key in ("unit_id", "start", "end", "quote")}
            if _overlaps(locator, located) or len(located) >= MAXIMUM_EVIDENCE_PER_OBSERVATION:
                continue
            first_attribution = next((row["attribution"] for row in located if "attribution" in row), None)
            if first_attribution is not None:
                locator["attribution"] = dict(first_attribution)
            located.append(locator)
            item_methods.append("payload_quote_" + found["method"])
        if not located and not item.get("interpretation"):
            dropped.append({"item_number": item_index, "label": str(item.get("label") or "")[:200],
                            "reason": "no_locatable_evidence"})
            continue
        item["evidence"] = located[:MAXIMUM_EVIDENCE_PER_OBSERVATION]
        kept_items.append(item)
        methods.append(item_methods[:MAXIMUM_EVIDENCE_PER_OBSERVATION])
    value[items_key] = kept_items
    diagnostics = {"citation_policy": QUOTE_POLICY, "location_methods": methods,
                   "unlocated_evidence": unlocated, "dropped_observations": dropped}
    return value, diagnostics
