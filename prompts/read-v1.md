You read one section of a preserved source for a research archive on sexual
orientation and gender identity change efforts (SOGICE: conversion therapy,
reparative therapy, ex-gay ministry, "healing" of homosexuality or gender
identity, and the laws, bans and debates about them). The archive stores who
said what, in their own words. You copy short quotes; software finds them in
the text. Never invent text, never translate, never give offsets.

Return JSON with three lists.

statements: one item per sentence or clause in which a voice says something
about SOGICE, sexuality or gender identity, the people involved, harm,
religion and sexuality, bans and laws, or numbers about any of these. Cover
the section evenly from start to end; skip menus, buttons, copyright lines,
cookie notices and other site furniture. Read every unit to the end, middle
units as carefully as the first and last; a relevant sentence late in a long
section matters as much as the opening. For each item:
- unit: the unit id (P1, P2, …) the quote comes from.
- quote: the shortest verbatim span that carries the statement: a clause or
  sentence, at most 40 words, copied exactly (same language, same spelling,
  same punctuation). No ellipses, no joined pieces.
- speaker: whose voice the quote is, as named in the source, with role if
  given ("Alan Chambers, Exodus president"; "M. Laurent Petit"; "Mme la
  rapporteure"). Rules:
  * Text quoted from a person: that person.
  * A news outlet's or author's own narration: the outlet or author.
  * A website's or organisation's own pages (guidance, adverts, programme
    descriptions, statements, press releases without a named speaker): the
    organisation that publishes it.
  * A transcript or hearing: the person whose turn it is. A turn continues
    across units and page breaks until another speaker is introduced.
  * Templates, sample forms, covenants and fill-in pledges written in the
    first person: the publisher, followed by "(template)". They are not
    anyone's testimony.
  * A speaker's own hypothetical or example words ("someone might say…")
    stay with that speaker.
  * Use "unknown" only when the source gives no way to tell.
- quoted_party: whose words or position the speaker relays inside the quote
  (the speaker quotes, paraphrases or cites them), else "".
- intermediary: who carries the speaker's words to us when that is not the
  speaker: the outlet that quotes them, the site that reprints them, the
  hearing record. Else "".
- attribution: direct_quote (the speaker's own words, in quotation marks or a
  transcript turn), reported_speech ("X said that…"),
  paraphrase_attributed (the source summarises X's view),
  editorial_narration (the outlet's or author's own voice),
  source_claim_without_named_speaker (an organisation's own page, a
  statement, a figure stated with no named person).
- stance: the speaker's position toward change efforts in this quote:
  endorses (promotes, defends, offers, testifies to benefit), criticizes
  (condemns, reports harm, opposes the practice), denies (denies a claim or
  that something happens), acknowledges (concedes, admits harm or a point),
  minimizes (downplays scale or harm), reports (relays facts or figures
  neutrally), describes (says what a practice, programme or text is, without
  judging), mixed, unclear, not_applicable (nothing to do with change
  efforts). Supporting a ban is criticizes; opposing a ban in order to keep
  the practice available is endorses or mixed.
- claim: true when the quote asserts a checkable fact (a number, an effect,
  an event, a study result). The archive records who claims it; you never
  judge whether it is true.

entities: named people, organisations, publications (books, reports,
articles, studies), laws and events that appear in the section. One item per
name per section: name (as written), kind (person, organization,
publication, law, event), unit, quote (the name exactly as written in the
unit).

relations: only relations the text states between two named entities,
direction "subject type object", type from the list given. A list of people
heard, invited, thanked or present states no relation. Signatures under a
joint statement give co_signatory_of from each signer to the statement,
never relations between signers. Never turn a co-mention, citation or
criticism into affiliation, funding, employment or training. For each:
subject, type, object, stated_by (the speaker who states it), unit, quote
(the verbatim span that states it).
