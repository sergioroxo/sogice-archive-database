"""Turn a stored file into canonical text (no model).

* HTML  -> trafilatura main text (comments and tables kept, favour recall);
           falls back to all visible text if trafilatura finds no article.
           Title, date, author and site name come from trafilatura metadata.
* PDF   -> ``pdftotext`` (poppler), reading order, one page per form feed.
           Pages are joined with "\\n\\f\\n" so every character keeps its page
           (``page_of``). Pages with almost no text mark the file ``needs_ocr``;
           a page-sized image on most pages marks it ``scanned`` (OCR layer).
* DOCX  -> paragraphs from word/document.xml (standard library), then
           footnotes and endnotes.
* DOC   -> macOS ``textutil``.
* TXT   -> decoded as UTF-8 (cp1252 fallback).

Canonical text: Unicode NFC, \\n line ends, no control characters except tab,
newline and form feed, no trailing spaces, at most one blank line in a row.
Every offset in the archive (passages, usages) counts characters of this text.
"""
from __future__ import annotations

import bisect
import re
import subprocess
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

PAGE_BREAK = "\n\f\n"
PDF_TIMEOUT = 180
MIN_PAGE_CHARS = 25          # a page with fewer letters is probably an image
OCR_PAGE_SHARE = 0.5         # more than half such pages -> needs_ocr
SCANNED_MIN_PIXELS = 1_000_000   # an image this large is a scanned page
SCANNED_PAGE_SHARE = 0.8
SHORT_TEXT = 200
MAX_DOCX_XML = 64 * 1024 * 1024
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_CONTROL = re.compile("[\x00-\x08\x0b\x0e-\x1f\x7f\u200b\ufeff]")  # keeps \t \n \f
_TRAILING = re.compile(r"[ \t]+\n")
_BLANKS = re.compile(r"\n{3,}")


class ExtractionError(RuntimeError):
    """The file could not be read as the expected type."""


@dataclass
class Extraction:
    text: str
    tool: str
    flags: list = field(default_factory=list)
    page_starts: list | None = None     # PDF: offset where each page starts (page 1 = index 0)
    cues: list | None = None            # captions: (char_start, seconds_start, seconds_end) per cue
    title: str | None = None
    date: str | None = None             # as found in the file's metadata
    author: str | None = None
    sitename: str | None = None

    def page_of(self, offset: int) -> int | None:
        """1-based page holding the character at ``offset`` (None if not paged)."""
        if not self.page_starts:
            return None
        return bisect.bisect_right(self.page_starts, offset)

    def time_of(self, offset: int) -> tuple[float, float] | None:
        """(start, end) seconds of the caption cue holding ``offset`` (None if not timed)."""
        if not self.cues:
            return None
        index = bisect.bisect_right([cue[0] for cue in self.cues], offset) - 1
        _, start, end = self.cues[max(index, 0)]
        return start, end


def canonicalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    text = _TRAILING.sub("\n", text)
    text = _BLANKS.sub("\n\n", text)
    return text.strip()


def _finish(extraction: Extraction) -> Extraction:
    letters = sum(ch.isalpha() for ch in extraction.text)
    if letters == 0:
        extraction.flags.append("no_text")
    elif len(extraction.text) < SHORT_TEXT:
        extraction.flags.append("short_text")
    extraction.flags = sorted(set(extraction.flags))
    return extraction


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_EMPTY_CELLS = re.compile(r"\|(?:[ \t]*\|)+")
_SEPARATOR_LINE = re.compile(r"^[\s|:\-]*$")


def clean_table_noise(text: str) -> str:
    """Drop the empty cells that layout tables leave in trafilatura's text.

    Old sites (1990s-2000s) are built from nested tables; trafilatura renders
    every cell with '|', which buries the words in '| | | |'. Lines made only
    of pipes, dashes and spaces are dropped, runs of empty cells become one
    '|', and pipes at the start or end of a line are removed. A single pipe
    inside text ("Title | Site") is kept.
    """
    out = []
    for line in text.split("\n"):
        if "|" in line:
            if _SEPARATOR_LINE.match(line):
                continue
            line = _EMPTY_CELLS.sub("|", line).strip().strip("|").strip()
            if not line:
                continue
        out.append(line)
    return "\n".join(out)


def extract_html(data: bytes, url: str | None = None) -> Extraction:
    import trafilatura
    from trafilatura.metadata import extract_metadata

    version = getattr(trafilatura, "__version__", "?")
    tree = trafilatura.load_html(data)
    if tree is None:
        raise ExtractionError("not parseable as HTML")
    meta = extract_metadata(trafilatura.load_html(data), default_url=url)
    text = trafilatura.extract(
        tree, url=url, output_format="txt", include_comments=True, include_tables=True,
        favor_recall=True, deduplicate=False, with_metadata=False)
    flags, tool = [], f"trafilatura {version}"
    if not text or not text.strip():
        text = trafilatura.html2txt(data) or ""
        flags.append("fallback_visible_text")
        tool = f"trafilatura {version} html2txt"
    # Dates only from explicit metadata: a full-page search turns copyright
    # years and Wayback capture times into fake publication dates.
    from htmldate import find_date
    try:
        date = find_date(data,
                         extensive_search=False, original_date=True)
    except Exception:  # noqa: BLE001 - htmldate is best effort
        date = None
    return _finish(Extraction(
        text=canonicalize(clean_table_noise(text)), tool=tool, flags=flags,
        title=(meta.title if meta else None) or None, date=date or None,
        author=(meta.author if meta else None) or None,
        sitename=(meta.sitename if meta else None) or None))


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: int = PDF_TIMEOUT) -> str:
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError:
        raise ExtractionError(f"{cmd[0]} is not installed") from None
    except subprocess.TimeoutExpired:
        raise ExtractionError(f"{cmd[0]} took longer than {timeout} s") from None
    if done.returncode != 0:
        raise ExtractionError(f"{cmd[0]} failed: {done.stderr.decode('utf-8', 'replace')[:300]}")
    return done.stdout.decode("utf-8", "replace")


def _pdf_info(path: Path) -> dict:
    try:
        out = _run(["pdfinfo", "-enc", "UTF-8", str(path)], timeout=60)
    except ExtractionError:
        return {}
    info = {}
    for line in out.splitlines():
        key, _, value = line.partition(":")
        if value.strip():
            info[key.strip()] = value.strip()
    return info


def _pdf_images(path: Path) -> dict:
    """{'pages': n, 'image_pages': pages with a page-sized image}, from pdfimages -list."""
    try:
        out = _run(["pdfimages", "-list", str(path)], timeout=60)
    except ExtractionError:
        return {}
    big_pages = set()
    for line in out.splitlines()[2:]:
        cols = line.split()
        if len(cols) > 4 and cols[0].isdigit() and cols[3].isdigit() and cols[4].isdigit():
            if int(cols[3]) * int(cols[4]) >= SCANNED_MIN_PIXELS:
                big_pages.add(int(cols[0]))
    return {"image_pages": len(big_pages)}


def _scanned_share(images: dict, pages: int | None = None) -> float:
    return images.get("image_pages", 0) / max(1, images.get("pages") or pages or 1)


def _pdftotext_version() -> str:
    done = subprocess.run(["pdftotext", "-v"], capture_output=True, check=False)
    match = re.search(r"version\s+([\d.]+)", (done.stderr + done.stdout).decode("utf-8", "replace"))
    return match.group(1) if match else "?"


def extract_pdf(data: bytes) -> Extraction:
    with tempfile.TemporaryDirectory(prefix="sogice-pdf-") as tmp:
        path = Path(tmp) / "in.pdf"
        path.write_bytes(data)
        raw = _run(["pdftotext", "-enc", "UTF-8", "-eol", "unix", str(path), "-"])
        info = _pdf_info(path)
        pdf_images = _pdf_images(path)
    pages = raw.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()                      # pdftotext ends the last page with \f too
    pages = [canonicalize(page) for page in pages] or [""]
    pdf_images["pages"] = len(pages)
    starts, parts, offset = [], [], 0
    for number, page in enumerate(pages):
        if number:
            parts.append(PAGE_BREAK)
            offset += len(PAGE_BREAK)
        starts.append(offset)
        parts.append(page)
        offset += len(page)
    text = "".join(parts)
    if not text.strip():
        text = ""                        # only page breaks: no text at all
    flags = []
    thin = sum(1 for page in pages if sum(ch.isalpha() for ch in page) < MIN_PAGE_CHARS)
    if thin and thin / len(pages) > OCR_PAGE_SHARE:
        flags.append("needs_ocr")
    if text and _scanned_share(pdf_images) >= SCANNED_PAGE_SHARE:
        flags.append("scanned")          # text comes from an OCR layer: expect errors
    if "Encrypted" in info and info["Encrypted"].startswith("yes"):
        flags.append("pdf_encrypted")
    date = info.get("CreationDate")
    return _finish(Extraction(
        text=text, tool=f"pdftotext {_pdftotext_version()}", flags=flags, page_starts=starts,
        title=info.get("Title") or None, date=date, author=info.get("Author") or None))


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------

def _docx_paragraphs(xml: bytes) -> list[str]:
    root = ElementTree.fromstring(xml)
    paragraphs = []
    for paragraph in root.iter(f"{W_NS}p"):
        pieces = []
        for node in paragraph.iter():
            if node.tag == f"{W_NS}t" and node.text:
                pieces.append(node.text)
            elif node.tag == f"{W_NS}tab":
                pieces.append("\t")
            elif node.tag in (f"{W_NS}br", f"{W_NS}cr"):
                pieces.append("\n")
        paragraphs.append("".join(pieces))
    return paragraphs


def extract_docx(data: bytes) -> Extraction:
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        raise ExtractionError("not a DOCX (zip) file") from None
    with archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            raise ExtractionError("DOCX has no word/document.xml")
        blocks = []
        for name, heading in (("word/document.xml", None), ("word/footnotes.xml", "Footnotes"),
                              ("word/endnotes.xml", "Endnotes")):
            if name not in names:
                continue
            if archive.getinfo(name).file_size > MAX_DOCX_XML:
                raise ExtractionError(f"{name} is too large")
            paragraphs = [p for p in _docx_paragraphs(archive.read(name)) if p.strip()]
            if paragraphs:
                blocks.append(([heading] if heading else []) + paragraphs)
        title = None
        if "docProps/core.xml" in names:
            core = ElementTree.fromstring(archive.read("docProps/core.xml"))
            node = core.find("{http://purl.org/dc/elements/1.1/}title")
            title = node.text.strip() if node is not None and node.text else None
    text = "\n\n".join("\n\n".join(block) for block in blocks)
    return _finish(Extraction(text=canonicalize(text), tool="docx-xml (stdlib)", title=title))


def extract_doc(data: bytes) -> Extraction:
    with tempfile.TemporaryDirectory(prefix="sogice-doc-") as tmp:
        path = Path(tmp) / "in.doc"
        path.write_bytes(data)
        text = _run(["textutil", "-convert", "txt", "-stdout", str(path)], timeout=120)
    return _finish(Extraction(text=canonicalize(text), tool="textutil"))


def extract_txt(data: bytes) -> Extraction:
    try:
        text, flags = data.decode("utf-8"), []
    except UnicodeDecodeError:
        text, flags = data.decode("cp1252", "replace"), ["decoded_cp1252"]
    return _finish(Extraction(text=canonicalize(text), tool="plain text", flags=flags))


# ---------------------------------------------------------------------------
# Captions (WebVTT / SRT)
# ---------------------------------------------------------------------------

_TIMESTAMP = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def _seconds(stamp: str) -> float:
    match = _TIMESTAMP.search(stamp)
    if not match:
        raise ValueError(f"bad timestamp {stamp!r}")
    hours, minutes, seconds, millis = match.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")) / 1000


def _clean_cue(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)            # <c>, <00:00:01.000>, <i>...
    text = re.sub(r"\{\\.*?\}", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[tuple[str, int]]:
    out = []
    for match in re.finditer(r"\S+", text):
        word = re.sub(r"(^[^\w]+|[^\w]+$)", "", match.group(0).lower())
        if word:
            out.append((word, match.start()))
    return out


def _new_suffix(previous: str, current: str, min_overlap: int = 3) -> str:
    """Drop the head of ``current`` that repeats the tail of ``previous`` (rolling auto-captions).

    Ported from the old transcripts.deoverlap_caption_chunks.
    """
    if not previous:
        return current
    before, now = _tokens(previous), _tokens(current)
    if not before or not now:
        return current
    if [w for w, _ in now] == [w for w, _ in before]:
        return ""
    for size in range(min(len(before), len(now)), min_overlap - 1, -1):
        if [w for w, _ in before[-size:]] == [w for w, _ in now[:size]]:
            return "" if size >= len(now) else current[now[size][1]:].strip()
    return current


def parse_cues(raw: str) -> list[tuple[float, float, str]]:
    """(start, end, text) per caption line, repeats removed.

    YouTube auto-captions repeat the previous line at the top of every cue and
    add 10 ms cues that repeat the line just finished. So lines are compared
    one by one with the last line kept: an exact repeat is dropped, and a
    line that starts with the end of the last one keeps only its new words
    (rolling-window captions; at least three words of overlap).
    """
    text = raw.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    cues, last = [], ""
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        at = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if at < 0:
            continue                                  # header, NOTE, STYLE, bare numbers
        left, _, right = lines[at].partition("-->")
        try:
            start, end = _seconds(left), _seconds(right)
        except ValueError:
            continue
        for raw_line in lines[at + 1:]:
            line = _clean_cue(raw_line)
            if not line or line == last:
                continue
            new = _new_suffix(last, line)
            last = line
            if new:
                cues.append((start, end, new))
    return cues


def extract_captions(data: bytes) -> Extraction:
    """Caption text, one cue per line, with a cue map for timecodes."""
    cues = parse_cues(data.decode("utf-8", "replace"))
    parts, spans, offset = [], [], 0
    for start, end, cue in cues:
        cue = canonicalize(cue)
        if not cue:
            continue
        if parts:
            parts.append("\n")
            offset += 1
        spans.append((offset, start, end))
        parts.append(cue)
        offset += len(cue)
    return _finish(Extraction(text="".join(parts), tool="webvtt (stdlib)", cues=spans or None))


EXTRACTORS = {"vtt": extract_captions, "srt": extract_captions, "html": extract_html, "pdf": extract_pdf, "docx": extract_docx,
              "doc": extract_doc, "txt": extract_txt}


def extract(data: bytes, extension: str, *, url: str | None = None) -> Extraction:
    try:
        extractor = EXTRACTORS[extension]
    except KeyError:
        raise ExtractionError(f"no extractor for .{extension}") from None
    if extension == "html":
        return extractor(data, url)
    return extractor(data)
