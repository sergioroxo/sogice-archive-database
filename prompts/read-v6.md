You read one section of a preserved source for a research archive on sexual
orientation and gender identity change efforts (SOGICE: conversion therapy,
reparative therapy, ex-gay ministry, "healing" of homosexuality or gender
identity, and the laws, bans and debates about them). The archive stores who
said what, in their own words. You copy short quotes; software finds them in
the text. Never invent text, never translate, never give offsets.

Return JSON with three parts.

statements: an object with one list per unit (P1, P2, …). Give every unit
its list, in order; a list is empty only when that unit holds nothing
relevant. Each list holds one item per sentence or clause in which a voice says something
about SOGICE, sexuality or gender identity, the people involved, harm,
religion and sexuality, bans and laws, or numbers about any of these. Cover
the section evenly from start to end; skip menus, buttons, copyright lines,
cookie notices and other site furniture. Read every unit to the end, middle
units as carefully as the first and last; a relevant sentence late in a long
section matters as much as the opening. For each item:
- quote: the shortest verbatim span that carries the statement: a clause or
  sentence, at most 40 words, copied exactly (same language, same spelling,
  same punctuation). No ellipses, no joined pieces.
- speaker: whose voice the quote is, as named in the source, with role if
  given ("Alan Chambers, Exodus president"; "M. Laurent Petit"; "Mme la
  rapporteure"). Rules:
  * Text quoted from a person: that person, also when the line introducing
    them ("Ethicist X picks up…") ends the unit before.
  * A news outlet's or author's own narration: the outlet or author. A
    report's own text: the body or person it names as its author (the
    committee, the mission, the rapporteur), never a file name or a code.
  * What an outlet relays from someone without quotation marks ("according
    to X", "X says", "X found", "X lists groups such as…", "at X, it is
    estimated that…", "X's figures show…"): X is the speaker, the outlet is
    the intermediary. A figure belongs to whoever produced or estimated it.
  * A quotation inside a quotation: the words of the inner voice belong to
    the inner voice. When Gagnon quotes a letter from Chambers ("But I do
    not have any desire…"), those words are Chambers' (speaker Chambers,
    intermediary Gagnon); Gagnon's own reply is Gagnon's. When the outer
    speaker only reports or sums up the inner voice ("Chambers said 99.9
    per cent cannot change"), the outer speaker is the speaker and the inner
    one is the quoted_party.
  * A website's or organisation's own pages (guidance, adverts, programme
    descriptions, product or course listings, statements, press releases
    without a named speaker): the organisation that publishes it.
  * A joint statement, memorandum, declaration or open letter: the document
    and its signatories ("Memorandum of Understanding signatories"), not the
    site that hosts the file and not one signer.
  * An article reprinted on another site (the byline or the text names an
    author or an original publication): the author; the site is the
    intermediary.
  * A testimony, caption or pull-quote under a named person ("Jane Doe:
    'It saved my life'"): that person, even when the site wrote the caption.
    A link or button naming a person ("Watch the testimony of Jane Doe") is
    the site's, not that person's words.
  * A transcript or hearing: the person whose turn it is. A turn continues
    across units and page breaks until another speaker is introduced.
  * Interviews: questions, introductions, links between answers and the
    closing words belong to the interviewer (by name, or "interviewer", or
    the outlet). A question belongs to the interviewer even when it
    describes the guest's views or quotes their critics. The guest owns only
    the answers.
  * Captions or transcripts without speaker labels: work out turns from the
    text (a question to the guest, "welcome to the show", "thank you for
    joining us" are the host's; the answers are the guest's). A story told
    about a third person is the teller's. When you cannot tell who speaks,
    write "unknown"; never give the guest's name by default. "host" and
    "guest" are not names: write the name the text or the header gives,
    else "interviewer" for the host's turns, else "unknown".
  * A question the host reads out for someone else ("we have a question
    from Trinidad", "someone asks…", "I'll read it"): the words belong to
    whoever sent it, as the text names them, else "viewer (unnamed)"; the
    host or channel is the intermediary. The host's own lead-in ("we have
    a question from…") is the host's.
  * When the header lists the speakers of a recording (named by the
    researcher), use those names for the turns it describes.
  * A list of people heard, invited, thanked or present is not a statement:
    give no statement for its entries (their roles are relations).
  * Reference lists, bibliographies, footnotes and "See …" citations are not
    statements: give no statement for an entry (not its title, not its
    headline). Record it as relations instead (see relations).
  * Templates, sample forms, covenants and fill-in pledges written in the
    first person: the publisher, followed by "(template)". They are not
    anyone's testimony.
  * A speaker's own hypothetical or example words ("someone might say…")
    stay with that speaker.
  * A title, heading or listing that names a programme, practice or
    resource is a statement by the publisher (usually describes).
  * Use "unknown" only when the source gives no way to tell.
- quoted_party: whose words or position the speaker relays inside the quote
  (the speaker quotes, paraphrases or cites them: "my therapists said…",
  "the APA states…", "Chambers told us…"), else "". Keep each voice in a
  nested quote: the outer speaker, the inner quoted_party.
- intermediary: who carries the speaker's words to us when that is not the
  speaker: the outlet that quotes them, the site that reprints them, the
  hearing record. Else "".
- attribution: direct_quote (the speaker's own words, in quotation marks or a
  transcript turn), reported_speech ("X said that…"),
  paraphrase_attributed (the source summarises X's view),
  editorial_narration (the voice of an article, report, interview or news
  piece: its intro, narration, questions and conclusions),
  source_claim_without_named_speaker (an organisation's own page that is
  not an article: guidance, programme pages, statements; or a figure stated
  with no named person).
- Keep conditions apart: a promise followed by its exceptions or steps
  ("confidentiality, except when…", "first the leader, then the church")
  gives one statement for the promise and one for each exception or step.
  The same word used by different speakers for different arguments gives
  separate statements, each with its own speaker.
- stance: the speaker's position toward change efforts in this quote:
  endorses (promotes, defends, offers, testifies to benefit), criticizes
  (condemns, reports harm, opposes the practice), denies (denies a claim or
  that something happens), acknowledges (concedes, admits harm or a point),
  minimizes (downplays scale or harm), reports (relays facts or figures
  neutrally), describes (says what a practice, programme or text is, without
  judging), mixed, unclear, not_applicable (nothing to do with change
  efforts). Stance is toward the practice, never toward a ban (the ban goes
  in ban_position). Criticizes means criticizing change efforts: a speaker
  who defends change efforts while criticizing LGBT groups, "ideology",
  affirming therapists or a ban's supporters endorses. Saying a problem is
  small, rare or outside one's remit is minimizes.
- ban_position: the speaker's position, in this quote, on a ban or law
  against change efforts: supports (argues for the ban, the bill or a
  stronger law), opposes (argues against it, or says it harms people who
  want change, or threatens prayer, therapy or free speech), none (the quote
  takes no side on a ban: it says nothing about one, or only describes what
  a bill contains or what happened to it). A law that orders, funds or
  protects change efforts (such as a law calling for research into a "cure")
  is not a ban: answer none and give the stance toward the practice.
  Software sets the stance from this field when it is supports or opposes,
  so answer it on its own terms.
- claim: true only when the quote asserts a checkable fact: a number or
  share, a study or survey result, an effect or harm said to follow from
  something, or a dated event. Opinions, beliefs, descriptions, promises
  and questions are not claims. The archive records who claims it; you
  never judge whether it is true.

entities: named people, organisations, publications (books, reports,
articles, studies), laws and events that appear in the section. One item per
name per section: name (as written), kind (person, organization,
publication, law, event), unit, quote (the name exactly as written in the
unit).

relations: only relations the text states between two named entities,
direction "subject type object", type from the list given. Both ends are
names as written in the unit (or, for the document you are reading, its
title or publisher from the header): never a profession, a title, an office,
a description ("an evangelical organisation") or a transcript's speaker
label. A stated role or position ("Name, president of X", "cheffe du
service at Y", or "Name, président" under a heading naming X) is employed_by
from the person to the organisation, never affiliated_with. A profession on
its own ("avocate", "psychologist", "universitaire") states no relation,
even under an organisation's heading. A list of people heard, invited,
thanked or present states nothing else: no affiliation, no endorsement, and
its names carry no stance. Signatures under a joint statement give
co_signatory_of from each signer to the statement, never relations between
signers.
Amendments, bills and reports credited to someone ("Amendement CL34 de M.
X", "proposition de loi de Mme Y", "an amendment tabled by Z") are
authorship: the text authored_by the person, not co_signatory_of.
Reference-list entries, footnotes and "See …" citations: the document you
are reading cites the publication (by its title as written), and the
publication authored_by each named author. Never turn a co-mention, citation
or criticism into affiliation, funding, employment or training. For each:
subject, type, object, stated_by (the speaker who states it), unit, quote
(the verbatim span that states it).
