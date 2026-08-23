"""Two-step API for the web page: read the books, let the reader correct what
was guessed, then write the archive.

The command line converts in one shot because nothing is there to review. The
page splits that in half so a wrong title, author or chapter split can be fixed
before the zip exists. Parsing is the expensive half, so the drafts stay cached
between the two calls and `build` never re-reads a file.
"""

import json
import os

import archive
import sources

# key -> (WorkMeta, [ChapterDraft]) from the last analyze(). One input file can
# produce several works, so the key is the path plus its index.
_CACHE = {}


class Options:
    """Stands in for the argparse namespace the readers expect."""

    def __init__(self, language="", max_chars=0, keep_front_matter=False,
                 no_images=False, local_ids=False):
        self.language = language
        self.max_chars = max_chars
        self.keep_front_matter = keep_front_matter
        self.no_images = no_images
        self.local_ids = local_ids


def analyze(paths, language="", max_chars=0, keep_front_matter=False,
            no_images=False, local_ids=False) -> str:
    """Read each file and describe what was found, without writing anything."""
    options = Options(language, max_chars, keep_front_matter, no_images, local_ids)
    _CACHE.clear()
    out = []
    for path in paths:
        name = os.path.basename(path)
        try:
            loaded = [(m, c) for m, c in sources.load_any(path, options) if c]
        except Exception as error:
            out.append({"key": path, "file": name,
                        "error": f"{type(error).__name__}: {error}"})
            continue
        if not loaded:
            out.append({"key": path, "file": name, "error": "no chapter text found"})
            continue

        for index, (meta, chapters) in enumerate(loaded):
            if local_ids:
                meta.work_id = None
            key = f"{path}#{index}"
            _CACHE[key] = (meta, chapters)
            out.append({
                "key": key,
                "file": name,
                "title": meta.title or "",
                "author": meta.author or "",
                "complete": bool(meta.complete),
                "language": meta.language or "",
                "summary": meta.summary or "",
                "workId": archive.make_work_id(meta, chapters),
                "ao3Id": meta.work_id if (meta.work_id or "").isdigit() else "",
                "fandoms": list(meta.fandoms),
                "tagCount": len(meta.tags),
                "droppedImages": meta.dropped_images,
                "volumes": list(meta.volumes),
                "chapters": [{"title": c.title or "", "chars": c.char_count}
                             for c in chapters],
            })
    return json.dumps(out, ensure_ascii=False)


def build(payload, out_path, separate=False) -> str:
    """Apply the reader's corrections to the cached drafts and write the zip.

    `payload` mirrors what analyze returned, with edited fields and a `keep`
    flag per chapter. Dropped chapters are removed before numbering, so the
    archive is always a clean 1..n.
    """
    works = []
    skipped = []
    for entry in (json.loads(payload) if isinstance(payload, str) else payload):
        cached = _CACHE.get(entry["key"])
        if cached is None:
            skipped.append(entry.get("file", entry["key"]))
            continue
        meta, drafts = cached

        kept = [d for d, item in zip(drafts, entry["chapters"]) if item.get("keep", True)]
        if not kept:
            skipped.append(entry.get("file", entry["key"]))
            continue
        for draft, item in zip(drafts, entry["chapters"]):
            if item.get("keep", True):
                draft.title = item.get("title") or draft.title

        meta.title = (entry.get("title") or meta.title or "").strip()
        meta.author = (entry.get("author") or "Unknown").strip()
        meta.complete = bool(entry.get("complete"))
        meta.summary = (entry.get("summary") or "").strip()
        if entry.get("language"):
            meta.language = entry["language"].strip()
        # A trimmed selection invalidates counts that came from the source.
        if len(kept) != len(drafts):
            meta.word_count = 0
            meta.chapter_max = 0

        work_id = archive.make_work_id(meta, kept)
        works.append(archive.build_work(meta, kept, work_id))

    if not works:
        return json.dumps({"files": [], "skipped": skipped, "works": 0}, ensure_ascii=False)

    folder = os.path.dirname(out_path) or "."
    os.makedirs(folder, exist_ok=True)
    files = []
    if separate:
        for work in works:
            single = os.path.join(folder, archive.clean_filename(work["title"]) + ".zip")
            archive.write_archive(single, [work])
            files.append(os.path.basename(single))
    else:
        archive.write_archive(out_path, works)
        files.append(os.path.basename(out_path))

    return json.dumps({"files": files, "skipped": skipped, "works": len(works)},
                      ensure_ascii=False)
