# Source authors

Who wrote a text is stored on the source (`source.author`) and shown to both
readers in the source header ("Author: …", the first five names, then "and N
others"). Software finds it. A model is used only for the last of the four
ways below: the titles model names the people on a document's first page,
and software locates each name on the page.

## Why

Until 11 November the readers were given a source's title and publisher but
never its author. On the holdout, an essay by Jaco van Zyl was credited to
Genspect, its publisher, and the readers disagreed over whether "the
journal" or "the authors" speak in a scholarly article. Most of the
holdout's 254 disputed statements came from this gap (`docs/TESTSET.md`).

## Where the names come from

All of a source's copies are used, including the copies of sources merged
into it. In order of trust:

1. **The DOI record.** The authors registered with Crossref, or OpenAlex
   when Crossref has none, taken from the reply stored when the source was
   fetched. Nothing is fetched again.
2. **The page code**, best first:
   - `citation_author` tags (scholarly pages; "Last, First" is turned round);
   - a structured-data (JSON-LD) author who is a person, not an organisation;
   - the author meta tags (`author`, `article:author`, `dc.creator`,
     `parsely-author`, `byl`);
   - Yoast's "Written by" label;
   - a `rel="author"` link, only when the page links at most two names.

   A name from page code is used only if the page also prints it. A theme's
   or webmaster's name that the reader never sees is not the text's author.
3. **The PDF's Author field**, only when the first page prints the name.
4. **The first page, read by the titles model** (`docs/TITLES.md`, prompt
   `title-v2`), for PDFs, Word files and plain text: the people it names as
   the document's authors, each located on the page. Used only when 1–3
   name no one.

## Names that are dropped

- Generic names: "admin", "Staff", "Editor", "The Editors", "Guest Writer",
  news agencies (Lusa, AFP, Reuters, Europa Press and others).
- Roles and affiliations after a comma: "Jane Doe, Clinical Psychologist"
  becomes "Jane Doe".
- Lead-ins: "By", "Author:", "View all posts by".
- Anything with an address, a link or digits, a single word, more than eight
  words, a name that ends on a function word ("Council on"), and the site's
  or publisher's own name.
- A name with a word that only organisations or topics use: "LLP", "Inc",
  "Society", "Committee", "Network", "theory", "identity" and others
  (`ORG_WORDS` in `archive/authors.py`), so "McDermott Will & Emery LLP" is
  not two people.

"and" splits names only when every part is a full name, so "Women and
Equalities Committee" stays whole. When two copies of a source name
different authors, with no name word in common, the source is left without
an author ("copies disagree").

## Records

Each author is an `edit` decision on the source by `rule:authors/v1`, with
the method, the copy and the span where the name was found. A run is undone
with `python -m archive review undo --run RUN`. An author set by the
researcher is never changed. A source whose author changes must be read
again for the change to reach its readings.

```bash
python -m archive authors --dry-run          # report only
python -m archive authors                    # sources without an author
python -m archive authors --source SRC --again
```

The report goes to `data/reports/authors-*.md`, one line per source with
the name and how it was found.

## First run (11 November 2026)

Tried first on a copy of the archive (1,258 authors). It was applied to the
archive after the publishers set by the titles run were sorted
(`docs/TITLES.md`): a person left in the publisher field is taken for the
organisation and refused as an author. The archive run (backup taken
first) gave 1,341 of 6,804 sources an author.

| How | Sources |
|---|---:|
| Structured data | 796 |
| Crossref | 191 |
| Author meta tag | 100 |
| First page, titles model, two readings agreeing | 93 |
| `citation_author` | 69 |
| Author link | 59 |
| PDF Author field, printed on page 1 | 30 |
| "Written by" label | 2 |
| OpenAlex | 1 |
| None found | 5,463 |

By kind of copy:

| Kind | Sources | With an author |
|---|---:|---:|
| Web pages | 5,318 | 1,064 |
| PDFs | 586 | 109 |
| Recordings | 14 | 0 |
| Other, or a DOI record with no stored copy | 886 | 168 |

On the copy, about 1% of the names were wrong, mostly topic or section
labels taken for people ("Queer theory", "Sexual Identity"). The archive run
refuses those through the organisation and topic words. Still wrong: a tag
or listing page credited to the writers of the stories it lists. These can
be undone one by one. Most sources still have no author: pages without a
byline, organisations' own pages, and PDFs whose metadata does not match
their first page.

## Effect on the dev set

Five dev sources' headers changed: two gained an author (France 24's
David Rich and Christian Concern's Sean Redfearn), and three gained repaired
titles. Both readers re-read them.

The readers now credit each article's own voice to its byline. Two dev
expectations had named only the outlet, or "author", as the speaker. They
now also accept the named author: the page prints "By: David RICH", and
"Sean Redfearn talks to Jack Sterling". With that change, no automatic dev
check fails that passed before. One judged check regressed, through a
publisher the titles run set wrongly, not through an author
(`docs/TESTSET.md`).

In the archive, nine already-read sources were read again with their
authors. Disputed statements fell from 450 to 378:

- on the APA article, 172 to 70;
- on Genspect's essay, 58 to 20.

There, the readers now agree on who wrote the text. On two news pages,
though, the 31B credits the narration to the byline and Qwen to the
outlet. 78 disputed statements differ only in that (`docs/TESTSET.md`).
