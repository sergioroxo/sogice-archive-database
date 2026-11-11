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
   Gemma 4 12B (role `titles` in `config/models.yaml`, prompt `title-v1`)
   copies the title and who issued it from the first page. Software locates
   both on that page; a title it cannot find is not used. The issuer fills an
   empty publisher.

Each repair is an `edit` decision on the source: the old title and
publisher, the method, the copy, the quoted span and the run. A run is
undone with `python -m archive review undo --run …`. A title Sérgio has set
is never changed, and a title that is not poor is never touched. Sources
with no preserved copy (about 370) keep their title until they are fetched.

```bash
python -m archive titles --no-model --dry-run   # what page code alone would change
python -m archive titles                        # page code, then first pages (local model)
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
