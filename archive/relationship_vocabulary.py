"""Closed relationship vocabulary for network edges.

Proposed in the October 2026 development brief (§4) and adopted in the
31 October review. Models are asked to use these types; ingest normalizes any
free-text type deterministically and keeps the original wording in the raw
proposal. A type that does not map stays ``other_stated`` — it is never forced
into a stronger relation.

Ported from the old repository (``runner/pipeline/relationship_vocabulary.py``).
November 2026: added ``cites``, ``responds_to``, ``translates``,
``co_signatory_of`` and ``trains``. The training words ("trains", "trained by",
"training provider") that used to fall under ``participated_in`` now map to
``trains``.

Synonyms classify the type only. Direction always comes from the instruction
("subject <type> object"), so a passive synonym such as "funded" or
"trained by" does not flip the endpoints.
"""
from __future__ import annotations

import re
import unicodedata

RELATIONSHIP_TYPES = (
    "links_to",          # hyperlink only
    "authored_by",
    "published_by",
    "operated_by",
    "affiliated_with",   # stated membership/affiliation
    "funded_by",
    "referred_to",       # referral of people to a provider/programme
    "participated_in",
    "succeeded_by",      # renamed/successor organisation
    "uses_term",
    "claims_about",
    "critiques",
    "employed_by",       # stated role/position (director, therapist, founder of...)
    "cites",             # subject text cites/quotes/references the object
    "responds_to",       # subject (text or actor) publicly replies to the object
    "translates",        # subject (translator or translated edition) translates the object text
    "co_signatory_of",   # subject signed the object (joint letter, declaration, petition)
    "trains",            # subject trains/teaches/certifies the object (people, programme, org)
    "co_mentioned",      # appear together; no relationship asserted
    "other_stated",      # source states a relation outside this vocabulary
)

# Consequential edges: shown publicly only as attributed source statements.
#
# Of the November 2026 additions, two are consequential:
# - co_signatory_of: signing a joint statement imputes endorsement of its
#   content to a named person or organisation, and signatory lists are prone
#   to name collisions and later withdrawals; treat it like affiliated_with.
# - trains: says who passes a practice on to whom (trainer -> practitioners,
#   programme, organisation). It is an operational tie of the same weight as
#   employment or affiliation, and is the edge most likely to be disputed.
# cites, responds_to and translates are bibliographic or discursive relations
# that can be checked against the documents themselves, like authored_by and
# critiques, so they stay non-consequential. A citation or a reply never
# implies agreement.
CONSEQUENTIAL_TYPES = frozenset({
    "funded_by", "affiliated_with", "operated_by", "employed_by",
    "co_signatory_of", "trains",
})

VOCABULARY_INSTRUCTION = (
    "Network edges only: subject and object must each be a NAMED person, organization, "
    "publication (book, article, study, journal), law, event or programme, and set "
    "subject_kind/object_kind accordingly. Do not return statements about concepts, conditions, "
    "groups of unnamed people or quantities (for example 'medication causes X', '75 participants "
    "-> the study'); those are claims, handled by another pass. Return no observation rather "
    "than a non-entity edge. For several authors or members, return one edge per named "
    "person, never a list of names in one subject or object. "
    "Set payload.relationship_type to exactly one of: " + ", ".join(RELATIONSHIP_TYPES) + ". "
    "Write subject and object as the names used in the source. Direction: subject "
    "<type> object (e.g. 'Person X' employed_by 'Clinic Y'; 'Programme Z' funded_by 'Foundation W'; "
    "'Report A' cites 'Study B'; 'Statement C' responds_to 'Report A'; 'Person T' translates "
    "'Book D'; 'Person S' co_signatory_of 'Declaration E'; 'Organization F' trains 'Ministry G'). "
    "Use co_mentioned when two names only appear together, links_to for a hyperlink, and "
    "other_stated (with the source wording in assertion) when no type fits. Never upgrade a "
    "co-mention, link, citation or reply into affiliation, funding, employment, training or "
    "endorsement."
)

_SYNONYMS = {
    "links_to": ("link", "links", "hyperlink", "links to", "linked to", "website link"),
    "authored_by": ("author", "authored", "written by", "wrote", "author of", "byline"),
    "published_by": ("publisher", "published", "published by", "publishes", "media outlet",
                     "outlet"),
    "operated_by": ("operates", "operated", "run by", "runs", "managed by", "manages",
                    "parent org", "parent organization", "parent organisation", "child org",
                    "subsidiary", "owned by", "owns", "program of", "programme of"),
    "affiliated_with": ("affiliate", "affiliated", "affiliation", "member", "member of",
                        "partner", "partner of", "partners with", "network member", "associated with",
                        "allied with", "coalition"),
    "funded_by": ("funds", "funded", "funding", "funder", "donor", "sponsor", "sponsored by",
                  "grant", "financed by", "finances"),
    "referred_to": ("referral", "refers", "referred", "refers to", "recommends", "sends clients to"),
    "participated_in": ("participant", "participates", "attended", "speaker at", "spoke at",
                        "took part in", "event"),
    "succeeded_by": ("successor", "succeeded", "formerly", "renamed", "rebranded", "became",
                     "predecessor"),
    "uses_term": ("uses term", "term use", "uses the term", "describes as", "calls"),
    "claims_about": ("claims", "claim", "states", "asserts", "alleges", "says", "causes",
                     "can cause", "mischaracterization", "mischaracterizes"),
    "critiques": ("critique", "critiques", "criticizes", "criticises", "opposes", "opposed",
                  "condemns", "refutes", "rebuts", "disputes", "challenges"),
    "employed_by": ("employee", "employed", "works for", "works at", "director", "director of",
                    "founder", "founded", "co founder", "president", "chair", "ceo", "staff",
                    "therapist at", "counsellor at", "counselor at", "role", "key individual",
                    "key individual reference", "consultant at", "nurse at"),
    "cites": ("cite", "cited", "cited by", "citation", "citation of", "quotes", "quoted",
              "quotes from", "references", "referenced", "footnote", "bibliography"),
    "responds_to": ("response", "response to", "responds", "responded to", "reply", "replies to",
                    "replied to", "in reply to", "in response to", "answers", "answer to",
                    "reacts to", "open letter to"),
    "translates": ("translation", "translation of", "translated", "translated by", "translated from",
                   "translator", "translator of", "translates into"),
    "co_signatory_of": ("signatory", "signatory of", "signatory to", "signatories", "co signatory",
                        "cosignatory", "co signed", "cosigned", "signed", "signed by", "signer",
                        "undersigned"),
    "trains": ("trainer", "trainer of", "trained", "trained by", "training", "training provider",
               "provides training", "provides training to", "teaches", "taught", "instructs",
               "certifies", "certified by", "mentors", "coaches"),
    "co_mentioned":("co mention", "co mentioned", "mentioned with", "mentioned together"),
}

_LOOKUP: dict[str, str] = {}
for _canonical, _words in _SYNONYMS.items():
    _LOOKUP[_canonical.replace("_", " ")] = _canonical
    for _word in _words:
        _LOOKUP[_word] = _canonical


def _key(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("_", " ")
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def normalize_relationship_type(value: str) -> tuple[str, str]:
    """Return (canonical_type, method) for a model-supplied relationship type."""
    key = _key(value)
    if not key:
        return "other_stated", "empty"
    if key.replace(" ", "_") in RELATIONSHIP_TYPES:
        return key.replace(" ", "_"), "exact"
    if key in _LOOKUP:
        return _LOOKUP[key], "synonym"
    return "other_stated", "unmapped"
