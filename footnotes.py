"""EPUB footnotes -> per-chapter notes.

A typeset EPUB numbers its footnotes straight through the volume and keeps
them on a separate notes page (or at the end of the chapter), each reference a
link to its note and each note a link back. Imported as-is, the notes page would
become a chapter of its own and every reference a dead number.

`link` runs once per book, before chapters are split. It finds each reference,
moves the note's text onto the block that references it, and marks the note's
original paragraph so it is never written. `render` runs per chapter when the
archive is built: it numbers that chapter's notes from 1 and lists them after a
rule at the end, the reference and the note linked to each other by anchor, so
tapping either jumps to the other.
"""

import html as _html
import posixpath
import re
from urllib.parse import unquote

from blocks import Block

_LINK = re.compile(r'<a href="([^"]*)"( data-noteref="")?>(.*?)</a>', re.S)
_ANY_LINK = re.compile(r"</?a\b[^>]*>")
_TAG = re.compile(r"<[^>]*>")
# What a footnote reference looks like on the page: `1)`, `[2]`, `(3)`, `*`.
_LABEL = re.compile(r"^[\[(]?\s*(?:\d{1,3}|[*†‡※]+)\s*[\])]?\.?$")
# The same label at the start of a note, which `render` replaces with its own.
_LEAD = re.compile(r"^\s*[\[(]?\s*(?:\d{1,3}|[*†‡※]+)\s*[\])]?[.:]?\s*")
# A tag no source uses, so the link clean-up below cannot touch it.
_PLACEHOLDER = re.compile(r'<sup>\s*<fnref k="(\d+)"></fnref>\s*</sup>|<fnref k="(\d+)"></fnref>')


def _plain(inline: str) -> str:
    return _html.unescape(_TAG.sub("", inline)).strip()


def _target(docs_by_href, from_href: str, href: str):
    """(doc, block index) a link points at, or None."""
    path, _, fragment = _html.unescape(href).partition("#")
    if not fragment:
        return None
    path = unquote(path)
    target = (posixpath.normpath(posixpath.join(posixpath.dirname(from_href), path))
              if path else from_href)
    doc = docs_by_href.get(target)
    if doc is None:
        return None
    index = doc.anchors.get(unquote(fragment))
    if index is None or index >= len(doc.blocks):
        return None
    return doc, index


def _note_text(inline: str) -> str:
    """A note's own paragraph, minus its back-link and the number it opened on."""
    return _LEAD.sub("", _ANY_LINK.sub("", inline), count=1).strip()


def link(docs) -> int:
    """Turn footnote references in `docs` into placeholders carrying their note.
    Every other link is unwrapped to its text. Returns how many notes were found."""
    by_href = {d.href: d for d in docs}

    # Pass 1 decides, without touching anything: a note's own back-link must
    # still be in place while the reference pointing at it is judged.
    keys: dict = {}          # (note doc href, block index) -> key
    refs: dict = {}          # (doc href, block index, link ordinal) -> key
    for doc in docs:
        for bi, block in enumerate(doc.blocks):
            if block.kind != "p" or "<a " not in block.inline:
                continue
            opening = _plain(block.inline)
            for ordinal, match in enumerate(_LINK.finditer(block.inline)):
                href, flagged, label = match.group(1), bool(match.group(2)), _plain(match.group(3))
                if not (flagged or _LABEL.match(label)):
                    continue
                # A paragraph that opens on this label is a note pointing back
                # at its reference, not a reference.
                if not flagged and opening.startswith(label):
                    continue
                hit = _target(by_href, doc.href, href)
                if hit is None:
                    continue
                note_doc, ni = hit
                if (note_doc is doc and ni == bi) or note_doc.blocks[ni].kind != "p":
                    continue
                if not flagged and not _LEAD.match(_plain(note_doc.blocks[ni].inline) or " "):
                    continue        # the target does not open like a note
                key = keys.setdefault((note_doc.href, ni), len(keys) + 1)
                refs[(doc.href, bi, ordinal)] = key

    texts = {key: _note_text(by_href[h].blocks[i].inline) for (h, i), key in keys.items()}

    # Pass 2 rewrites.
    for doc in docs:
        for bi, block in enumerate(doc.blocks):
            if "<a" not in block.inline:
                continue
            counter = iter(range(10 ** 6))

            def swap(match, doc=doc, bi=bi, block=block, counter=counter):
                key = refs.get((doc.href, bi, next(counter)))
                if key is None:
                    return match.group(3)
                block.notes[key] = texts[key]
                return f'<fnref k="{key}"></fnref>'

            block.inline = _ANY_LINK.sub("", _LINK.sub(swap, block.inline))
    for (h, i) in keys:
        by_href[h].blocks[i].kind = "note"
    return len(keys)


def render(blocks: "list[Block]", chapter_number: int) -> "list[Block]":
    """This chapter's blocks with its notes numbered from 1 and listed at the
    end. The draft's own blocks are left as they are."""
    order: dict = {}
    notes: dict = {}
    out: "list[Block]" = []
    for block in blocks:
        if block.kind == "note":
            continue
        if "<fnref " not in block.inline:
            out.append(block)
            continue
        notes.update(block.notes)

        def number(match):
            key = int(match.group(1) or match.group(2))
            first = key not in order
            n = order.setdefault(key, len(order) + 1)
            name = f' name="ref{chapter_number}-{n}"' if first else ""
            return f'<sup><a{name} href="#fn{chapter_number}-{n}">{n})</a></sup>'

        out.append(Block(block.kind, _PLACEHOLDER.sub(number, block.inline),
                         block.level, block.align))
    if order:
        out.append(Block("hr"))
        for key, n in sorted(order.items(), key=lambda kv: kv[1]):
            out.append(Block("p", f'<small><a name="fn{chapter_number}-{n}" '
                                  f'href="#ref{chapter_number}-{n}">{n})</a> '
                                  f'{notes.get(key, "")}</small>'))
    return out
