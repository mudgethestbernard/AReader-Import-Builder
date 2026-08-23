"""Chapter-boundary detection shared by the .txt and .epub readers."""

import re
from dataclasses import dataclass, field

from blocks import Block

# Heading shapes seen in Korean web novels and AO3/calibre exports, most
# specific first. A marker only counts on a short line, so body prose that
# happens to start with a number is never mistaken for a heading.
MARKER_FAMILIES: list[tuple[str, re.Pattern]] = [
    ("제N화", re.compile(r"^제\s*\d+\s*[화장회편권]\s*[.:\-–—]?\s*(?P<rest>.{0,40})$")),
    ("N화", re.compile(r"^\d+\s*[화장회편]\s*[.:\-–—]?\s*(?P<rest>.{0,40})$")),
    ("#N", re.compile(r"^#\s*(?P<num>\d+)\s*[.:\-–—]?\s*(?P<rest>.{0,40})$")),
    ("Chapter N", re.compile(r"^(?:chapter|ch\.?)\s*\d+\s*[.:\-–—]?\s*(?P<rest>.{0,40})$", re.I)),
    # The title after the number is optional: plenty of books number their
    # chapters and nothing else.
    ("NN.", re.compile(r"^\d{1,3}\s*[.．]\s*(?P<rest>.{0,40})$")),
    ("서두/외전", re.compile(
        r"^(?:프롤로그|에필로그|서장|종장|외전|번외|후기|작가의\s*말|prologue|epilogue|interlude)"
        r"\s*\d*\s*[.:\-–—]?\s*(?P<rest>.{0,40})$", re.I)),
]

MAX_MARKER_LEN = 60

# Chapters that are packaging, not story.
FRONT_MATTER = re.compile(
    r"^(?:목차|차례|표지|판권|저작권|preface|title\s*page|table\s*of\s*contents|contents|"
    r"cover|copyright|colophon|about\s+the\s+author)\s*$", re.I)


@dataclass
class ChapterDraft:
    title: str
    blocks: list[Block] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(len(b.text) for b in self.blocks)


def match_family(text: str, family: re.Pattern) -> re.Match | None:
    t = text.strip()
    if not t or len(t) > MAX_MARKER_LEN:
        return None
    return family.match(t)


def pick_family(blocks: list[Block], minimum: int = 2) -> tuple[str, re.Pattern] | None:
    """The marker shape this run of blocks actually uses, or None."""
    best = None
    best_hits = 0
    for name, pattern in MARKER_FAMILIES:
        hits = sum(1 for b in blocks if b.kind == "p" and match_family(b.text, pattern))
        if hits > best_hits:
            best, best_hits = (name, pattern), hits
    return best if best_hits >= minimum else None


def split_on_markers(blocks: list[Block], family: re.Pattern) -> list[ChapterDraft]:
    """Split at marker paragraphs, dropping anything before the first one.

    A run of adjacent markers is a table of contents, so only its last entry
    starts a chapter — the preceding ones are the list itself.
    """
    marks = [i for i, b in enumerate(blocks) if b.kind == "p" and match_family(b.text, family)]
    starts: list[int] = []
    for i, idx in enumerate(marks):
        if i + 1 < len(marks) and marks[i + 1] == idx + 1:
            continue  # part of a contents run
        starts.append(idx)
    out = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(blocks)
        out.append(ChapterDraft(tidy_marker_title(blocks[start].text), blocks[start + 1:end]))
    return out


_BARE_NUMBER = re.compile(r"^(\d{1,3})\s*[.．]\s*$")


def tidy_marker_title(text: str) -> str:
    """`1.` names a chapter but reads badly in a list, and the number alone is
    the whole title, so drop the trailing stop. A named title keeps its own."""
    text = text.strip()
    bare = _BARE_NUMBER.match(text)
    return bare.group(1) if bare else text


def split_on_headings(blocks: list[Block]) -> list[ChapterDraft]:
    starts = [i for i, b in enumerate(blocks) if b.kind == "h"]
    out = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(blocks)
        out.append(ChapterDraft(blocks[start].text, blocks[start + 1:end]))
    return out


def fold_subtitle(chapter: ChapterDraft) -> None:
    """A short heading-shaped line at the top of a chapter is its arc or part
    title - a chapter marker followed by a named section heading. Fold it into
    the chapter name instead of leaving it as the first line of the body."""
    if not chapter.blocks:
        return
    first = chapter.blocks[0]
    if first.kind == "h":
        chapter.title = f"{chapter.title} - {first.text}" if chapter.title else first.text
        chapter.blocks.pop(0)
        return
    text = first.text
    if first.kind != "p" or not text or len(text) > 40:
        return
    for _, pattern in MARKER_FAMILIES:
        if match_family(text, pattern):
            chapter.title = f"{chapter.title} - {text}" if chapter.title else text
            chapter.blocks.pop(0)
            return


def drop_front_matter(chapters: list[ChapterDraft]) -> list[ChapterDraft]:
    return [c for c in chapters if not FRONT_MATTER.match(c.title.strip())]


def drop_empty(chapters: list[ChapterDraft], min_chars: int = 1) -> list[ChapterDraft]:
    return [c for c in chapters if c.char_count >= min_chars]


_ECHO_NOISE = re.compile(r"[\s:：.．,，·・\-–—~〜!！?？'\"“”‘’()（）\[\]]+")
ECHO_MAX_BLOCKS = 4
ECHO_MAX_LEN = 40


def _echo_key(text: str) -> str:
    return _ECHO_NOISE.sub("", text)


def strip_title_echo(chapter: ChapterDraft) -> None:
    """Drop the chapter's own title where the page prints it again.

    Books typeset the heading as decoration: an `<h2>` naming the chapter is
    followed by the same words again as centred paragraphs, one per line, while
    the table of contents supplies the title separately. Left in, every chapter
    opens by repeating its own name. Only short leading blocks whose text is
    part of the title are removed, so a chapter that genuinely opens on its own
    name keeps it.
    """
    title_key = _echo_key(chapter.title or "")
    if len(title_key) < 2:
        return
    cut = 0
    for block in chapter.blocks[:ECHO_MAX_BLOCKS]:
        if block.kind == "hr":
            cut += 1
            continue
        text = block.text
        if not text or len(text) > ECHO_MAX_LEN:
            break
        key = _echo_key(text)
        if not key or key not in title_key:
            break
        cut += 1
    # A rule left at the very top has nothing above it to divide.
    while cut < len(chapter.blocks) and chapter.blocks[cut].kind == "hr":
        cut += 1
    if cut:
        del chapter.blocks[:cut]


def strip_title_echoes(chapters: list[ChapterDraft]) -> list[ChapterDraft]:
    for chapter in chapters:
        strip_title_echo(chapter)
    return chapters


def split_long(chapters: list[ChapterDraft], max_chars: int) -> list[ChapterDraft]:
    """Break over-long chapters at paragraph boundaries into numbered parts."""
    if max_chars <= 0:
        return chapters
    out: list[ChapterDraft] = []
    for chapter in chapters:
        if chapter.char_count <= max_chars:
            out.append(chapter)
            continue
        part, size, index = [], 0, 1
        for block in chapter.blocks:
            part.append(block)
            size += len(block.text)
            if size >= max_chars:
                out.append(ChapterDraft(f"{chapter.title} ({index})", part))
                part, size, index = [], 0, index + 1
        if part:
            out.append(ChapterDraft(f"{chapter.title} ({index})", part))
    return out
