# Fingerprints: what a site's own code says about who runs it

Organisations do not always say that they share people, money or a web
manager with another organisation. Their web pages sometimes do: a page's
code carries identifiers that only the site's owner, or whoever runs the
site for them, can put there. `python -m archive fingerprints` reads every
preserved web page for them. It is software only: no model, no network.
Code: `archive/fingerprints.py`.

## What it looks for

| Kind | Example | Strength when two sites share it |
|---|---|---|
| UK charity or company number, US tax number (EIN) | "Registered charity no. 1234567" | strong: one legal owner |
| PayPal donation button, Mailchimp account, AdSense publisher | `hosted_button_id=…`, `list-manage.com/subscribe?u=…` | strong: one financial owner |
| Google Analytics account, GA4 stream, Tag Manager container, Facebook app or pixel | `UA-53159868-29`, `GTM-…` | medium: one web manager, which may be an agency serving several clients |
| A profile the site declares as its own | `"sameAs": ["https://www.facebook.com/…"]` in its structured data | medium |
| Contact email | `info@…` | weak: directories and articles list other groups' addresses |

It also keeps what each page says about itself: its title, its site name
(`og:site_name`), the organisation named in its structured data, its
copyright line, and the outside servers it loads files from. These name the
sites in the report, and they are the material for repairing missing or poor
source titles (`docs/MODELS.md`: the "r4501" problem).

Each fingerprint is **located**: offsets into the stored page code (decoded
as UTF-8), the snippet as written, the copy's capture date, and **where in
the page it sits**: the site's own code (a script), structured data, a meta
tag, a link or form, switched-off code (an HTML comment), or visible text.
`shown` says the value also appears in the visible text.

## What it does not say

A shared fingerprint is a finding about page code, not a relation between
organisations. The report says "the page code of A and B carries the same
X, in these years, in this place", never "A is affiliated with B". Testing
it on the archive (11 November 2026) showed why the difference matters:

- **A watchdog quotes what it documents.** A ministry's PayPal donation
  button also appears on a watchdog's site, because its report on
  conversion therapy online reproduces the button's link as evidence. The
  report marks a value shown as text in a link as "this page may be quoting
  it".
- **Platforms put their own identifiers on every site they host.** Blogger's
  and Google Site Kit's advertising IDs (`ca-host-pub-…`), WordPress.com's
  analytics, Medium's, Tumblr's and Teachable's. These are set apart:
  identifiers seen on a platform's own site, on a site it hosts, or on more
  than 15 sites.
- **Providers do the same.** When most of the sites in the archive that load
  files from one server carry the identifier (four of four sites on one
  church-website provider share a Facebook app), it is probably the
  provider's. For every cluster the report lists the servers all its sites
  load files from, with how many of that server's sites carry the
  identifier: three sites sharing one analytics property are 3 of the 11
  sites in the archive built with the same website builder, which is for
  the researcher to weigh.
- **One account, many properties.** A Google Analytics account whose
  properties run to `-29` manages at least 29 sites; that is more likely an
  agency than one organisation. The snippet keeps the property number.
- **Media groups and campaigns** (Hearst, Springer Nature, one campaign's
  two domains) are true links that tell us little.

Personal email addresses and private postal addresses are personal data:
they are used for matching, and nothing from this table is published.

## Using it

```bash
python -m archive fingerprints                  # scan pages not yet scanned, write the report
python -m archive fingerprints --again          # rescan everything (after the scanner changes)
python -m archive fingerprints --report-only    # report from what is stored
python -m archive fingerprints --report-only --queue-sites --skip-site npr.org   # queue the sites' home pages
python -m archive queue-add URL … --origin NAME  # add any pages to the source queue
```

The report goes to `data/reports/fingerprints-*.md`: strong, medium and weak
clusters, each site with its name, pages, years, places and snippets, then
the platform and provider identifiers apart.

`--queue-sites` adds the home page of every site in a cluster to the source
queue (and, for a site known only from Wayback copies, its home page as
captured then), so the sites can be preserved and scanned again. Media
outlets can be left out with `--skip-site`.

## First run (11 November 2026)

5,486 preserved web pages; 29,000 fingerprints. Shared by two or more sites,
after setting platforms and providers apart: 5 strong, 29 medium, 135 weak
(email) clusters; 11 identifiers set apart as a platform's or provider's.
Most medium clusters are media groups or one organisation's several
domains; about eight link separate ministries or projects to one another
(whether their pages disclose the link is not yet checked). The clusters are named only in the
local report: they are unreviewed findings, not published. 38 of the sites'
home pages were added to the source queue (16 new sources); 19 news and
media sites were left out.

## Not decided yet

- **Relation types.** Publishing a finding needs relation types that say
  what was found ("shares an analytics account with", "shares a donation
  account with"), with the fingerprints as evidence. That is a vocabulary
  change for Sérgio.
- **Servers and certificates.** The fetcher does not yet record a page's
  server address, name servers or TLS certificate. A certificate that covers
  several domains is strong evidence; a shared server address is weak
  (shared hosting).
- **Reading the pages that say who runs a site.** Footers, about, contact
  and legal-notice pages, and the first page of PDFs, could be read by the
  small local model (Gemma 4 12B) to quote the owner, address and real
  title; software would locate the quotes.
- **Checking against registers.** A Claude agent with web search could check
  clusters against charity and company registers and Wikidata, proposing
  links with their sources.
