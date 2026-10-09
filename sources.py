"""One entry point for every input format.

A .txt or .epub is one work; a .zip may be one multi-volume novel or several
separate books, so everything returns a list.
"""

import os

import epub_source
import txt_source
import zip_source

READERS = {".txt": txt_source, ".epub": epub_source}
SUPPORTED = (".txt", ".epub", ".zip")


def is_supported(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in SUPPORTED


def load_any(path, options, name=None, mtime=None) -> list:
    """[(WorkMeta, [ChapterDraft])] for one input file."""
    extension = os.path.splitext(name or path)[1].lower()
    if extension == ".zip":
        return zip_source.load_many(path, options, name=name, mtime=mtime)
    reader = READERS.get(extension)
    if reader is None:
        raise ValueError(f"unsupported file type: {extension or '(none)'}")
    return [reader.load(path, options, name=name, mtime=mtime)]


def load_joined(paths, options) -> list:
    """Several input files read as the volumes of one work, the way a zip of
    volumes is. A file that is itself several works adds each as a volume."""
    volumes = []
    for path in paths:
        loaded = [(m, c) for m, c in load_any(path, options) if c]
        for meta, drafts in loaded:
            # A volume is labelled off its file name; a book inside a zip of
            # several has none of its own, so it goes by its title.
            file = (os.path.basename(path) if len(loaded) == 1
                    else f"{meta.title or os.path.basename(path)}.epub")
            volumes.append({"index": len(volumes), "file": file,
                            "meta": meta, "drafts": drafts})
    if not volumes:
        return []
    return zip_source.join_volumes(volumes, force=True)
