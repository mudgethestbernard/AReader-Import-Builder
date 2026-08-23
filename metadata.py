"""Work metadata: filename conventions, EPUB package metadata, AO3 preface."""

import html as _html
import re
from dataclasses import dataclass, field
from datetime import date

RATINGS = {
    "general audiences": "general", "general": "general", "g": "general",
    "teen and up audiences": "teen", "teen": "teen", "t": "teen",
    "mature": "mature", "m": "mature",
    "explicit": "explicit", "e": "explicit",
    "not rated": "none",
}

CATEGORIES = {
    "f/f": "ff", "f/m": "fm", "m/m": "mm", "gen": "gen", "multi": "multi", "other": "other",
}

LANGUAGES = {
    "ko": "한국어", "kor": "한국어", "korean": "한국어",
    "en": "English", "eng": "English", "english": "English",
    "ja": "日本語", "jpn": "日本語", "japanese": "日本語",
    "zh": "中文-普通话 國語", "chinese": "中文-普通话 國語",
}

# A calibre/epubmerge anthology writes hashes where a title belongs.
_HASHLIKE = re.compile(r"[0-9a-f]{16,}", re.I)
_HANGUL = re.compile(r"[가-힣]")

COMPLETE_WORDS = ("완결", "완료", "完", "완", "complete", "completed", "end", "fin")
ONGOING_WORDS = ("미완", "연재중", "연재", "ongoing", "wip", "incomplete")


@dataclass
class WorkMeta:
    title: str = ""
    author: str = "Unknown"
    work_id: str | None = None       # a real AO3 id, when the source carries one
    summary: str = ""
    language: str = ""
    fandoms: list[str] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    rating: str = "none"
    warning: str = "none"
    category: str = "none"
    complete: bool | None = None
    word_count: int = 0
    chapter_max: int = 0
    published: str | None = None
    # Images embedded in the EPUB, which the app has no way to store.
    dropped_images: int = 0
    # Volume labels, when this work was joined from a zip of volumes.
    volumes: list = field(default_factory=list)

    def merge_missing(self, other: "WorkMeta") -> None:
        """Fill blanks from a weaker source; never overwrite what we have."""
        for name in ("title", "summary", "language", "published", "work_id"):
            if not getattr(self, name) and getattr(other, name):
                setattr(self, name, getattr(other, name))
        if self.author in ("", "Unknown") and other.author not in ("", "Unknown"):
            self.author = other.author
        for name in ("fandoms", "characters", "relationships", "tags"):
            if not getattr(self, name) and getattr(other, name):
                setattr(self, name, list(getattr(other, name)))
        for name in ("rating", "warning", "category"):
            if getattr(self, name) == "none" and getattr(other, name) != "none":
                setattr(self, name, getattr(other, name))
        for name in ("word_count", "chapter_max"):
            if not getattr(self, name) and getattr(other, name):
                setattr(self, name, getattr(other, name))
        if self.complete is None:
            self.complete = other.complete


def looks_like_hash(value: str) -> bool:
    return bool(value) and bool(_HASHLIKE.search(value))


def normalize_language(value: str, sample_text: str = "") -> str:
    key = (value or "").strip().lower()
    if key in LANGUAGES:
        return LANGUAGES[key]
    if key and not re.fullmatch(r"[a-z]{2,3}(-[a-z]{2,4})?", key):
        return value.strip()          # already a display name, e.g. "English"
    if sample_text:
        return "한국어" if _HANGUL.search(sample_text) else "English"
    return value.strip()


def normalize_warning(value: str) -> str:
    v = value.strip().lower()
    if not v:
        return "none"
    if v.startswith("no archive warnings"):
        return "none"
    if "chose not to use" in v:
        return "unspecified"
    return "warning"


# ---------------------------------------------------------------- filename

def parse_filename(stem: str) -> WorkMeta:
    """Read the conventions a shared novel file uses: a bracketed author, a
    trailing status note, a chapter range that is packaging not title."""
    meta = WorkMeta()
    name = stem.strip()

    lead = re.match(r"^[\[\(【]\s*([^\]\)】]+)\s*[\]\)】]\s*(.*)$", name)
    if lead:
        meta.author = lead.group(1).strip()
        name = lead.group(2).strip()

    complete: bool | None = None

    def _strip_note(match: re.Match) -> str:
        nonlocal complete
        inner = match.group(1).strip().lower()
        if any(w in inner for w in COMPLETE_WORDS):
            complete = True
        elif any(w in inner for w in ONGOING_WORDS):
            complete = False
        else:
            return match.group(0)      # not a status note; keep it in the title
        return " "

    name = re.sub(r"[\(（\[]([^)）\]]{1,20})[\)）\]]", _strip_note, name)
    # A chapter range is packaging; a volume number is part of the title.
    name = re.sub(r"\s*\d+\s*[-~–]\s*\d+\s*[화장회편]\s*", " ", name)
    meta.title = re.sub(r"\s{2,}", " ", name).strip(" -_·")
    meta.complete = complete
    return meta


# ------------------------------------------------------------- AO3 preface

_DL_RE = re.compile(r"<dl[^>]*class=[\"'][^\"']*tags[^\"']*[\"'][^>]*>(.*?)</dl>", re.I | re.S)
_PAIR_RE = re.compile(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]*>")
_WORK_URL_RE = re.compile(r"archiveofourown\.org/works/(\d+)")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _list(fragment: str) -> list[str]:
    items = re.findall(r"<a[^>]*>(.*?)</a>", fragment, re.I | re.S)
    if not items:
        items = [p for p in re.split(r",", _text(fragment)) if p.strip()]
    return [v for v in (_text(i) for i in items) if v]


def parse_ao3_preface(html: str) -> "WorkMeta | None":
    """Metadata from the preface page a calibre AO3 download writes."""
    block = _DL_RE.search(html)
    if not block or "archiveofourown.org" not in html:
        return None
    meta = WorkMeta()
    work_url = _WORK_URL_RE.search(html)
    if work_url:
        meta.work_id = work_url.group(1)

    for raw_key, raw_value in _PAIR_RE.findall(block.group(1)):
        key = _text(raw_key).rstrip(":").lower()
        if key == "rating":
            meta.rating = RATINGS.get(_text(raw_value).lower(), "none")
        elif key.startswith("archive warning"):
            meta.warning = normalize_warning(_text(raw_value))
        elif key == "category":
            first = (_list(raw_value) or [""])[0]
            meta.category = CATEGORIES.get(first.lower(), "none")
        elif key == "fandom":
            meta.fandoms = _list(raw_value)
        elif key == "relationship":
            meta.relationships = _list(raw_value)
        elif key == "character":
            meta.characters = _list(raw_value)
        elif key.startswith("additional tag"):
            meta.tags = _list(raw_value)
        elif key == "language":
            meta.language = normalize_language(_text(raw_value))
        elif key == "stats":
            stats = _text(raw_value)
            published = re.search(r"Published:\s*([\d-]+)", stats)
            if published:
                meta.published = published.group(1)
            words = re.search(r"Words:\s*([\d,]+)", stats)
            if words:
                meta.word_count = int(words.group(1).replace(",", ""))
            chapters = re.search(r"Chapters:\s*(\d+)\s*/\s*(\d+|\?)", stats)
            if chapters:
                total = chapters.group(2)
                meta.chapter_max = int(total) if total.isdigit() else 0
                meta.complete = total.isdigit() and total == chapters.group(1)
            if "Completed:" in stats:
                meta.complete = True

    # `<b[^>]*>` would also match `<body>`; the work title is a real <b>.
    title = re.search(r"<b(?:\s[^>]*)?>(.*?)</b>", html, re.I | re.S)
    if title:
        meta.title = _text(title.group(1))
    return meta


# ------------------------------------------------------------------- OPF

def parse_opf_meta(dc: "dict[str, list[str]]", subjects: "list[str]") -> WorkMeta:
    meta = WorkMeta()
    title = (dc.get("title") or [""])[0].strip()
    if title and not looks_like_hash(title):
        meta.title = title
    creator = (dc.get("creator") or [""])[0].strip()
    if creator and creator.lower() != "unknown" and not looks_like_hash(creator):
        meta.author = creator
    description = (dc.get("description") or [""])[0].strip()
    if description and not looks_like_hash(description):
        meta.summary = _html.unescape(description).strip()
    language = (dc.get("language") or [""])[0].strip()
    if language:
        meta.language = normalize_language(language)

    # calibre writes AO3 rating/category/warning into dc:subject beside the real
    # tags, so lift those out and keep the remainder as tags.
    leftover = []
    for subject in subjects:
        low = subject.strip().lower()
        if low in RATINGS and meta.rating == "none":
            meta.rating = RATINGS[low]
        elif low in CATEGORIES and meta.category == "none":
            meta.category = CATEGORIES[low]
        elif "archive warning" in low or "chose not to use" in low:
            meta.warning = normalize_warning(subject)
        else:
            leftover.append(subject.strip())
    meta.tags = leftover

    for value in dc.get("date", []):
        found = re.search(r"(\d{4}-\d{2}-\d{2})", value)
        if found:
            meta.published = found.group(1)
            break
    return meta


def common_base_title(titles) -> str:
    """The part every volume title shares - the novel's own name."""
    if not titles:
        return ""
    import os
    base = os.path.commonprefix(list(titles))
    # Volume 1 and volume 10 share the digit, so the common prefix can swallow
    # part of the number; trim it back off.
    return base.rstrip(" 	-_·:()[]（）0123456789").strip()


def volume_label(title: str, base: str, fallback: str = "") -> str:
    """What is left of a volume's title once the novel's name is removed."""
    label = title[len(base):] if base and title.startswith(base) else title
    label = label.strip(" 	-_·:()[]（）")
    return label or fallback


def today() -> str:
    return date.today().isoformat()
