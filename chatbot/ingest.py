"""Turn uploaded files and web pages into titled sections, then into chunks small enough to quote.

Supported: Markdown, plain text, PDF (text layer, via pypdf) and HTML pages fetched from a URL.
Scanned PDFs without a text layer are rejected with a clear message instead of being stored empty.
"""
from __future__ import annotations

import io
import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_CHUNK_CHARS = 1100
TEXT_KINDS = {".md": "md", ".markdown": "md", ".txt": "txt"}


class IngestError(ValueError):
    """Shown to the admin as is."""


@dataclass
class Section:
    heading: str
    text: str


@dataclass
class Parsed:
    title: str
    kind: str
    sections: list[Section]


# ------------------------------------------------------------------------------------------------
# Parsers
# ------------------------------------------------------------------------------------------------

def parse_markdown(text: str, fallback_title: str) -> Parsed:
    title, heading, buf, sections = "", "", [], []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            sections.append(Section(heading or title or fallback_title, body))

    for line in text.splitlines():
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif re.match(r"^#{1,3} ", line):
            flush()
            heading, buf = line.lstrip("#").strip(), []
        else:
            buf.append(line)
    flush()
    return Parsed(title or fallback_title, "md", sections)


def parse_text(text: str, fallback_title: str) -> Parsed:
    parsed = parse_markdown(text, fallback_title)  # plain text with no headings becomes one section
    return Parsed(parsed.title, "txt", parsed.sections)


def parse_pdf(data: bytes, fallback_title: str) -> Parsed:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [p.extract_text() or "" for p in reader.pages]
    except Exception as e:  # pypdf raises many kinds of errors on broken files
        raise IngestError(f"Couldn't read this PDF ({e.__class__.__name__}).") from e
    text = "\n".join(pages).strip()
    if len(text) < 40:
        raise IngestError("This PDF has no text layer (probably a scan). Upload a text PDF or paste the text.")
    meta_title = ""
    try:
        meta_title = (reader.metadata.title or "").strip() if reader.metadata else ""
    except Exception:
        pass
    # Treat short lines that look like numbered or standalone headings as section breaks.
    lines = [ln.strip() for ln in text.splitlines()]
    title = meta_title or next((ln for ln in lines if ln), fallback_title)
    heading, buf, sections = title, [], []
    for ln in lines:
        if not ln or ln == title:
            continue
        if re.match(r"^(\d+[.)]\s+)\S.{0,60}$", ln) and not ln.endswith((".", ",", ";")):
            if buf:
                sections.append(Section(heading, " ".join(buf)))
            heading, buf = re.sub(r"^\d+[.)]\s+", "", ln), []
        else:
            buf.append(ln)
    if buf:
        sections.append(Section(heading, " ".join(buf)))
    return Parsed(title, "pdf", sections)


class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe", "template"}
    BLOCK = {"p", "div", "li", "tr", "br", "section", "article", "table", "ul", "ol", "dd", "dt", "td", "th"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.sections = "", []
        self.heading, self.buf, self.skip, self.in_title, self.in_h = "", [], 0, False, None
        self.hbuf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag in {"h1", "h2", "h3"} and not self.skip:
            self.in_h, self.hbuf = tag, []
        elif tag in self.BLOCK:
            self.buf.append("\n")
        if tag == "li":
            self.buf.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self.in_title = False
        elif tag == self.in_h:
            text = " ".join("".join(self.hbuf).split())
            self.in_h = None
            if tag == "h1" and not self.title:
                self.title = text
            self.flush()
            self.heading = text
        elif tag in self.BLOCK:
            self.buf.append("\n")

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title = self.title or data.strip()
        elif self.in_h:
            self.hbuf.append(data)
        else:
            self.buf.append(data)

    def flush(self):
        lines = [" ".join(ln.split()) for ln in "".join(self.buf).splitlines()]
        body = "\n".join(ln for ln in lines if ln and ln != "-")
        if body:
            self.sections.append(Section(self.heading or self.title, body))
        self.buf = []


def parse_html(html: str, fallback_title: str) -> Parsed:
    p = _HTMLText()
    p.feed(html)
    p.flush()
    title = p.title or fallback_title
    if sum(len(s.text) for s in p.sections) < 40:
        raise IngestError("Found almost no text on this page (it may be built with JavaScript).")
    return Parsed(title, "url", [Section(s.heading or title, s.text) for s in p.sections])


def parse_upload(filename: str, data: bytes) -> Parsed:
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError("File is larger than 5 MB.")
    suffix = Path(filename).suffix.lower()
    stem = Path(filename).stem.replace("-", " ").replace("_", " ").strip() or "Document"
    if suffix == ".pdf":
        return parse_pdf(data, stem)
    if suffix in {".html", ".htm"}:
        return parse_html(data.decode("utf-8", errors="replace"), stem)
    if suffix in TEXT_KINDS:
        text = data.decode("utf-8", errors="replace")
        return parse_markdown(text, stem) if TEXT_KINDS[suffix] == "md" else parse_text(text, stem)
    raise IngestError("Use PDF, Markdown (.md), plain text (.txt) or HTML.")


# ------------------------------------------------------------------------------------------------
# URLs, with a guard against fetching internal addresses (SSRF)
# ------------------------------------------------------------------------------------------------

def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


def fetch_url(url: str, client=None, check_host=_public_host) -> Parsed:
    import httpx

    own = client is None
    client = client or httpx.Client(timeout=10, follow_redirects=False,
                                    headers={"User-Agent": "website-chatbot/1.0 (+document import)"})
    try:
        for _ in range(4):  # follow up to 3 redirects, checking each hop
            parts = urlparse(url)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                raise IngestError("Only http(s) URLs can be imported.")
            if not check_host(parts.hostname):
                raise IngestError("This address points to a private or unknown host.")
            with client.stream("GET", url) as r:
                if r.is_redirect and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code != 200:
                    raise IngestError(f"The page returned HTTP {r.status_code}.")
                data = b""
                for part in r.iter_bytes():
                    data += part
                    if len(data) > MAX_UPLOAD_BYTES:
                        raise IngestError("The page is larger than 5 MB.")
                ctype = r.headers.get("content-type", "").lower()
            fallback = parts.hostname + parts.path
            if "pdf" in ctype:
                parsed = parse_pdf(data, fallback)
            elif "html" in ctype or data.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
                parsed = parse_html(data.decode(r.encoding or "utf-8", errors="replace"), fallback)
            elif ctype.startswith("text/"):
                parsed = parse_text(data.decode(r.encoding or "utf-8", errors="replace"), fallback)
            else:
                raise IngestError(f"Unsupported content type: {ctype or 'unknown'}.")
            return Parsed(parsed.title, "url", parsed.sections)
        raise IngestError("Too many redirects.")
    except httpx.HTTPError as e:
        raise IngestError(f"Couldn't fetch the page ({e.__class__.__name__}).") from e
    finally:
        if own:
            client.close()


# ------------------------------------------------------------------------------------------------
# Chunking
# ------------------------------------------------------------------------------------------------

def chunk_sections(sections: list[Section], max_chars: int = MAX_CHUNK_CHARS) -> list[Section]:
    """Keep each section whole when it fits; otherwise split on paragraphs (then lines) and repeat the heading."""
    out: list[Section] = []
    for s in sections:
        text = s.text.strip()
        if len(text) <= max_chars:
            out.append(Section(s.heading, text))
            continue
        parts = re.split(r"\n\s*\n", text)
        if len(parts) == 1:
            parts = text.splitlines() or [text]
        buf = ""
        for part in parts:
            while len(part) > max_chars:  # one enormous paragraph: hard split on a sentence end if possible
                cut = part.rfind(". ", 0, max_chars)
                cut = cut + 1 if cut > max_chars // 2 else max_chars
                if buf:
                    out.append(Section(s.heading, buf.strip()))
                    buf = ""
                out.append(Section(s.heading, part[:cut].strip()))
                part = part[cut:]
            if len(buf) + len(part) + 2 > max_chars and buf:
                out.append(Section(s.heading, buf.strip()))
                buf = ""
            buf += part + "\n\n"
        if buf.strip():
            out.append(Section(s.heading, buf.strip()))
    return out
