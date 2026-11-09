-- SOGICE archive: SQLite schema (source of truth for all evidence records).
--
-- Applied by archive/db.py:init_db(). Every statement is idempotent
-- (CREATE ... IF NOT EXISTS), so applying it twice is harmless. The applied
-- version is recorded in schema_version; bump SCHEMA_VERSION in db.py when
-- this file changes in a way that needs a migration.
--
-- Chain of evidence (docs/PLAN.md):
--   source -> source_version -> section -> passage -> usage -> (concept | entity)
--   usage -> relation (via relation_usage), usage -> claim (via claim_usage)
--   decision (append-only, hash-chained), run, release (+ release_record)
--
-- Conventions
--   * Ids are TEXT chosen by the writer (stable across rebuilds and releases).
--     passage also has an INTEGER pk, used only as the FTS5 rowid.
--   * Dates are ISO 8601, full or partial ('2019', '2019-05', '2019-05-14'),
--     each with a *_certainty column: exact | approximate | inferred | unknown.
--   * Timestamps (created_at etc.) are UTC ISO 8601 strings.
--   * Language codes are ISO 639 (lingua output, e.g. 'en', 'pt', 'es'; 'und'
--     when undetected). Countries are ISO 3166-1 alpha-2 ('GB') or 3166-2
--     subdivisions ('GB-SCT').
--   * JSON columns hold canonical JSON text and are checked with json_valid().
--   * Offsets are Unicode code points, start inclusive, end exclusive. SQLite
--     substr() counts characters the same way, so triggers can check quotes.
--
-- The three states (every interpretive record carries all three, separately):
--   evidence_state  unlocated | located | recurrent | researcher_verified
--                   (is there a located quote? in more than one source? did
--                   Sérgio check it?)
--   review_state    proposed | confirmed | disputed | rejected | superseded
--                   (where the record stands after AI / researcher decisions)
--   visibility      internal | public_eligible
--                   (may a release include it? There is no human gate, but a
--                   record can only be public_eligible when it is located and
--                   not disputed, rejected or superseded.)
-- Interpretive tables: usage, concept, concept_label, concept_relation,
-- entity, entity_variant, relation, claim. Changes to their states are made
-- together with a row in decision.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
  version     INTEGER PRIMARY KEY,
  applied_at  TEXT NOT NULL,
  schema_sha256 TEXT NOT NULL CHECK (length(schema_sha256) = 64)
);

-- ---------------------------------------------------------------------------
-- Vocabulary: one SKOS-style concept store; each facet is a scheme.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS concept (
  id                TEXT PRIMARY KEY,
  scheme            TEXT NOT NULL CHECK (scheme IN (
                      -- passage-level facets and the lexicon itself
                      'term', 'tactic', 'practice', 'function',
                      'harm_type', 'harm_severity', 'target',
                      -- document-level facets (used by source.*)
                      'genre', 'source_stance', 'medium', 'actor_type',
                      'era', 'religious_tradition')),
  pref_label        TEXT NOT NULL CHECK (length(trim(pref_label)) > 0),
  notation          TEXT,            -- e.g. position in the 44-entry tactic tree
  definition        TEXT,
  definition_source TEXT CHECK (definition_source IN ('seed', 'model', 'researcher')),
  -- Primary tree parent (tactic tree, lexicon backbone). Further or
  -- cross-scheme links go in concept_relation.
  broader           TEXT REFERENCES concept(id),
  created_at        TEXT NOT NULL,
  evidence_state    TEXT NOT NULL DEFAULT 'unlocated' CHECK (evidence_state IN ('unlocated', 'located', 'recurrent', 'researcher_verified')),
  review_state      TEXT NOT NULL DEFAULT 'proposed' CHECK (review_state IN ('proposed', 'confirmed', 'disputed', 'rejected', 'superseded')),
  visibility        TEXT NOT NULL DEFAULT 'internal' CHECK (visibility IN ('internal', 'public_eligible')),
  CHECK (broader IS NULL OR broader <> id),
  CHECK (definition IS NULL OR definition_source IS NOT NULL),
  CHECK (visibility = 'internal' OR (evidence_state <> 'unlocated' AND review_state IN ('proposed', 'confirmed')))
);
CREATE INDEX IF NOT EXISTS idx_concept_scheme ON concept(scheme, pref_label);
CREATE INDEX IF NOT EXISTS idx_concept_broader ON concept(broader);
CREATE INDEX IF NOT EXISTS idx_concept_states ON concept(review_state, evidence_state, visibility);

-- Labels per language. normalized_label is NFKC + casefold + collapsed
-- whitespace (computed in Python); exact matching searches it.
CREATE TABLE IF NOT EXISTS concept_label (
  id               TEXT PRIMARY KEY,
  concept_id       TEXT NOT NULL REFERENCES concept(id),
  label            TEXT NOT NULL CHECK (length(trim(label)) > 0),
  normalized_label TEXT NOT NULL,
  language         TEXT NOT NULL CHECK (language GLOB '[a-z][a-z]*'),
  kind             TEXT NOT NULL CHECK (kind IN ('pref', 'alt', 'hidden')),
  created_at       TEXT NOT NULL,
  evidence_state   TEXT NOT NULL DEFAULT 'unlocated' CHECK (evidence_state IN ('unlocated', 'located', 'recurrent', 'researcher_verified')),
  review_state     TEXT NOT NULL DEFAULT 'proposed' CHECK (review_state IN ('proposed', 'confirmed', 'disputed', 'rejected', 'superseded')),
  visibility       TEXT NOT NULL DEFAULT 'internal' CHECK (visibility IN ('internal', 'public_eligible')),
  CHECK (visibility = 'internal' OR (evidence_state <> 'unlocated' AND review_state IN ('proposed', 'confirmed'))),
  UNIQUE (concept_id, normalized_label, language, kind)
);
CREATE INDEX IF NOT EXISTS idx_concept_label_match ON concept_label(normalized_label, language);
-- At most one live preferred label per concept and language (SKOS rule).
CREATE UNIQUE INDEX IF NOT EXISTS idx_concept_label_one_pref
  ON concept_label(concept_id, language)
  WHERE kind = 'pref' AND review_state NOT IN ('rejected', 'superseded');

-- SKOS relations between concepts, or to an external scheme (GOV.UK
-- glossary, ILGA, ...). Exactly one of target_concept_id / external_scheme.
CREATE TABLE IF NOT EXISTS concept_relation (
  id                TEXT PRIMARY KEY,
  concept_id        TEXT NOT NULL REFERENCES concept(id),
  relation          TEXT NOT NULL CHECK (relation IN (
                      'broader', 'narrower', 'related',
                      'exactMatch', 'closeMatch', 'broadMatch', 'narrowMatch', 'relatedMatch')),
  target_concept_id TEXT REFERENCES concept(id),
  external_scheme   TEXT,            -- e.g. 'govuk_glossary_2021'
  external_id       TEXT,            -- id or URI within that scheme
  external_label    TEXT,
  created_at        TEXT NOT NULL,
  evidence_state    TEXT NOT NULL DEFAULT 'unlocated' CHECK (evidence_state IN ('unlocated', 'located', 'recurrent', 'researcher_verified')),
  review_state      TEXT NOT NULL DEFAULT 'proposed' CHECK (review_state IN ('proposed', 'confirmed', 'disputed', 'rejected', 'superseded')),
  visibility        TEXT NOT NULL DEFAULT 'internal' CHECK (visibility IN ('internal', 'public_eligible')),
  CHECK ((target_concept_id IS NULL) <> (external_scheme IS NULL)),
  CHECK (external_scheme IS NULL OR external_id IS NOT NULL),
  CHECK (target_concept_id IS NULL OR target_concept_id <> concept_id),
  CHECK (visibility = 'internal' OR (evidence_state <> 'unlocated' AND review_state IN ('proposed', 'confirmed')))
);
CREATE INDEX IF NOT EXISTS idx_concept_relation_from ON concept_relation(concept_id, relation);
CREATE INDEX IF NOT EXISTS idx_concept_relation_to ON concept_relation(target_concept_id);
CREATE INDEX IF NOT EXISTS idx_concept_relation_external ON concept_relation(external_scheme, external_id);

-- ---------------------------------------------------------------------------
-- Entities: people, organisations, publications, laws, events.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS entity (
  id                   TEXT PRIMARY KEY,
  kind                 TEXT NOT NULL CHECK (kind IN ('person', 'organization', 'publication', 'law', 'event')),
  label                TEXT NOT NULL CHECK (length(trim(label)) > 0),
  normalized_label     TEXT NOT NULL,
  description          TEXT,
  country              TEXT CHECK (country IS NULL OR country GLOB '[A-Z][A-Z]' OR country GLOB '[A-Z][A-Z]-[A-Z0-9]*'),
  start_date           TEXT CHECK (start_date IS NULL OR start_date GLOB '[0-9][0-9][0-9][0-9]*'),
  start_date_certainty TEXT CHECK (start_date_certainty IN ('exact', 'approximate', 'inferred', 'unknown')),
  end_date             TEXT CHECK (end_date IS NULL OR end_date GLOB '[0-9][0-9][0-9][0-9]*'),
  end_date_certainty   TEXT CHECK (end_date_certainty IN ('exact', 'approximate', 'inferred', 'unknown')),
  created_at           TEXT NOT NULL,
  evidence_state       TEXT NOT NULL DEFAULT 'unlocated' CHECK (evidence_state IN ('unlocated', 'located', 'recurrent', 'researcher_verified')),
  review_state         TEXT NOT NULL DEFAULT 'proposed' CHECK (review_state IN ('proposed', 'confirmed', 'disputed', 'rejected', 'superseded')),
  visibility           TEXT NOT NULL DEFAULT 'internal' CHECK (visibility IN ('internal', 'public_eligible')),
  CHECK (start_date IS NULL OR start_date_certainty IS NOT NULL),
  CHECK (end_date IS NULL OR end_date_certainty IS NOT NULL),
  CHECK (visibility = 'internal' OR (evidence_state <> 'unlocated' AND review_state IN ('proposed', 'confirmed')))
);
CREATE INDEX IF NOT EXISTS idx_entity_kind_label ON entity(kind, normalized_label);
CREATE INDEX IF NOT EXISTS idx_entity_states ON entity(review_state, evidence_state, visibility);

-- Name variants. Exact or variant matches attach to an entity; any other
-- merge is a recorded decision (CLAUDE.md: no silent merges).
CREATE TABLE IF NOT EXISTS entity_variant (
  id               TEXT PRIMARY KEY,
  entity_id        TEXT NOT NULL REFERENCES entity(id),
  label            TEXT NOT NULL CHECK (length(trim(label)) > 0),
  normalized_label TEXT NOT NULL,
  language         TEXT CHECK (language IS NULL OR language GLOB '[a-z][a-z]*'),
  kind             TEXT NOT NULL CHECK (kind IN ('pref', 'alt', 'abbreviation', 'former_name', 'translation', 'misspelling', 'hidden')),
  valid_from       TEXT CHECK (valid_from IS NULL OR valid_from GLOB '[0-9][0-9][0-9][0-9]*'),
  valid_to         TEXT CHECK (valid_to IS NULL OR valid_to GLOB '[0-9][0-9][0-9][0-9]*'),
  created_at       TEXT NOT NULL,
  evidence_state   TEXT NOT NULL DEFAULT 'unlocated' CHECK (evidence_state IN ('unlocated', 'located', 'recurrent', 'researcher_verified')),
  review_state     TEXT NOT NULL DEFAULT 'proposed' CHECK (review_state IN ('proposed', 'confirmed', 'disputed', 'rejected', 'superseded')),
  visibility       TEXT NOT NULL DEFAULT 'internal' CHECK (visibility IN ('internal', 'public_eligible')),
  CHECK (visibility = 'internal' OR (evidence_state <> 'unlocated' AND review_state IN ('proposed', 'confirmed'))),
  UNIQUE (entity_id, normalized_label, language, kind)
);
CREATE INDEX IF NOT EXISTS idx_entity_variant_match ON entity_variant(normalized_label);

-- ---------------------------------------------------------------------------
-- Sources and their preserved versions.
-- ---------------------------------------------------------------------------

-- A source is the work (a web page, report, video). Document-level facets
-- are concept ids; the scheme of each is checked by software, not SQL.
CREATE TABLE IF NOT EXISTS source (
  id                      TEXT PRIMARY KEY,
  title                   TEXT,
  publisher               TEXT,
  url                     TEXT,
  archive_url             TEXT,       -- Wayback (or other) capture of url
  published_date          TEXT CHECK (published_date IS NULL OR published_date GLOB '[0-9][0-9][0-9][0-9]*'),
  published_date_certainty TEXT CHECK (published_date_certainty IN ('exact', 'approximate', 'inferred', 'unknown')),
  captured_date           TEXT CHECK (captured_date IS NULL OR captured_date GLOB '[0-9][0-9][0-9][0-9]*'),
  captured_date_certainty TEXT CHECK (captured_date_certainty IN ('exact', 'approximate', 'inferred', 'unknown')),
  retrieved_date          TEXT CHECK (retrieved_date IS NULL OR retrieved_date GLOB '[0-9][0-9][0-9][0-9]*'),
  retrieved_date_certainty TEXT CHECK (retrieved_date_certainty IN ('exact', 'approximate', 'inferred', 'unknown')),
  country                 TEXT CHECK (country IS NULL OR country GLOB '[A-Z][A-Z]' OR country GLOB '[A-Z][A-Z]-[A-Z0-9]*'),
  language                TEXT CHECK (language IS NULL OR language GLOB '[a-z][a-z]*'),  -- main language
  genre                   TEXT REFERENCES concept(id),   -- scheme 'genre'
  medium                  TEXT REFERENCES concept(id),   -- scheme 'medium'
  source_stance           TEXT REFERENCES concept(id),   -- scheme 'source_stance'
  actor_type              TEXT REFERENCES concept(id),   -- scheme 'actor_type'
  era                     TEXT REFERENCES concept(id),   -- scheme 'era'
  religious_tradition     TEXT REFERENCES concept(id),   -- scheme 'religious_tradition'
  legacy_ref              TEXT,       -- id in the old state/source_queue.db, if imported
  created_at              TEXT NOT NULL,
  CHECK (url IS NOT NULL OR title IS NOT NULL),
  CHECK (published_date IS NULL OR published_date_certainty IS NOT NULL),
  CHECK (captured_date IS NULL OR captured_date_certainty IS NOT NULL),
  CHECK (retrieved_date IS NULL OR retrieved_date_certainty IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_source_url ON source(url) WHERE url IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_source_country ON source(country);
CREATE INDEX IF NOT EXISTS idx_source_legacy ON source(legacy_ref);

-- A version is one preserved byte stream (content-addressed by sha256) plus
-- the text extracted from it. Passages cut that text.
CREATE TABLE IF NOT EXISTS source_version (
  id                TEXT PRIMARY KEY,
  source_id         TEXT NOT NULL REFERENCES source(id),
  sha256            TEXT NOT NULL CHECK (length(sha256) = 64),
  path              TEXT NOT NULL,      -- relative to data/, never committed
  byte_size         INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
  mime_type         TEXT,
  acquisition_route TEXT NOT NULL CHECK (acquisition_route IN (
                      'live_fetch', 'wayback', 'local_file', 'manual_upload',
                      'transcription', 'legacy_import')),
  extraction_tool   TEXT,               -- e.g. 'trafilatura 2.0', 'docling 2.x', 'faster-whisper'
  text_sha256       TEXT CHECK (text_sha256 IS NULL OR length(text_sha256) = 64),
  text_path         TEXT,
  quality_flags     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(quality_flags) AND json_type(quality_flags) = 'array'),
  captured_at       TEXT,               -- when these bytes were captured
  created_at        TEXT NOT NULL,
  UNIQUE (source_id, sha256)
);
CREATE INDEX IF NOT EXISTS idx_source_version_sha ON source_version(sha256);

-- What to fetch for a source (schema v2). One source (an original URL) can
-- have several targets: the live page and any number of Wayback captures
-- from different dates. Each target that is fetched becomes a source_version.
-- Rows imported from the old queue keep their old ids and hints here.
CREATE TABLE IF NOT EXISTS source_target (
  id                TEXT PRIMARY KEY,
  source_id         TEXT NOT NULL REFERENCES source(id),
  route             TEXT NOT NULL CHECK (route IN ('live_fetch', 'wayback', 'local_file')),
  url               TEXT NOT NULL,      -- what to fetch: live URL, Wayback URL or file:// URI
  capture_timestamp TEXT CHECK (capture_timestamp IS NULL OR capture_timestamp GLOB '[0-9]*'),
  kind_hint         TEXT NOT NULL DEFAULT 'unknown' CHECK (kind_hint IN (
                      'webpage', 'pdf', 'docx', 'video', 'youtube', 'audio', 'social', 'unknown')),
  hold              TEXT,               -- reason not to fetch (e.g. 'legacy_priority_skip'); NULL = fetch
  legacy_refs       TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(legacy_refs) AND json_type(legacy_refs) = 'array'),
  legacy_hints      TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(legacy_hints) AND json_type(legacy_hints) = 'object'),
  created_at        TEXT NOT NULL,
  CHECK (route <> 'wayback' OR url LIKE 'http%://web.archive.org/web/%'),
  UNIQUE (route, url)
);
CREATE INDEX IF NOT EXISTS idx_source_target_source ON source_target(source_id);
CREATE INDEX IF NOT EXISTS idx_source_target_kind ON source_target(kind_hint, hold);

-- One network request for a target (schema v3). A stored response points to
-- the source_version it produced (an existing version if the bytes were
-- already preserved). Transient failures are retried once (attempt 2) in the
-- same run; then the target counts as failed and the batch moves on.
-- detail holds response headers, redirects and error text as JSON.
CREATE TABLE IF NOT EXISTS fetch_attempt (
  id             TEXT PRIMARY KEY,
  target_id      TEXT NOT NULL REFERENCES source_target(id),
  run_id         TEXT REFERENCES run(id),
  attempt        INTEGER NOT NULL CHECK (attempt IN (1, 2)),
  route          TEXT NOT NULL CHECK (route IN ('live_fetch', 'wayback')),
  requested_url  TEXT NOT NULL,
  final_url      TEXT,
  http_status    INTEGER,
  content_type   TEXT,
  outcome        TEXT NOT NULL CHECK (outcome IN (
                   'stored', 'blocked', 'not_found', 'gone', 'http_error', 'robots_disallowed',
                   'too_large', 'unsupported_type', 'empty', 'no_capture', 'network_error')),
  detail         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(detail) AND json_type(detail) = 'object'),
  version_id     TEXT REFERENCES source_version(id),
  seconds        REAL CHECK (seconds IS NULL OR seconds >= 0),
  created_at     TEXT NOT NULL,
  CHECK ((outcome = 'stored') = (version_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_fetch_attempt_target ON fetch_attempt(target_id, created_at);
CREATE INDEX IF NOT EXISTS idx_fetch_attempt_outcome ON fetch_attempt(outcome);

-- Structural sections of a version. type separates article body from site
-- furniture so repeated navigation/promotional blocks are counted apart.
CREATE TABLE IF NOT EXISTS section (
  id                TEXT PRIMARY KEY,
  version_id        TEXT NOT NULL REFERENCES source_version(id),
  parent_id         TEXT REFERENCES section(id),
  sequence          INTEGER NOT NULL CHECK (sequence >= 1),
  type              TEXT NOT NULL CHECK (type IN ('body', 'navigation', 'endorsement', 'debate_turn', 'template', 'contact', 'other')),
  heading           TEXT,
  speaker           TEXT,               -- as named in the source (debate turns, testimony)
  speaker_entity_id TEXT REFERENCES entity(id),
  char_start        INTEGER CHECK (char_start IS NULL OR char_start >= 0),
  char_end          INTEGER,
  page_start        INTEGER CHECK (page_start IS NULL OR page_start >= 1),
  page_end          INTEGER,
  created_at        TEXT NOT NULL,
  time_start        REAL CHECK (time_start IS NULL OR time_start >= 0),  -- seconds (v4)
  time_end          REAL CHECK (time_end IS NULL OR time_end >= 0),
  CHECK (char_end IS NULL OR (char_start IS NOT NULL AND char_end >= char_start)),
  CHECK (page_end IS NULL OR (page_start IS NOT NULL AND page_end >= page_start)),
  CHECK (parent_id IS NULL OR parent_id <> id),
  UNIQUE (version_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_section_parent ON section(parent_id);
CREATE INDEX IF NOT EXISTS idx_section_type ON section(type);

-- ---------------------------------------------------------------------------
-- Passages: gap-free citation units (~1,500 characters) over a version's
-- extracted text. Sequence starts at 1 at offset 0; each passage starts where
-- the previous one ended. Text and offsets never change once written.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS passage (
  pk                  INTEGER PRIMARY KEY,   -- FTS5 rowid only; refer to passages by id
  id                  TEXT NOT NULL UNIQUE,
  version_id          TEXT NOT NULL REFERENCES source_version(id),
  section_id          TEXT REFERENCES section(id),
  sequence            INTEGER NOT NULL CHECK (sequence >= 1),
  char_start          INTEGER NOT NULL CHECK (char_start >= 0),
  char_end            INTEGER NOT NULL,
  page_start          INTEGER CHECK (page_start IS NULL OR page_start >= 1),
  page_end            INTEGER,
  text                TEXT NOT NULL,
  text_sha256         TEXT NOT NULL CHECK (length(text_sha256) = 64),
  language            TEXT CHECK (language IS NULL OR language GLOB '[a-z][a-z]*'),
  language_confidence REAL CHECK (language_confidence IS NULL OR (language_confidence >= 0 AND language_confidence <= 1)),
  created_at          TEXT NOT NULL,
  time_start          REAL CHECK (time_start IS NULL OR time_start >= 0),  -- seconds into audio/video (v4)
  time_end            REAL CHECK (time_end IS NULL OR time_end >= 0),
  CHECK (char_end > char_start),
  CHECK (length(text) = char_end - char_start),
  CHECK (page_end IS NULL OR (page_start IS NOT NULL AND page_end >= page_start)),
  UNIQUE (version_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_passage_section ON passage(section_id);
CREATE INDEX IF NOT EXISTS idx_passage_language ON passage(language);

CREATE TRIGGER IF NOT EXISTS passage_gap_free BEFORE INSERT ON passage
WHEN NOT (
  (NEW.sequence = 1 AND NEW.char_start = 0)
  OR NEW.char_start = COALESCE(
       (SELECT char_end FROM passage WHERE version_id = NEW.version_id AND sequence = NEW.sequence - 1), -1)
)
BEGIN
  SELECT RAISE(ABORT, 'passage must start where the previous passage of its version ends');
END;

CREATE TRIGGER IF NOT EXISTS passage_text_immutable
BEFORE UPDATE OF pk, id, version_id, sequence, char_start, char_end, text, text_sha256 ON passage
BEGIN
  SELECT RAISE(ABORT, 'passage text and offsets are immutable; cut a new version instead');
END;

-- Full-text index over passage text (external content: text is stored once).
-- remove_diacritics 2 so 'conversão' also matches 'conversao'.
CREATE VIRTUAL TABLE IF NOT EXISTS passage_fts USING fts5(
  text,
  content = 'passage',
  content_rowid = 'pk',
  tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS passage_fts_insert AFTER INSERT ON passage
BEGIN
  INSERT INTO passage_fts(rowid, text) VALUES (NEW.pk, NEW.text);
END;

CREATE TRIGGER IF NOT EXISTS passage_fts_delete AFTER DELETE ON passage
BEGIN
  INSERT INTO passage_fts(passage_fts, rowid, text) VALUES ('delete', OLD.pk, OLD.text);
END;

-- ---------------------------------------------------------------------------
-- Runs: one model call, matching pass or extraction job.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS run (
  id             TEXT PRIMARY KEY,
  role           TEXT NOT NULL CHECK (role IN ('reading', 'audit', 'embedding', 'matching', 'extraction', 'transcription', 'other')),
  model          TEXT,                 -- Ollama tag from config/models.yaml; NULL for software-only runs
  prompt_version TEXT,
  inputs         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(inputs)),
  input_tokens   INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
  output_tokens  INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
  seconds        REAL CHECK (seconds IS NULL OR seconds >= 0),
  attempt        INTEGER NOT NULL DEFAULT 1 CHECK (attempt IN (1, 2)),  -- retried once, then failed
  outcome        TEXT NOT NULL DEFAULT 'running' CHECK (outcome IN ('running', 'succeeded', 'failed')),
  error          TEXT,
  started_at     TEXT NOT NULL,
  finished_at    TEXT,
  CHECK (role NOT IN ('reading', 'audit', 'embedding') OR model IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_run_role ON run(role, outcome);

-- ---------------------------------------------------------------------------
-- Usages: the core record. A short verbatim quote located by software in a
-- passage, with who says it, stance, language and date. Everything else
-- (lexicon entries, tags, edges, clips) points back to usages.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS usage (
  id                     TEXT PRIMARY KEY,
  passage_id             TEXT REFERENCES passage(id),
  char_start             INTEGER,           -- offsets within the passage text
  char_end               INTEGER,
  quote                  TEXT NOT NULL CHECK (length(quote) > 0),   -- exact passage slice
  quote_sha256           TEXT NOT NULL CHECK (length(quote_sha256) = 64),
  concept_id             TEXT REFERENCES concept(id),
  entity_id              TEXT REFERENCES entity(id),
  speaker                TEXT,              -- whose voice, as named in the source, or 'unknown'
  speaker_entity_id      TEXT REFERENCES entity(id),
  quoted_party           TEXT,              -- who the speaker quotes, if anyone
  quoted_party_entity_id TEXT REFERENCES entity(id),
  intermediary           TEXT,              -- who relays it (reporter, hearing, translator)
  intermediary_entity_id TEXT REFERENCES entity(id),
  attribution_mode       TEXT NOT NULL DEFAULT 'unknown' CHECK (attribution_mode IN (
                           'direct_quote', 'reported_speech', 'paraphrase_attributed',
                           'editorial_narration', 'source_claim_without_named_speaker', 'unknown')),
  stance                 TEXT NOT NULL DEFAULT 'unknown' CHECK (stance IN (
                           'endorses', 'criticizes', 'denies', 'acknowledges', 'minimizes',
                           'reports', 'describes', 'mixed', 'unclear', 'unknown', 'not_applicable')),
  language               TEXT CHECK (language IS NULL OR language GLOB '[a-z][a-z]*'),
  date                   TEXT CHECK (date IS NULL OR date GLOB '[0-9][0-9][0-9][0-9]*'),
  date_certainty         TEXT CHECK (date_certainty IN ('exact', 'approximate', 'inferred', 'unknown')),
  found_by               TEXT NOT NULL CHECK (found_by IN ('exact_match', 'model', 'embedding')),
  run_id                 TEXT REFERENCES run(id),
  created_at             TEXT NOT NULL,
  evidence_state         TEXT NOT NULL DEFAULT 'located' CHECK (evidence_state IN ('unlocated', 'located', 'recurrent', 'researcher_verified')),
  review_state           TEXT NOT NULL DEFAULT 'proposed' CHECK (review_state IN ('proposed', 'confirmed', 'disputed', 'rejected', 'superseded')),
  visibility             TEXT NOT NULL DEFAULT 'internal' CHECK (visibility IN ('internal', 'public_eligible')),
  -- A usage names at most one concept or entity (relations/claims link via tables below).
  CHECK (concept_id IS NULL OR entity_id IS NULL),
  -- Anything past 'unlocated' must carry its location.
  CHECK (evidence_state = 'unlocated' OR (passage_id IS NOT NULL AND char_start IS NOT NULL AND char_end IS NOT NULL)),
  CHECK (char_start IS NULL OR (char_start >= 0 AND char_end > char_start AND length(quote) = char_end - char_start)),
  CHECK (date IS NULL OR date_certainty IS NOT NULL),
  CHECK (found_by = 'exact_match' OR run_id IS NOT NULL),
  CHECK (visibility = 'internal' OR (evidence_state <> 'unlocated' AND review_state IN ('proposed', 'confirmed')))
);
CREATE INDEX IF NOT EXISTS idx_usage_passage ON usage(passage_id);
CREATE INDEX IF NOT EXISTS idx_usage_concept ON usage(concept_id);
CREATE INDEX IF NOT EXISTS idx_usage_entity ON usage(entity_id);
CREATE INDEX IF NOT EXISTS idx_usage_speaker ON usage(speaker_entity_id);
CREATE INDEX IF NOT EXISTS idx_usage_run ON usage(run_id);
CREATE INDEX IF NOT EXISTS idx_usage_states ON usage(review_state, evidence_state, visibility);

-- The model quotes; software locates. A located quote must be the exact slice
-- of its passage (substr is 1-based).
CREATE TRIGGER IF NOT EXISTS usage_quote_located_insert BEFORE INSERT ON usage
WHEN NEW.passage_id IS NOT NULL AND NEW.char_start IS NOT NULL
 AND NEW.quote IS NOT (SELECT substr(text, NEW.char_start + 1, NEW.char_end - NEW.char_start)
                       FROM passage WHERE id = NEW.passage_id)
BEGIN
  SELECT RAISE(ABORT, 'usage quote is not the exact passage slice at its offsets');
END;

CREATE TRIGGER IF NOT EXISTS usage_quote_located_update
BEFORE UPDATE OF passage_id, char_start, char_end, quote ON usage
WHEN NEW.passage_id IS NOT NULL AND NEW.char_start IS NOT NULL
 AND NEW.quote IS NOT (SELECT substr(text, NEW.char_start + 1, NEW.char_end - NEW.char_start)
                       FROM passage WHERE id = NEW.passage_id)
BEGIN
  SELECT RAISE(ABORT, 'usage quote is not the exact passage slice at its offsets');
END;

-- ---------------------------------------------------------------------------
-- Relations: attributed statements between entities / concepts ("Source X
-- states that A trains B"). The type comes from the closed vocabulary in
-- archive/relationship_vocabulary.py (checked in Python, so the vocabulary
-- can grow without a schema migration). Endpoints may stay unresolved: the
-- labels are always kept, the ids are filled when resolution succeeds.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS relation (
  id                   TEXT PRIMARY KEY,
  subject_entity_id    TEXT REFERENCES entity(id),
  subject_concept_id   TEXT REFERENCES concept(id),
  subject_label        TEXT NOT NULL,
  type                 TEXT NOT NULL,
  object_entity_id     TEXT REFERENCES entity(id),
  object_concept_id    TEXT REFERENCES concept(id),
  object_label         TEXT NOT NULL,
  valid_from           TEXT CHECK (valid_from IS NULL OR valid_from GLOB '[0-9][0-9][0-9][0-9]*'),
  valid_to             TEXT CHECK (valid_to IS NULL OR valid_to GLOB '[0-9][0-9][0-9][0-9]*'),
  attribution          TEXT,              -- who states it, as it will be shown
  attributed_source_id TEXT REFERENCES source(id),
  layer                TEXT NOT NULL DEFAULT 'asserted' CHECK (layer IN ('asserted', 'co_mention')),
  resolution_status    TEXT NOT NULL DEFAULT 'unresolved' CHECK (resolution_status IN (
                         'unresolved', 'subject_resolved', 'object_resolved', 'resolved', 'ambiguous')),
  created_at           TEXT NOT NULL,
  evidence_state       TEXT NOT NULL DEFAULT 'unlocated' CHECK (evidence_state IN ('unlocated', 'located', 'recurrent', 'researcher_verified')),
  review_state         TEXT NOT NULL DEFAULT 'proposed' CHECK (review_state IN ('proposed', 'confirmed', 'disputed', 'rejected', 'superseded')),
  visibility           TEXT NOT NULL DEFAULT 'internal' CHECK (visibility IN ('internal', 'public_eligible')),
  CHECK (subject_entity_id IS NULL OR subject_concept_id IS NULL),
  CHECK (object_entity_id IS NULL OR object_concept_id IS NULL),
  CHECK (resolution_status <> 'resolved' OR (
           COALESCE(subject_entity_id, subject_concept_id) IS NOT NULL
       AND COALESCE(object_entity_id, object_concept_id) IS NOT NULL)),
  CHECK (visibility = 'internal' OR (evidence_state <> 'unlocated' AND review_state IN ('proposed', 'confirmed')))
);
CREATE INDEX IF NOT EXISTS idx_relation_subject ON relation(subject_entity_id, type);
CREATE INDEX IF NOT EXISTS idx_relation_object ON relation(object_entity_id, type);
CREATE INDEX IF NOT EXISTS idx_relation_subject_concept ON relation(subject_concept_id);
CREATE INDEX IF NOT EXISTS idx_relation_object_concept ON relation(object_concept_id);
CREATE INDEX IF NOT EXISTS idx_relation_states ON relation(review_state, evidence_state, visibility);

CREATE TABLE IF NOT EXISTS relation_usage (
  relation_id TEXT NOT NULL REFERENCES relation(id),
  usage_id    TEXT NOT NULL REFERENCES usage(id),
  role        TEXT NOT NULL DEFAULT 'evidence' CHECK (role IN ('evidence', 'subject_mention', 'object_mention')),
  PRIMARY KEY (relation_id, usage_id)
);
CREATE INDEX IF NOT EXISTS idx_relation_usage_usage ON relation_usage(usage_id);

-- ---------------------------------------------------------------------------
-- Claims: attributed assertions (numbers, outcomes). Kept attributed; the
-- archive records whether a claim is assessed, never states it as fact.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS claim (
  id                 TEXT PRIMARY KEY,
  claimant_entity_id TEXT REFERENCES entity(id),
  claimant_label     TEXT NOT NULL,
  text               TEXT NOT NULL CHECK (length(trim(text)) > 0),
  scope              TEXT,               -- what / where / when the claim covers
  factual_status     TEXT NOT NULL DEFAULT 'not_assessed' CHECK (factual_status IN (
                       'not_assessed', 'corroborated', 'contested', 'refuted', 'not_applicable')),
  created_at         TEXT NOT NULL,
  evidence_state     TEXT NOT NULL DEFAULT 'unlocated' CHECK (evidence_state IN ('unlocated', 'located', 'recurrent', 'researcher_verified')),
  review_state       TEXT NOT NULL DEFAULT 'proposed' CHECK (review_state IN ('proposed', 'confirmed', 'disputed', 'rejected', 'superseded')),
  visibility         TEXT NOT NULL DEFAULT 'internal' CHECK (visibility IN ('internal', 'public_eligible')),
  CHECK (visibility = 'internal' OR (evidence_state <> 'unlocated' AND review_state IN ('proposed', 'confirmed')))
);
CREATE INDEX IF NOT EXISTS idx_claim_claimant ON claim(claimant_entity_id);

CREATE TABLE IF NOT EXISTS claim_usage (
  claim_id TEXT NOT NULL REFERENCES claim(id),
  usage_id TEXT NOT NULL REFERENCES usage(id),
  PRIMARY KEY (claim_id, usage_id)
);
CREATE INDEX IF NOT EXISTS idx_claim_usage_usage ON claim_usage(usage_id);

-- ---------------------------------------------------------------------------
-- Decisions: every AI or researcher decision, append-only and hash-chained.
-- event_hash = sha256(prior_hash || canonical JSON of the event); the first
-- decision's prior_hash is 64 zeros. Computed in archive/db.py. Undo is a new
-- decision (action 'undo', reverts_decision_id) using previous_state.
-- An audit verdict on an earlier decision is a new decision with action
-- 'audit', target_table 'decision'.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS decision (
  seq                 INTEGER PRIMARY KEY AUTOINCREMENT,
  id                  TEXT NOT NULL UNIQUE,
  created_at          TEXT NOT NULL,
  actor_kind          TEXT NOT NULL CHECK (actor_kind IN ('model', 'researcher')),
  actor_id            TEXT NOT NULL,      -- model tag or researcher id
  run_id              TEXT REFERENCES run(id),   -- required for model decisions (model + prompt version)
  action              TEXT NOT NULL CHECK (action IN (
                        'propose', 'confirm', 'dispute', 'reject', 'supersede',
                        'merge', 'split', 'alias', 'rename', 'attach', 'detach',
                        'edit', 'set_evidence_state', 'set_visibility', 'audit', 'undo')),
  target_table        TEXT NOT NULL CHECK (target_table IN (
                        'source', 'source_version', 'section', 'passage', 'usage',
                        'concept', 'concept_label', 'concept_relation', 'entity',
                        'entity_variant', 'relation', 'claim', 'run', 'release', 'decision')),
  target_id           TEXT NOT NULL,
  reason              TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  quote               TEXT,               -- the quote the decision rests on, if any
  audit_verdict       TEXT NOT NULL DEFAULT 'not_audited' CHECK (audit_verdict IN ('not_audited', 'pass', 'fail', 'uncertain')),
  previous_state      TEXT CHECK (previous_state IS NULL OR json_valid(previous_state)),
  new_state           TEXT CHECK (new_state IS NULL OR json_valid(new_state)),
  reverts_decision_id TEXT REFERENCES decision(id),
  prior_hash          TEXT NOT NULL CHECK (length(prior_hash) = 64),
  event_hash          TEXT NOT NULL UNIQUE CHECK (length(event_hash) = 64),
  CHECK (actor_kind = 'researcher' OR run_id IS NOT NULL),
  CHECK (action <> 'undo' OR reverts_decision_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_decision_target ON decision(target_table, target_id);
CREATE INDEX IF NOT EXISTS idx_decision_actor ON decision(actor_kind, actor_id);
CREATE INDEX IF NOT EXISTS idx_decision_run ON decision(run_id);

CREATE TRIGGER IF NOT EXISTS decision_no_update BEFORE UPDATE ON decision
BEGIN
  SELECT RAISE(ABORT, 'decision history is append-only');
END;

CREATE TRIGGER IF NOT EXISTS decision_no_delete BEFORE DELETE ON decision
BEGIN
  SELECT RAISE(ABORT, 'decision history is append-only');
END;

-- ---------------------------------------------------------------------------
-- Releases: a frozen selection of records that every product is built from.
-- decision_head_hash anchors the decision chain at release time (so a
-- truncated chain is detectable). release_record rows are immutable.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS release (
  id                 TEXT PRIMARY KEY,
  created_at         TEXT NOT NULL,
  rules              TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(rules)),   -- selection rules used
  code_commit        TEXT,
  decision_head_hash TEXT CHECK (decision_head_hash IS NULL OR length(decision_head_hash) = 64),
  doi                TEXT,               -- filled after deposit (Zenodo / DataverseNO)
  notes              TEXT
);

CREATE TABLE IF NOT EXISTS release_record (
  release_id    TEXT NOT NULL REFERENCES release(id),
  table_name    TEXT NOT NULL CHECK (table_name IN (
                  'source', 'source_version', 'section', 'passage', 'usage',
                  'concept', 'concept_label', 'concept_relation', 'entity',
                  'entity_variant', 'relation', 'relation_usage', 'claim', 'claim_usage')),
  record_id     TEXT NOT NULL,
  record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),   -- canonical JSON of the row
  PRIMARY KEY (release_id, table_name, record_id)
);

CREATE TRIGGER IF NOT EXISTS release_record_no_update BEFORE UPDATE ON release_record
BEGIN
  SELECT RAISE(ABORT, 'release records are frozen');
END;

CREATE TRIGGER IF NOT EXISTS release_record_no_delete BEFORE DELETE ON release_record
BEGIN
  SELECT RAISE(ABORT, 'release records are frozen');
END;
