"""Plain-text novels: one file, chapters marked by a repeated heading line."""

import os
from datetime import datetime

import blocks as B
import chapters as C
import metadata as M

ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr", "utf-16", "latin-1")


def decode(raw: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            return M.nfc(raw.decode(encoding))
        except UnicodeDecodeError:
            continue
    return M.nfc(raw.decode("utf-8", errors="replace"))


def read_text(path: str) -> str:
    return decode(open(path, "rb").read())


def load(source, options, name=None, mtime=None) -> "tuple[M.WorkMeta, list[C.ChapterDraft]]":
    """`source` is a path, or the file's bytes together with its `name` - a
    volume inside a zip is read straight from memory."""
    if isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
        label = name or "untitled.txt"
    else:
        raw = open(source, "rb").read()
        label = name or source
        if mtime is None:
            mtime = os.path.getmtime(source)

    text = decode(raw).replace("\r\n", "\n").replace("\r", "\n")
    body = B.parse_text_lines(text.split("\n"))

    meta = M.parse_filename(os.path.splitext(os.path.basename(label))[0])
    sample = " ".join(b.text for b in body[:40])
    meta.language = M.normalize_language(options.language or "", sample)
    if not meta.published:
        meta.published = date_from(mtime)

    family = C.pick_family(body)
    if family is None:
        drafts = [C.ChapterDraft(meta.title or "Chapter 1", body)]
    else:
        drafts = C.split_on_markers(body, family[1])
        for draft in drafts:
            C.fold_subtitle(draft)

    drafts = C.strip_title_echoes(drafts)
    drafts = C.drop_front_matter(drafts)
    drafts = C.drop_empty(drafts)
    drafts = C.split_long(drafts, options.max_chars)
    return meta, drafts


def date_from(mtime) -> str:
    """A book with no date of its own is dated by its file."""
    try:
        return datetime.fromtimestamp(mtime).date().isoformat()
    except (TypeError, ValueError, OSError):
        return datetime.now().date().isoformat()
