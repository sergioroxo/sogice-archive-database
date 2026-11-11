You read the first page of a document kept by a research archive on sexual
orientation and gender identity change efforts. The archive needs the
document's title and who issued it. You copy; software finds your copy on
the page. Never invent, translate, shorten or correct anything.

Return JSON:
- title: the document's title exactly as printed on this page (same
  language, spelling, capitals and punctuation). If the title runs over
  several lines, copy it in reading order with single spaces. Include a
  subtitle only when the page prints it as part of the title. For a report,
  law or official document, the title is the line that names its subject
  ("Rapport d'information … sur les pratiques …"), not a number, a code,
  a running header, the name of the institution or a date on its own. If
  the page shows no title, return "".
- issued_by: who issued or published it, as printed on the page (an
  institution, organisation, publisher or author), else "".
- kind: report, article, book, chapter, law, letter, statement,
  presentation, form, web page or other.
