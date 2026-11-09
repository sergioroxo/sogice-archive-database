import textwrap
import zipfile
from io import BytesIO

import pytest

from archive.extract import (PAGE_BREAK, ExtractionError, canonicalize, extract, extract_docx,
                             extract_html, extract_pdf, extract_txt)


def make_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF, one line of Helvetica text per page (empty string = blank page)."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None,
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for text in pages:
        lines = textwrap.wrap(text, 85)
        shown = " T* ".join(f"({line}) Tj" for line in lines)
        stream = f"BT /F1 11 Tf 13 TL 50 750 Td {shown} ET" if text else ""
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        content_id = len(objects)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                       f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>")
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def make_docx(paragraphs: list[str], footnotes: list[str] = (), title: str | None = None) -> bytes:
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

    def body(items):
        return "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in items)

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml",
                         f"<w:document {w}><w:body>{body(paragraphs)}"
                         f"<w:tbl><w:tr><w:tc><w:p><w:r><w:t>cell one</w:t><w:tab/>"
                         f"<w:t>after tab</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>")
        if footnotes:
            archive.writestr("word/footnotes.xml", f"<w:footnotes {w}>{body(footnotes)}</w:footnotes>")
        if title:
            archive.writestr("docProps/core.xml",
                             '<cp:coreProperties xmlns:cp="x" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                             f"<dc:title>{title}</dc:title></cp:coreProperties>")
    return buffer.getvalue()


ARTICLE = """<!DOCTYPE html><html lang="en"><head><title>Ministry promises change | Example News</title>
<meta property="og:site_name" content="Example News">
<meta name="author" content="Jane Writer"><meta property="article:published_time" content="2015-04-09">
</head><body><nav><a href="/">Home</a> <a href="/news">News</a> <a href="/about">About us</a></nav>
<article><h1>Ministry promises change</h1>
<p>The ministry said its weekend courses offered healing from same-sex attraction to anyone who attended.</p>
<p>Former members told reporters that the courses caused lasting harm and that nobody they knew had changed.</p>
<p>A spokesperson for the national psychological association called the claims unfounded and dangerous.</p>
</article><footer>Copyright Example News. All rights reserved.</footer></body></html>"""


def test_canonicalize():
    assert canonicalize("﻿a\r\nb  \n\n\n\nc\x00​d\fe ") == "a\nb\n\ncd\fe"
    assert canonicalize("é") == "é"          # NFC


def test_html_article_and_metadata():
    result = extract_html(ARTICLE.encode(), "https://example.org/a")
    assert "healing from same-sex attraction" in result.text
    assert "lasting harm" in result.text
    assert "About us" not in result.text                 # navigation dropped
    assert result.title.startswith("Ministry promises change")
    assert result.date == "2015-04-09"
    assert result.sitename == "Example News"
    assert result.tool.startswith("trafilatura ")
    assert result.page_starts is None and result.page_of(5) is None


def test_html_without_article_falls_back():
    result = extract_html(b"<html><body><ul><li>Prayer line: 555 0100</li></ul></body></html>")
    assert "Prayer line" in result.text
    assert "fallback_visible_text" in result.flags or result.text


def test_pdf_pages_and_page_of():
    data = make_pdf(["First page about healing ministries.", "Second page continues the turn."])
    result = extract_pdf(data)
    assert result.text.split(PAGE_BREAK) == ["First page about healing ministries.",
                                             "Second page continues the turn."]
    assert result.page_starts == [0, len("First page about healing ministries.") + len(PAGE_BREAK)]
    assert result.page_of(0) == 1
    second = result.text.index("Second")
    assert result.page_of(second) == 2 and result.page_of(second - 1) == 1
    assert result.tool.startswith("pdftotext ")
    assert "needs_ocr" not in result.flags


def test_pdf_mostly_blank_needs_ocr():
    result = extract_pdf(make_pdf(["", "", "Short caption for the third page here."]))
    assert "needs_ocr" in result.flags
    assert len(result.page_starts) == 3 and result.page_of(len(result.text) - 1) == 3


def test_pdf_garbage_raises():
    with pytest.raises(ExtractionError):
        extract_pdf(b"%PDF-1.4 this is not really a pdf")


def test_docx_paragraphs_tables_footnotes():
    data = make_docx(["Our programme helps people leave homosexuality.", "Call us today."],
                     footnotes=["See the 2019 annual report."], title="Programme leaflet")
    result = extract_docx(data)
    assert result.text == ("Our programme helps people leave homosexuality.\n\nCall us today.\n\n"
                           "cell one\tafter tab\n\nFootnotes\n\nSee the 2019 annual report.")
    assert result.title == "Programme leaflet"
    with pytest.raises(ExtractionError):
        extract_docx(b"PK\x03\x04 broken")


def test_txt_and_flags():
    assert extract_txt("café".encode("cp1252")).flags == ["decoded_cp1252", "short_text"]
    assert extract_txt(b"   ").flags == ["no_text"]


def test_dispatch():
    assert extract(b"hello there", "txt").text == "hello there"
    with pytest.raises(ExtractionError):
        extract(b"x", "zip")


def test_html_date_only_from_explicit_metadata():
    page = ("<html><head><title>Welcome</title></head><body><p>" + "Welcome to our ministry. " * 20
            + "</p><footer>© 2025 Our Ministry</footer></body></html>")
    assert extract_html(page.encode()).date is None          # a copyright year is not a date


def test_scanned_pdf_flag(monkeypatch):
    from archive import extract as module
    listing = ("page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio\n"
               "-------------------------------------------------------------------------------\n"
               "   1     0 image    1702  2164  rgb     3   8  jpeg   no         6  0   200   200  217K 2.0%\n"
               "   2     1 image    1701  2163  rgb     3   8  jpeg   no        15  0   200   200  261K 2.4%\n"
               "   2     2 image      40    40  rgb     3   8  jpeg   no        16  0   200   200    1K 2.4%\n")
    real_run = module._run
    monkeypatch.setattr(module, "_run", lambda cmd, timeout=180: listing if cmd[0] == "pdfimages"
                        else real_run(cmd, timeout))
    result = extract_pdf(make_pdf(["Scanned page one with an OCR text layer.",
                                   "Scanned page two with an OCR text layer."]))
    assert "scanned" in result.flags
    monkeypatch.setattr(module, "_run", lambda cmd, timeout=180: listing.splitlines()[0] if cmd[0] == "pdfimages"
                        else real_run(cmd, timeout))
    assert "scanned" not in extract_pdf(make_pdf(["Born-digital page one.", "Page two."])).flags


YOUTUBE_AUTO = """WEBVTT
Kind: captions
Language: en

00:06:44.720 --> 00:06:46.230 align:start position:0%
didn't want to talk.
It<00:06:45.120><c> was</c>

00:06:46.230 --> 00:06:46.240 align:start position:0%
It was

00:06:46.240 --> 00:06:49.000 align:start position:0%
It was
you<00:06:46.600><c> know,</c><00:06:47.000><c> the</c><00:06:47.500><c> most</c><00:06:48.000><c> unhealthy</c>

00:06:49.000 --> 00:06:49.010 align:start position:0%
you know, the most unhealthy

00:06:49.010 --> 00:06:51.000 align:start position:0%
you know, the most unhealthy
environment<00:06:49.500><c> that</c><00:06:50.000><c> I</c>
"""


def test_youtube_auto_caption_repeats_removed():
    from archive.extract import extract_captions, parse_cues
    cues = parse_cues(YOUTUBE_AUTO)
    assert [c[2] for c in cues] == ["didn't want to talk.", "It was", "you know, the most unhealthy",
                                    "environment that I"]
    assert cues[1][0] == 404.72 and cues[2][0] == 406.24
    result = extract_captions(YOUTUBE_AUTO.encode())
    assert result.text == "didn't want to talk.\nIt was\nyou know, the most unhealthy\nenvironment that I"
    assert result.time_of(result.text.index("environment")) == (409.01, 411.0)


def test_clean_table_noise():
    from archive.extract import clean_table_noise
    raw = ("|  | |  | |  | The Ministry\n| | |\n|---|---|\nHome | | Who We Are | | Contact Us\n"
           "Formation | 2009\nHuffPost | Latest News")
    assert clean_table_noise(raw) == ("The Ministry\nHome | Who We Are | Contact Us\n"
                                      "Formation | 2009\nHuffPost | Latest News")
