"""Multi-format enterprise document parsers (Markdown, Text, HTML, CSV/TSV, JSON/JSONL, DOCX, PDF)."""
from __future__ import annotations

import csv
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET
import zipfile


SUPPORTED_EXTENSIONS = (
    ".md",
    ".markdown",
    ".txt",
    ".log",
    ".html",
    ".htm",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".docx",
    ".pdf",
)
MAX_FILE_BYTES = 15 * 1024 * 1024


class _MarkdownHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._out: list[str] = []
        self._heading_level: int | None = None

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag in ("script", "style", "noscript", "svg"):
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            self._out.append("\n\n" + ("#" * level) + " ")
            self._heading_level = level
        elif tag in ("p", "div", "section", "article", "tr", "blockquote"):
            self._out.append("\n")
        elif tag == "li":
            self._out.append("\n- ")
        elif tag == "br":
            self._out.append("\n")
        elif tag in ("td", "th"):
            self._out.append(" | ")

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in ("script", "style", "noscript", "svg") and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "tr", "li"):
            self._out.append("\n")
            self._heading_level = None

    def handle_data(self, data: str):
        if self._skip_depth:
            return
        cleaned = re.sub(r"[ \t\r\n]+", " ", data)
        if cleaned.strip():
            self._out.append(cleaned)

    def get_markdown(self) -> str:
        raw = "".join(self._out)
        lines = [line.strip() for line in raw.splitlines()]
        # Collapse runs of blank lines while keeping paragraph separation
        collapsed: list[str] = []
        prev_blank = False
        for line in lines:
            if not line:
                if not prev_blank and collapsed:
                    collapsed.append("")
                prev_blank = True
            else:
                collapsed.append(line)
                prev_blank = False
        return "\n".join(collapsed).strip()


def _decode_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("unable to decode document text")


def _parse_docx(raw: bytes) -> tuple[str, dict[str, Any]]:
    w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("invalid .docx archive") from exc

    with zf:
        if "word/document.xml" not in zf.namelist():
            raise ValueError("missing word/document.xml inside .docx")
        xml_bytes = zf.read("word/document.xml")

    root = ET.fromstring(xml_bytes)
    body = root.find(f"{w_ns}body")
    if body is None:
        return "", {"paragraphs": 0, "tables": 0}

    blocks: list[str] = []
    para_count = 0
    table_count = 0

    def extract_paragraph(p_elem) -> str:
        parts: list[str] = []
        for elem in p_elem.iter():
            if elem.tag == f"{w_ns}t" and elem.text:
                parts.append(elem.text)
            elif elem.tag == f"{w_ns}tab":
                parts.append("\t")
            elif elem.tag in (f"{w_ns}br", f"{w_ns}cr"):
                parts.append("\n")
        return "".join(parts).strip()

    for child in body:
        if child.tag == f"{w_ns}p":
            txt = extract_paragraph(child)
            if not txt:
                continue
            para_count += 1
            p_style = child.find(f"{w_ns}pPr/{w_ns}pStyle")
            style_val = p_style.attrib.get(f"{w_ns}val", "") if p_style is not None else ""
            m = re.match(r"^(?:Heading|标题\s*)([1-6])$", style_val, re.IGNORECASE)
            if m:
                blocks.append(f"{'#' * int(m.group(1))} {txt}")
            elif style_val.lower() == "title":
                blocks.append(f"# {txt}")
            else:
                blocks.append(txt)
        elif child.tag == f"{w_ns}tbl":
            rows_md: list[str] = []
            for r_idx, tr in enumerate(child.findall(f"{w_ns}tr")):
                cells = []
                for tc in tr.findall(f"{w_ns}tc"):
                    cell_txt = " ".join(
                        extract_paragraph(p) for p in tc.findall(f"{w_ns}p") if extract_paragraph(p)
                    )
                    cells.append(cell_txt.replace("|", "\\|"))
                if cells:
                    rows_md.append("| " + " | ".join(cells) + " |")
                    if r_idx == 0:
                        rows_md.append("| " + " | ".join("---" for _ in cells) + " |")
            if rows_md:
                table_count += 1
                blocks.append("\n".join(rows_md))

    return "\n\n".join(blocks).strip(), {"paragraphs": para_count, "tables": table_count}


def _parse_pdf(raw: bytes) -> tuple[str, dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("pypdf is required to parse PDF files; install tenant-rag[enterprise]") from exc

    reader = PdfReader(io.BytesIO(raw))
    pages: list[str] = []
    for idx, page in enumerate(reader.pages, 1):
        txt = (page.extract_text() or "").strip()
        if txt:
            pages.append(f"## Page {idx}\n\n{txt}")
    return "\n\n".join(pages).strip(), {"page_count": len(reader.pages)}


def _parse_csv(raw: bytes, delimiter: str = ",") -> tuple[str, dict[str, Any]]:
    text = _decode_text(raw)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return "", {"row_count": 0}
    headers = [h.strip() or f"col_{i+1}" for i, h in enumerate(rows[0])]
    blocks: list[str] = []
    for idx, row in enumerate(rows[1:], 1):
        pairs = []
        for col_idx, val in enumerate(row):
            key = headers[col_idx] if col_idx < len(headers) else f"col_{col_idx+1}"
            if val.strip():
                pairs.append(f"- **{key}**: {val.strip()}")
        if pairs:
            blocks.append(f"### Record {idx}\n" + "\n".join(pairs))
    if not blocks:
        blocks.append(" | ".join(headers))
    return "\n\n".join(blocks), {"row_count": max(0, len(rows) - 1), "columns": headers}


def _parse_json(raw: bytes, is_jsonl: bool = False) -> tuple[str, dict[str, Any]]:
    text = _decode_text(raw)
    if is_jsonl:
        items = []
        for line in text.splitlines():
            s = line.strip()
            if s:
                items.append(json.loads(s))
        return json.dumps(items, ensure_ascii=False, indent=2), {"record_count": len(items)}
    obj = json.loads(text)
    return json.dumps(obj, ensure_ascii=False, indent=2), {"json_type": type(obj).__name__}


def parse_document_bytes(filename: str, raw: bytes) -> dict[str, Any]:
    """Extract normalized text, chunk format, and parser metadata from uploaded bytes."""
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("filename is required")
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise ValueError("uploaded file is empty")
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"file exceeds {MAX_FILE_BYTES // (1024 * 1024)} MiB limit")

    ext = Path(filename).suffix.lower() or ".txt"
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"unsupported file extension {ext!r}; supported: {SUPPORTED_EXTENSIONS}")

    meta: dict[str, Any] = {"file_format": ext.lstrip("."), "filename": Path(filename).name}
    if ext in (".md", ".markdown"):
        text = _decode_text(bytes(raw)).strip()
        fmt = "markdown"
    elif ext in (".txt", ".log"):
        text = _decode_text(bytes(raw)).strip()
        fmt = "text"
    elif ext in (".html", ".htm"):
        parser = _MarkdownHTMLParser()
        parser.feed(_decode_text(bytes(raw)))
        parser.close()
        text = parser.get_markdown()
        fmt = "markdown"
    elif ext in (".csv", ".tsv"):
        text, extra = _parse_csv(bytes(raw), delimiter="\t" if ext == ".tsv" else ",")
        meta.update(extra)
        fmt = "markdown"
    elif ext in (".json", ".jsonl"):
        text, extra = _parse_json(bytes(raw), is_jsonl=(ext == ".jsonl"))
        meta.update(extra)
        fmt = "text"
    elif ext == ".docx":
        text, extra = _parse_docx(bytes(raw))
        meta.update(extra)
        fmt = "markdown"
    elif ext == ".pdf":
        text, extra = _parse_pdf(bytes(raw))
        meta.update(extra)
        fmt = "markdown"
    else:
        raise ValueError(f"unsupported file extension {ext!r}")

    if not text or not text.strip():
        raise ValueError(f"no extractable text found in {filename!r}")
    meta["char_count"] = len(text)
    return {"text": text, "format": fmt, "extracted_metadata": meta}
