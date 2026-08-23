# AReader Import Builder

Turns `.txt` and `.epub` novels into the `.zip` archive the AReader app's
**Imported** tab accepts. A `.zip` of per-volume files is joined into a single
work rather than a dozen unrelated ones.

Three front ends over one converter:

| | |
| --- | --- |
| **Web** | `index.html` — runs in the browser, nothing is uploaded |
| **Window** | `AReader Import Builder.bat` (or `gui.pyw`) — double-click, no terminal |
| **Command line** | `python AReaderImportBuilder <file or folder> -o books.zip` |

Python 3.10+, no third-party packages. The window is stdlib tkinter; the web
page runs the same `.py` files under Pyodide, so the browser and the desktop
cannot drift apart.

Move the finished zip to your phone, open the **Imported** tab, tap **+**.

## What it produces

One `works/<workId>.json` per work, each a `Work.toJson()` object whose
`chapters` array holds `Chapter.toJson()` objects with their bodies. Chapter
bodies are flat `<p>` / `<hr>` runs, which is all `HtmlBlockParser` walks;
`em`/`strong`/`i`/`b`/`br` and friends survive inline, everything else is
unwrapped to text.

## How chapters are found

A book is split by the first of these that the file actually supports:

1. **EPUB `toc.ncx`** — used when its labels are real names. An epubmerge
   anthology writes hashes there, which fails this check.
2. **In-body headings** (`<h1>`…`<h6>`).
3. **A repeated marker paragraph** — `Chapter 12`, `#12`, `01. Title`, a
   prologue or epilogue heading, and the Korean equivalents these files are
   actually named with. The shape with the most hits wins, chosen per directory
   so an anthology's main volumes and its side stories can use different ones.
   A run of adjacent markers is a table of contents, so only its last entry
   starts a chapter — that is what keeps the contents list out of the text.
4. **One chapter per spine document.**

Covers, contents pages, copyright pages and the AO3 preface page are dropped
(`--keep-front-matter` keeps them). A short heading-shaped line at the top of a
chapter is folded into its name, so a chapter marker followed by a named section
heading becomes one title rather than a stray first line.

## Volumes in one zip

A multi-volume novel is usually shared as one zip of per-volume `.epub` files.
Imported a volume at a time it becomes a dozen unrelated works, so the volumes
are joined instead: chapters run straight through in volume order, each labelled
with the volume it came from — `Vol. 1 - Chapter 3`, `Side Story - Afterwards`.

The label comes off the file name, because a volume is usually filed under a
fuller name than the one it carries inside. Numbered volumes sort numerically,
so volume 2 precedes volume 10 however the zip listed them, and side stories,
extras and afterwords sort last. The novel's own name is the part every file
name shares.

Volumes are read from the zip in memory, one at a time — these files run to
several MB each, mostly embedded fonts nobody reads.

If the file names share no common name, the zip is treated as separate books
that merely travelled together, and each becomes its own work.

## Metadata

Richest source wins: an AO3 preface page (rating, warnings, category, fandoms,
characters, relationships, tags, stats and the real work id) beats the EPUB's
OPF metadata, which beats the file name. File names are read for the conventions
shared novels use — a bracketed author at the front, a chapter range that is
packaging rather than title, and a completion note in parentheses at the end. So
`[Someone] A Novel 1-225 (complete).txt` yields the author, the title alone, and
completed status.

A work that carries a real AO3 id keeps it, so importing a work already
downloaded in the app is recognised as a duplicate and skipped. Everything else
gets a `local-<hash>` id derived from title and author only — converting the
same book twice always yields the same id, so a re-import is skipped rather than
added a second time. Use `--local-ids` to ignore an EPUB's AO3 id.

## Reviewing before saving

The web page splits the conversion so nothing is written from a guess.
`api.analyze` reads each book and reports the title, author, completion,
language, summary and the chapter list; the page makes those editable and gives
every chapter a checkbox. `api.build` then applies the corrections to the drafts
still held in memory — it never re-reads a file — and writes the archive.

A book with no summary gets an empty box to write one in; left empty it stays
absent rather than blank.

Unchecked chapters are dropped before numbering, so the archive is always a
clean 1..n. Trimming the selection also clears the word count and the chapter
total that came from the source, since neither describes the archive any more,
and editing the title or author mints a new local work id — the id is derived
from exactly those two fields.

The desktop front ends convert in one shot, because a terminal or a log pane has
nothing to correct.

## Images

An EPUB's own image files cannot reach the app. `ArchiveImporter` writes chapter
HTML and nothing else — only `OfflineDownloadService` fetches images into
`offline_images/`, and the import path never runs it. So an embedded image is
dropped and counted in the report; a `http(s)://` or `//` src is kept, because
that is a URL the reader can load exactly as it does for a downloaded work.

A kept image is sized by the app's own rule (`ChapterContent.kt`): it fills the
text column unless the decoded bitmap is narrower than 40% of that column. A
small image stored at high resolution lands above the threshold and is stretched
to full width. This is the same rule a downloaded AO3 work follows, so imported
works match rather than diverge; the reader ignores an `<img>` tag's width and
height entirely.

In practice a linked image barely exists: calibre downloads remote images into
the epub, which makes them embedded and therefore dropped anyway. So
`--no-images` is a command-line flag only - a toggle for it would sit in the way
without ever changing a result. The review screen still reports how many images
a book had to leave behind.

## Command line options

| flag | effect |
| --- | --- |
| `--dry-run` | print the detected chapters, write nothing |
| `--verbose` | list every chapter, not just the first and last three |
| `--separate` | one `.zip` per work instead of one combined archive |
| `--no-images` | drop every image, including ones linked by URL |
| `--language NAME` | override the language on the work card |
| `--keep-front-matter` | keep cover / contents / copyright pages as chapters |
| `--local-ids` | mint a local id even when the EPUB carries an AO3 one |
| `--max-chars N` | break any chapter longer than N characters into numbered parts |

Two of these are command-line only, deliberately. `--max-chars` cuts mid-scene at
an arbitrary count, and `--no-images` controls something that hardly ever occurs
(see *Images*). Neither earns a place in the web page or the window, where the
review screen already shows chapter sizes and dropped-image counts.

## Files

| | |
| --- | --- |
| `index.html`, `css/`, `js/` | the web page |
| `api.py` | analyze / build, the two halves the web page needs |
| `AReader Import Builder.bat`, `gui.pyw` | the window |
| `__main__.py`, `cli.py` | the command line |
| `sources.py` | one entry point over the three input formats |
| `txt_source.py`, `epub_source.py`, `zip_source.py` | reading each format |
| `chapters.py`, `blocks.py` | chapter splitting, HTML to reader blocks |
| `metadata.py`, `archive.py` | metadata, and writing the zip |

## Publishing the web version

The page is static and needs no build step. Push this folder to a GitHub repo
and turn on Pages (Settings → Pages → Deploy from a branch → `main` / `/root`).

The `.py` files must stay next to `index.html`: the page fetches them at runtime
and runs them under Pyodide.

Locally it needs a server, because the page fetches those modules:

```bash
python -m http.server 8000
```

Then open `http://localhost:8000`.

Pyodide itself (about 10 MB) comes from the jsDelivr CDN on first load and is
then cached by the browser. Nothing else leaves the page — files are read into
Pyodide's in-memory filesystem and the finished zip is handed back as a blob
download.

One browser-specific detail: a file written into that virtual filesystem is
stamped with the current time, and a book carrying no date of its own falls back
to its file date for `publishedDate`. The page restores the real date from the
upload's `lastModified`, which is what makes web output byte-identical to the
desktop output.
