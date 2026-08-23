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
