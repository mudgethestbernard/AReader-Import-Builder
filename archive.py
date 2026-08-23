"""Writes the zip ArchiveImporter reads.

The importer walks `works/*.json` only: each file is one `Work.toJson()` object
with a `chapters` array of `Chapter.toJson()` objects, bodies included. Key
names here have to match those two serializers exactly.
"""

import hashlib
import json
import os
import re
import zipfile

import blocks as B
import metadata as M

# Characters Windows will not accept in a file name.
_BAD_FILENAME_CHARS = re.compile('[' + chr(92) + '/:*?"<>|]')


def clean_filename(name: str) -> str:
    name = _BAD_FILENAME_CHARS.sub("", name or "").strip().strip(".")
    return re.sub(r"\s{2,}", " ", name)[:80].strip()


def suggest_zip_name(paths) -> str:
    """Name the archive after the first work. The title comes off the filename,
    so nothing has to be opened to answer this. Shared by the window and the web
    page so both suggest the same thing."""
    if not paths:
        return "areader-import"
    stem = os.path.splitext(os.path.basename(paths[0]))[0]
    first = clean_filename(M.parse_filename(stem).title) or "areader-import"
    return first if len(paths) == 1 else f"{first} and {len(paths) - 1} more"


def make_work_id(meta, chapters) -> str:
    """A real AO3 id passes through the importer untouched and dedupes against a
    copy already downloaded. Everything else gets a stable synthetic id: it must
    not be all digits, or ImportedIdStore would read it as an AO3 id."""
    if meta.work_id and meta.work_id.isdigit():
        return meta.work_id
    # Title and author only, so converting the same book twice - with different
    # options, or after a change here - keeps one id and the second import is
    # recognised as a duplicate instead of landing as a second copy.
    seed = "|".join([(meta.title or "").strip(), (meta.author or "").strip()])
    return "local-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def count_words(chapters) -> int:
    total = 0
    for chapter in chapters:
        for block in chapter.blocks:
            total += len(block.text.split())
    return total


def build_work(meta, chapters, work_id: str) -> dict:
    complete = bool(meta.complete)
    chapter_max = meta.chapter_max or (len(chapters) if complete else 0)
    return {
        "workId": work_id,
        "title": meta.title or "Untitled",
        "author": meta.author or "Unknown",
        "authorUrl": None,
        "fandoms": list(meta.fandoms),
        "characters": list(meta.characters),
        "relationships": list(meta.relationships),
        "tags": list(meta.tags),
        "classification": {
            "contentRating": meta.rating,
            "relationship": meta.category,
            "warning": meta.warning,
            "status": "completed" if complete else "incomplete",
        },
        "summary": meta.summary or "",
        "language": meta.language or "",
        "wordCount": meta.word_count or count_words(chapters),
        "chapterCount": len(chapters),
        "chapterMax": chapter_max,
        "hits": 0,
        "kudos": 0,
        "bookmarks": 0,
        "publishedDate": meta.published,
        "isFichubSource": False,
        "isFanfictionNet": False,
        "isImported": True,
        "chapters": [
            {
                "title": chapter.title or f"Chapter {i + 1}",
                "url": "",
                "date": meta.published,
                "number": i + 1,
                "content": B.blocks_to_html(chapter.blocks),
                "chapterNotes": "",
                "isFichubSource": False,
            }
            for i, chapter in enumerate(chapters)
        ],
    }


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", value).strip("._")
    return cleaned[:60] or "work"


def write_archive(out_path: str, works: list) -> None:
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for work in works:
            payload = json.dumps(work, ensure_ascii=False, separators=(",", ":"))
            zf.writestr(f"works/{safe_name(work['workId'])}.json", payload)
