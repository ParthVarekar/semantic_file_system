"""Chunking, filename tokenization, and lightweight keyword extraction."""

from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter

_WS = re.compile(r"[ \t\r\f\v]+")
_PARA = re.compile(r"\n\s*\n")
_SENT = re.compile(r"(?<=[.!?])\s+")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")

# Same token characters as the FTS5 tokenizer (db.FTS_TOKENIZE): letters, numbers, private-use
# and combining marks, so Devanagari vowel signs and viramas stay inside their word.
_TOKEN_CATS = ("L", "N", "M")


def words(text: str) -> list[str]:
    """Lower-cased words exactly as the full-text index splits them."""
    out: list[str] = []
    cur: list[str] = []
    for ch in unicodedata.normalize("NFC", text.lower()):
        cat = unicodedata.category(ch)
        if cat[0] in _TOKEN_CATS or cat == "Co":
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out

STOPWORDS = frozenset(
    """a about above after again against all am an and any are as at be because been before being
    below between both but by can could did do does doing down during each few for from further had
    has have having he her here hers him his how i if in into is it its itself just me more most my
    no nor not now of off on once only or other our out over own same she should so some such than
    that the their them then there these they this those through to too under until up very was we
    were what when where which while who whom why will with you your yours also may new one two use
    used using page pages file files copy final version doc docx pdf txt img image jpg png www http
    https com org html null true false none self return def import class var let const function""".split()
)


def clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = _WS.sub(" ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_text(text: str, size: int = 1200, overlap: int = 150, max_chunks: int = 48) -> list[str]:
    """Paragraph-aware chunks of roughly ``size`` characters with a small overlap."""
    text = clean(text)
    if not text:
        return []
    if len(text) <= size:
        return [text]
    pieces: list[str] = []
    for para in _PARA.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= size:
            pieces.append(para)
            continue
        for sent in _SENT.split(para):
            while len(sent) > size:
                pieces.append(sent[:size])
                sent = sent[size - overlap:]
            if sent:
                pieces.append(sent)
    chunks: list[str] = []
    cur = ""
    for p in pieces:
        if cur and len(cur) + len(p) + 1 > size:
            chunks.append(cur)
            if len(chunks) >= max_chunks:
                return chunks
            tail = cur[-overlap:] if overlap else ""
            cur = (tail + " " + p).strip() if tail else p
        else:
            cur = f"{cur}\n{p}" if cur else p
    if cur and len(chunks) < max_chunks:
        chunks.append(cur)
    return chunks


def name_tokens(name: str) -> str:
    """'Linear_AlgebraHW3-final.pdf' -> 'Linear Algebra HW 3 final pdf' for FTS and descriptors."""
    base, ext = os.path.splitext(name)
    s = re.sub(r"[_\-.+()\[\]{}]+", " ", base)
    s = _CAMEL.sub(" ", s)
    return f"{_WS.sub(' ', s).strip()} {ext.lstrip('.')}".strip()


def folder_tokens(path: str, depth: int = 4) -> str:
    parts = [p for p in os.path.normpath(os.path.dirname(path)).split(os.sep) if p and not p.endswith(":")]
    return " ".join(name_tokens(p) for p in parts[-depth:])


def keywords(text: str, top: int = 12) -> list[str]:
    counts = Counter(w for w in words(text) if w not in STOPWORDS and not w.isdigit() and len(w) > 2)
    return [w for w, _ in counts.most_common(top)]


KIND_LABEL = {
    "document": "document", "code": "source code", "data": "data/config file", "image": "image",
    "audio": "audio recording", "video": "video", "archive": "compressed archive",
    "installer": "software installer", "temporary": "temporary file", "other": "file",
}


def descriptor(row) -> str:
    """Short natural-language description of a file from metadata, embedded for every file so
    that files without extractable text (images, installers, archives) are still searchable."""
    kind = row["kind"] or "other"
    label = KIND_LABEL.get(kind, "file")
    if kind == "image" and row["is_screenshot"]:
        label = "screenshot image"
    elif kind == "image" and row["camera"]:
        label = f"photo taken with {row['camera']}"
    parts = [name_tokens(row["name"]), f"({label})"]
    if row["title"]:
        parts.insert(0, row["title"])
    folders = folder_tokens(row["path"], 3)
    if folders:
        parts.append(f"in folder {folders}")
    if row["exif_time"]:
        parts.append(f"taken {row['exif_time'][:10].replace(':', '-')}")
    return " ".join(parts)
