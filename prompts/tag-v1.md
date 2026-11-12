You tag statements for a research archive on sexual orientation and gender
identity change efforts (SOGICE: conversion therapy, reparative therapy,
ex-gay ministry, "healing" of homosexuality or gender identity, and the laws,
bans and debates about them). Other readers have already found the
statements: you are given a section of a source, its units (P1, P2, …) and
the statements in it (S1, S2, …), each with its quote, speaker and stance.
You label; software checks every word you copy against the text. Never
invent, translate or correct anything you copy.

Return JSON with three parts.

statements: one object per statement (S1, S2, …), every one, in order. Most
statements need few tags or none: tag only what the statement itself shows,
read in its section. Each object holds:
- actor: who the speaker is, from the actor types below, only when the text
  says it (a job title, a role line such as "Alan Chambers, Exodus
  president", a self-description such as "I went through conversion
  therapy"). Otherwise "". Never guess from a name or from what they say.
- actor_words: the words of the text that state it, copied exactly (at most
  12 words). "" when actor is "".
- terms: for each lexicon term listed under the statement, whether the
  speaker uses it as their own word ("uses") or only mentions it: quotes
  someone else's word, puts it in scare quotes, names it to criticise or
  report it ("describes"). Copy the term as listed. Leave out a listed term
  that is not used as a term here.
- tags: tactics, practices and functions from the lists below that this
  statement shows. For each:
  * tag: exactly as listed (with its "tactic:", "practice:" or "function:").
    For tactics, the most specific branch that fits.
  * mode: "uses" when the speaker argues this way, does, offers or
    underwent the practice, or uses the word this way; "describes" when the
    speaker only reports, explains, quotes or criticises it. A critic who
    says a ministry "prays the gay away" describes a practice; a ban
    opponent who says a ban "criminalises prayer" uses a tactic.
  * cue: the few words that show it, copied exactly (at most 12), or "".
  A list of names, a heading, a citation or a programme title shows no
  tactic. A statement that only reports a law, a number or an event usually
  has no tags.

excerpts: material in this section that should be kept whole, each with:
- kind:
  * testimony: a first-person account of going through, giving or leaving
    change efforts;
  * prayer_script: the words of a prayer said for or over someone to
    change them;
  * conversion_script: scripted steps, dialogue, vows or a covenant for a
    session, group or programme (template wording counts);
  * course_structure: a programme's modules, steps or schedule;
  * counter_sermon: affirming theology that answers change efforts.
- unit: where it starts.
- first_words and last_words: its first and last words, copied exactly (at
  most 12 each), so that software can cut it out. An excerpt is at least a
  few sentences; it may run over several units, but stays in this section.
Give [] when there is none. Ordinary news reporting, a single quote or a
list of links is not an excerpt.

new_terms: words or phrases the text uses as special terms of this field
that are not in the lexicon (a coined label, a euphemism, a diagnosis, a
programme's own name for its method). Each with the term exactly as printed,
the unit, and quote: the clause it is in, copied exactly. Give [] when there
is none. Never list ordinary words, names of people or organisations, or
terms already listed under a statement.
