# Source titles

A source's title is shown to the readers at the top of every section, so a
poor title becomes a poor reading: the French report's catalogue title,
"r4501", was taken as its name and became a speaker over a hundred times
(`docs/MODELS.md`). About 1,190 of the 6,800 sources had a title that was
missing (820), generic ("Home", "Accueil", "Voorpagina"), a file name
("Microsoft Word - 127593789_8.docx"), a domain, a code or one short word.

`python -m archive titles` repairs them from what the preserved copies say
about themselves (code: `archive/titles.py`):

1. **Page code, no model.** A web page's citation title, `og:title`,
   `twitter:title`, structured-data headline, `<title>` without the site's
   name, or first heading, in that order; the first that is not poor itself.
   Left alone: a title that other pages of the same site also give (a slogan
   such as "Dive into anything"), a parked or for-sale domain page, a dateline,
   and a source whose copies disagree (an old capture and a domain now used by
   someone else). A PDF's own metadata title counts only when its first page
   prints it.
2. **First page, small local model.** For PDFs, Word files and plain text,
   the small model (role `titles` in `config/models.yaml`, prompt `title-v2`
   since 11 Nov) copies from the first page the title, the organisation that
   issued it and the people who wrote it. Software locates each on that page;
   what it cannot find is not used. The issuer fills an empty publisher. The
   authors are kept in the decision, for the authors step (`docs/AUTHORS.md`).

Each repair is an `edit` decision on the source: the old title and
publisher, the method, the copy, the quoted span and the run. A run is
undone with `python -m archive review undo --run …`. A title Sérgio has set
is never changed, and a title that is not poor is never touched. Sources
with no preserved copy (about 370) keep their title until they are fetched.

```bash
python -m archive titles --no-model --dry-run   # what page code alone would change
python -m archive titles                        # page code, then first pages (local model)
python -m archive titles --reissue              # re-read first pages whose publisher title-v1 set
```

The report (`data/reports/titles-*.md`) lists every change, old and new.

## First run (11 November 2026)

On a copy first, then on the archive (backup taken before). In the archive
run, of 1,191 poor titles, 556 were repaired: 90 from page code and 466 from
first pages read by the small model; 19 titles the model gave could not be
found on the page and were not used, 13 more gave no usable title, and 602
sources have no copy with text to read (no preserved copy, or a web page with
nothing better). The trial on the copy gave the same 556 (91 and 465). A check of
every ninth first-page repair found the titles right and copied as printed,
often in capitals or in the document's own language (an EU citizens'
initiative appears in Polish, Danish, Estonian, Czech and Slovenian
copies). The French report is now "Rapport fait au nom de la Commission des
lois … sur la proposition de loi interdisant les pratiques visant à modifier
l'orientation sexuelle ou l'identité de genre d'une personne (n° 4021)",
published by the Assemblée nationale.

One repair was wrong and was left out of the archive run (`--exclude`): a
survivors' site whose domain now hosts an unrelated health blog, so the
only preserved copy names the blog. The issuer is sometimes a list of
references rather than the publisher; it only fills an empty publisher.

## Publishers read again (11 November 2026)

Prompt `title-v1` asked for the issuer as "an institution, organisation,
publisher or author". So of the 344 publishers it set, about a third were
people, the documents' authors. A few were headings: the Harvest covenant's
publisher became "Men's Discipleship Group - Sample #1". On the dev set,
that heading made both readers file the second sample under the first.

`title-v2` asks for the issuing organisation and the authors separately.
`titles --reissue` read those 344 first pages again (11 minutes) and
changed a publisher only where the two readings agree:

| Outcome | Sources |
|---|---:|
| Both readings give the same publisher: kept (a `confirm` decision) | 214 |
| The publisher was a person the new reading names as an author: cleared | 79* |
| The same, and the new issuer is the site's own name: replaced | 25 |
| The readings name different organisations: kept, listed for review | 26 |

\* One of the 79 was not a person. The new reading listed "Exodus
International" as an author of its own press release, and the name filter
let it through. A ruling restored the publisher, and "International" and
"Global" now mark a name as an organisation's. In two others the old value
held an organisation and a person ("Covenant Eyes, Inc.; Ronald DeHaas").
Only the person was kept, as author.

Why the replacement issuer must match the site: a trial without that check
used whatever the new reading named. On first pages that is often an
addressee (a committee, a court, the UN Independent Expert), a title
("The WPATH Files") or the subject: Mia Hughes's report on WPATH got WPATH
as its publisher. That trial was undone and read again. The 25 replacements
are mostly Exodus Global Alliance on its own pages, and the Family Research
Council's and Gender Wholeness's own PDFs.

Of the 26 disagreements, 23 keep the old publisher. In most of them the
new reading named an addressee (the Federal Trade Commission, a court of
appeals, the Department of Justice Canada), the Human Rights Council for
the Independent Expert's own papers, or the same body under another name.
McDermott Will & Emery's report for the Mattachine Society keeps the law
firm.
Claude ruled on the other three (recorded decisions by the review model):

- the covenant's heading was cleared;
- "Eastern Europe and Central Asia (EECA)", part of a report's title, was
  cleared;
- "EUROPOS SĄJUNGOJE", the end of a title, became "Europos piliečių
  iniciatyva (EPI)".

The new reading also names the people on 194 first pages. Those names are
used as authors only where both readings agree: the old reading had put one
of them in the publisher field (104 sources). A single reading's names are
kept in its decision but not used. In a sample they included addressees
(the UN Independent Expert for states' submissions to him), committee
members and a speaker label ("Participant – R").

Effect on reading: the nine already-read sources whose header changed were
read again. The covenant's judged check, which had regressed when its
publisher was a sample heading, passes again (`docs/TESTSET.md`).
