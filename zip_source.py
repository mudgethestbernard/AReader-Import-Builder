"""A zip of volumes read as one work.

A multi-volume novel is usually shared as one zip of per-volume .epub files.
Imported a volume at a time it becomes a dozen unrelated works, so the volumes
are joined here instead: chapters run straight through in volume order and each
one is labelled with the volume it came from ("Vol. 2 - Chapter 7").

Volumes are read from the zip in memory, one at a time, because these files run
to several MB each - mostly embedded fonts nobody reads.
"""

import os
import re
import zipfile

import chapters as C
import epub_source
import metadata as M
import txt_source

READERS = {".txt": txt_source, ".epub": epub_source}

# Anything the OS or the archiver left behind rather than a book.
JUNK = re.compile(r"(^|/)(__MACOSX/|\.|~)")

_NUMBER = re.compile(r"(\d+)")
# Extras belong after the numbered volumes, whatever order the zip listed them
# in. The Korean words are what these files are actually named.
_EXTRA = re.compile(r"외전|번외|후기|단편|부록|특별|side\s*story|extra", re.I)


def _entries(zf: zipfile.ZipFile) -> list:
    out = []
    for info in zf.infolist():
        name = info.filename
        if info.is_dir() or JUNK.search(name):
            continue
        if os.path.splitext(name)[1].lower() in READERS:
            out.append(info)
    return out


def _base_title(titles: "list[str]") -> str:
    """The part every volume title shares - the novel's own name."""
    if not titles:
        return ""
    base = os.path.commonprefix(titles)
    # Volume 1 and volume 10 share the digit, so the common prefix can swallow
    # part of the number; trim it back off.
    return base.rstrip(" \t-_·:()[]（）0123456789").strip()


def _volume_label(title: str, base: str, fallback: str) -> str:
    label = title[len(base):] if base and title.startswith(base) else title
    label = label.strip(" \t-_·:()[]（）")
    return label or fallback


def _order(label: str, index: int) -> tuple:
    """Numbered volumes first in numeric order, then the extras, both stable."""
    if _EXTRA.search(label):
        return (2, index, 0)
    found = _NUMBER.search(label)
    if found:
        return (1, int(found.group(1)), index)
    return (0, index, 0)


def load_many(path, options, name=None, mtime=None) -> list:
    """[(meta, chapters)] - one entry when the zip holds a single novel's
    volumes, one per book when it holds unrelated books."""
    if isinstance(path, (bytes, bytearray)):
        import io as _io
        handle, label = _io.BytesIO(bytes(path)), (name or "archive.zip")
    else:
        handle, label = path, (name or path)
        if mtime is None:
            mtime = os.path.getmtime(path)

    with zipfile.ZipFile(handle) as zf:
        infos = _entries(zf)
        if not infos:
            raise ValueError("no .txt or .epub inside the zip")

        volumes = []
        for index, info in enumerate(infos):
            inner = os.path.basename(info.filename)
            reader = READERS[os.path.splitext(inner)[1].lower()]
            try:
                meta, drafts = reader.load(zf.read(info), options, name=inner, mtime=mtime)
            except Exception as error:
                raise ValueError(f"{inner}: {type(error).__name__}: {error}") from error
            if drafts:
                volumes.append({"index": index, "file": inner, "meta": meta, "drafts": drafts})

    if not volumes:
        raise ValueError("no chapter text in the zip")
    if len(volumes) == 1:
        return [(volumes[0]["meta"], volumes[0]["drafts"])]

    # Label from the file name, not the book's own title: a volume is usually
    # filed under a fuller name than the one its OPF carries, and the file name
    # is the one the reader recognises.
    for volume in volumes:
        stem = os.path.splitext(volume["file"])[0]
        volume["name_title"] = M.parse_filename(stem).title or stem

    titles = [v["name_title"] for v in volumes]
    base = _base_title(titles)
    if len(base) < 2:                       # file names disagree; try the books
        titles = [v["meta"].title or v["file"] for v in volumes]
        base = _base_title(titles)
        for volume, title in zip(volumes, titles):
            volume["name_title"] = title
    # Without a shared name these are separate books that merely travelled
    # together, and joining them would invent a work that does not exist.
    if len(base) < 2 or not all(t.startswith(base) for t in titles):
        return [(v["meta"], v["drafts"]) for v in volumes]

    for volume in volumes:
        volume["label"] = _volume_label(volume["name_title"], base,
                                        os.path.splitext(volume["file"])[0])
    volumes.sort(key=lambda v: _order(v["label"], v["index"]))

    merged: "list[C.ChapterDraft]" = []
    for volume in volumes:
        for draft in volume["drafts"]:
            draft.title = f"{volume['label']} - {draft.title}" if draft.title else volume["label"]
            merged.append(draft)

    meta = _merge_meta([v["meta"] for v in volumes], base,
                       M.parse_filename(os.path.splitext(os.path.basename(label))[0]))
    meta.volumes = [v["label"] for v in volumes]
    return [(meta, merged)]


def _merge_meta(metas: "list[M.WorkMeta]", base: str, outer: M.WorkMeta) -> M.WorkMeta:
    """One work's metadata out of many volumes' worth."""
    meta = M.WorkMeta()
    meta.title = base or outer.title
    for source in metas:
        meta.merge_missing(source)
    meta.merge_missing(outer)
    meta.title = base or meta.title
    # A set is finished when any volume says so - it is the last one that carries
    # the marker. The counts are per volume and describe nothing once joined.
    meta.complete = any(bool(m.complete) for m in metas) or bool(outer.complete)
    meta.word_count = 0
    meta.chapter_max = 0
    meta.dropped_images = sum(m.dropped_images for m in metas)
    # A volume's own id belongs to that volume, never to the joined work.
    meta.work_id = None
    return meta
