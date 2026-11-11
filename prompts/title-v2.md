You read the first page of a document kept by a research archive on sexual
orientation and gender identity change efforts. The archive needs the
document's title, who issued it and who wrote it. You copy; software finds
your copy on the page. Never invent, translate, shorten or correct anything.

Return JSON:
- title: the document's title exactly as printed on this page (same
  language, spelling, capitals and punctuation). If the title runs over
  several lines, copy it in reading order with single spaces. Include a
  subtitle only when the page prints it as part of the title. For a report,
  law or official document, the title is the line that names its subject
  ("Rapport d'information … sur les pratiques …"), not a number, a code,
  a running header, the name of the institution or a date on its own. If
  the page shows no title, return "".
- issued_by: the organisation that issued or published the document, as
  printed on the page: an institution, government body, parliament,
  committee, ministry, company, church, journal, magazine or publisher.
  Never a person: a person who wrote the document goes in authors, even when
  no organisation is named. Not the heading of a section, a sample or a
  form, and not a place on its own. If the page names no such organisation,
  return "".
- authors: the people the page names as the document's authors, each
  name as printed, one per item, without degrees, job titles or
  affiliations ("Jane Doe, Ph.D., Director" → "Jane Doe"). Only people who
  wrote or signed the document, not people it is about, quotes, thanks or
  addresses. If the page names none, return [].
- kind: report, article, book, chapter, law, letter, statement,
  presentation, form, web page or other.
