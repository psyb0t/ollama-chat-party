"""
File readers for various document formats.
Supports: TXT, MD, HTML, PDF, DOCX, ODT
"""

import re
from pathlib import Path
from typing import List

from rich.console import Console

# Optional imports with graceful fallbacks
try:
    from bs4 import BeautifulSoup

    HAS_BS4 = True
except ImportError:
    BeautifulSoup = None  # type: ignore
    HAS_BS4 = False

try:
    import PyPDF2

    HAS_PYPDF2 = True
except ImportError:
    PyPDF2 = None  # type: ignore
    HAS_PYPDF2 = False

try:
    from docx import Document  # type: ignore

    HAS_DOCX = True
except ImportError:
    Document = None  # type: ignore
    HAS_DOCX = False

try:
    from odf import teletype  # type: ignore
    from odf import text as odf_text  # type: ignore
    from odf.opendocument import load as load_odt  # type: ignore

    HAS_ODF = True
except ImportError:
    load_odt = None  # type: ignore
    odf_text = None  # type: ignore
    teletype = None  # type: ignore
    HAS_ODF = False

console = Console()

# Regex for cleaning markdown formatting
_md_strip = re.compile(r"(!?\[.*?\]\(.*?\))|(```.*?```)|(`#.*)|[*_>`~-]")


def read_txt(p: Path) -> str:
    """Read plain text files"""
    return p.read_text(errors="ignore")


def read_md(p: Path) -> str:
    """Read markdown files with basic formatting removal"""
    return _md_strip.sub(" ", read_txt(p))


def read_html(p: Path) -> str:
    """Read HTML files and extract text content"""
    if not HAS_BS4:
        return ""
    return BeautifulSoup(read_txt(p), "lxml").get_text("\n")  # type: ignore


def read_pdf(p: Path) -> str:
    """Read PDF files and extract text content"""
    if not HAS_PYPDF2:
        return ""
    try:
        return "\n".join(
            pg.extract_text() or ""
            for pg in PyPDF2.PdfReader(p.open("rb")).pages  # type: ignore
        )
    except Exception as e:
        console.print(
            f"⚠️  [yellow]PDF parsing failed for[/] [bold red]{p.name}[/]: [dim]{e}[/]"
        )
        return ""


def read_docx(p: Path) -> str:
    """Read DOCX files and extract text content"""
    if not HAS_DOCX:
        return ""
    try:
        doc = Document(p)  # type: ignore
        return "\n".join(paragraph.text for paragraph in doc.paragraphs)  # type: ignore
    except Exception as e:
        console.print(
            f"⚠️  [yellow]DOCX parsing failed for[/] [bold red]{p.name}[/]: [dim]{e}[/]"
        )
        return ""


def read_odt(p: Path) -> str:
    """Read ODT files and extract text content"""
    if not HAS_ODF:
        return ""
    try:
        doc = load_odt(p)  # type: ignore
        text_content: List[str] = []
        for element in doc.getElementsByType(odf_text.P):  # type: ignore
            text_content.append(teletype.extractText(element))  # type: ignore
        return "\n".join(text_content)
    except Exception as e:
        console.print(
            f"⚠️  [yellow]ODT parsing failed for[/] [bold red]{p.name}[/]: [dim]{e}[/]"
        )
        return ""


# File extension to reader function mapping
READERS = {
    ".txt": read_txt,
    ".md": read_md,
    ".markdown": read_md,
    ".htm": read_html,
    ".html": read_html,
    ".pdf": read_pdf,
    ".docx": read_docx,
    ".odt": read_odt,
}
