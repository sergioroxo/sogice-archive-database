"""Closed relationship vocabulary. The first test is ported from the old
tests/test_phase1_extraction_contract.py; the rest cover the November 2026
additions."""
import pytest

from archive.relationship_vocabulary import (
    _SYNONYMS,
    CONSEQUENTIAL_TYPES,
    RELATIONSHIP_TYPES,
    VOCABULARY_INSTRUCTION,
    _key,
    normalize_relationship_type,
)

NEW_TYPES = ("cites", "responds_to", "translates", "co_signatory_of", "trains")


@pytest.mark.parametrize("value,expected", [
    ("funded_by", "funded_by"), ("Funded by", "funded_by"), ("partner", "affiliated_with"),
    ("Director of", "employed_by"), ("is where", "other_stated"), ("", "other_stated"),
    ("hyperlink", "links_to"), ("opposes", "critiques"),
])
def test_relationship_type_normalization(value, expected):
    assert normalize_relationship_type(value)[0] == expected


@pytest.mark.parametrize("value", NEW_TYPES)
def test_new_types_are_in_vocabulary_and_map_exactly(value):
    assert value in RELATIONSHIP_TYPES
    assert normalize_relationship_type(value) == (value, "exact")
    assert normalize_relationship_type(value.replace("_", " ").upper()) == (value, "exact")
    assert value in VOCABULARY_INSTRUCTION


@pytest.mark.parametrize("value,expected", [
    ("cited", "cites"), ("References", "cites"), ("quotes", "cites"), ("cited by", "cites"),
    ("in response to", "responds_to"), ("Replies to", "responds_to"), ("reply", "responds_to"),
    ("translated by", "translates"), ("Translation of", "translates"), ("translator", "translates"),
    ("signatory", "co_signatory_of"), ("co-signed", "co_signatory_of"),
    ("Co-signatory", "co_signatory_of"), ("signed by", "co_signatory_of"),
    ("trainer", "trains"), ("trained by", "trains"), ("training provider", "trains"),
    ("provides training to", "trains"), ("teaches", "trains"),
])
def test_new_type_synonyms(value, expected):
    assert normalize_relationship_type(value) == (expected, "synonym")


def test_training_words_moved_from_participated_in_to_trains():
    assert normalize_relationship_type("trains")[0] == "trains"
    assert normalize_relationship_type("attended")[0] == "participated_in"
    assert normalize_relationship_type("spoke at")[0] == "participated_in"


def test_consequential_types():
    assert CONSEQUENTIAL_TYPES == {
        "funded_by", "affiliated_with", "operated_by", "employed_by",
        "co_signatory_of", "trains",
    }
    assert {"cites", "responds_to", "translates"}.isdisjoint(CONSEQUENTIAL_TYPES)
    assert CONSEQUENTIAL_TYPES <= set(RELATIONSHIP_TYPES)


def test_vocabulary_is_closed_and_unambiguous():
    assert len(RELATIONSHIP_TYPES) == len(set(RELATIONSHIP_TYPES))
    assert RELATIONSHIP_TYPES[-1] == "other_stated"
    assert set(_SYNONYMS) <= set(RELATIONSHIP_TYPES)
    seen: dict[str, str] = {}
    for canonical, words in _SYNONYMS.items():
        for word in words:
            # Synonyms are stored in normalized key form and map to one type only.
            assert _key(word) == word, word
            assert word not in seen, f"{word!r} maps to {seen.get(word)} and {canonical}"
            assert word.replace(" ", "_") not in RELATIONSHIP_TYPES or word.replace(" ", "_") == canonical
            seen[word] = canonical
