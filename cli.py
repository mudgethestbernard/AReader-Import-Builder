"""Command line front end. `gui.pyw` calls straight into `main`."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# A Windows console is often cp949/cp1252; a title it cannot encode must not
# take the run down with it.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(errors="replace")
    except Exception:
        pass

import archive
import sources


def collect_inputs(paths: "list[str]") -> "list[str]":
    found = []
    for path in paths:
        if os.path.isdir(path):
            for entry in sorted(os.listdir(path)):
                full = os.path.join(path, entry)
                if os.path.isfile(full) and sources.is_supported(entry):
                    found.append(full)
        elif os.path.isfile(path):
            found.append(path)
        else:
            print(f"  ! not found: {path}")
    return found


def describe(meta, chapters, work_id: str, verbose: bool) -> None:
    status = "complete" if meta.complete else "ongoing"
    print(f"    title    : {meta.title}")
    print(f"    author   : {meta.author}")
    print(f"    workId   : {work_id}")
    print(f"    language : {meta.language or '-'}   status: {status}")
    if meta.fandoms:
        print(f"    fandoms  : {', '.join(meta.fandoms[:3])}")
    if meta.tags:
        print(f"    tags     : {len(meta.tags)} ({', '.join(meta.tags[:3])}...)")
    total = sum(c.char_count for c in chapters)
    kept_images = sum(1 for c in chapters for b in c.blocks if b.kind == "img")
    print(f"    chapters : {len(chapters)}  ({total:,} chars)")
    if kept_images or meta.dropped_images:
        print(f"    images   : {kept_images} kept (linked), "
              f"{meta.dropped_images} dropped (embedded in the file - the app "
              f"cannot store those)")
    longest = max(chapters, key=lambda c: c.char_count, default=None)
    if meta.volumes:
        print(f"    volumes  : {len(meta.volumes)} joined ({', '.join(meta.volumes[:6])}"
              f"{'...' if len(meta.volumes) > 6 else ''})")
    if longest and longest.char_count > 120_000:
        print(f"    ! longest chapter is {longest.char_count:,} chars "
              f"({longest.title!r}) - consider --max-chars 80000")
    if verbose or len(chapters) <= 6:
        show = list(chapters)
    else:
        show = chapters[:3] + ["..."] + chapters[-3:]
    for item in show:
        if isinstance(item, str):
            print(f"      {item}")
        else:
            n = chapters.index(item) + 1
            print(f"      {n:>4}. {item.title[:60]:<60} {item.char_count:>8,} chars")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="import_builder",
        description="Convert .txt / .epub novels into an AReader import archive (.zip). "
                    "A .zip of per-volume files is joined into one work.")
    parser.add_argument("inputs", nargs="+",
                        help="files or folders to convert (.txt, .epub, or a .zip of volumes)")
    parser.add_argument("-o", "--out", default="areader-import.zip", help="output .zip path")
    parser.add_argument("--separate", action="store_true",
                        help="write one .zip per input instead of one combined archive")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the detected chapters without writing anything")
    parser.add_argument("--verbose", action="store_true", help="list every chapter")
    parser.add_argument("--language", default="",
                        help="override the language shown on the work card")
    parser.add_argument("--max-chars", type=int, default=0,
                        help="split any chapter longer than this into numbered parts")
    parser.add_argument("--keep-front-matter", action="store_true",
                        help="keep cover / contents / copyright pages as chapters")
    parser.add_argument("--no-images", action="store_true",
                        help="drop every image, including ones linked by URL")
    parser.add_argument("--local-ids", action="store_true",
                        help="ignore the AO3 work id an EPUB carries and mint a local one")
    options = parser.parse_args(argv)

    files = collect_inputs(options.inputs)
    if not files:
        print("Nothing to convert.")
        return 1

    built = []
    failures = 0
    for path in files:
        print(f"\n[{os.path.basename(path)}]")
        try:
            loaded = [(m, c) for m, c in sources.load_any(path, options) if c]
        except Exception as error:                       # noqa: BLE001 - report and continue
            print(f"  ! failed: {type(error).__name__}: {error}")
            failures += 1
            continue
        if not loaded:
            print("  ! no chapter text found")
            failures += 1
            continue
        for meta, chapters in loaded:
            if options.local_ids:
                meta.work_id = None
            work_id = archive.make_work_id(meta, chapters)
            describe(meta, chapters, work_id, options.verbose)
            built.append((path, archive.build_work(meta, chapters, work_id)))

    if options.dry_run:
        print(f"\nDry run: {len(built)} work(s) ready, {failures} failed. Nothing written.")
        return 1 if failures and not built else 0

    if options.separate:
        for path, work in built:
            out = os.path.join(os.path.dirname(os.path.abspath(options.out)),
                               archive.clean_filename(work["title"]) + ".zip")
            archive.write_archive(out, [work])
            print(f"\nWrote {out}  ({os.path.getsize(out):,} bytes)")
    elif built:
        archive.write_archive(options.out, [w for _, w in built])
        print(f"\nWrote {options.out}  ({os.path.getsize(options.out):,} bytes, "
              f"{len(built)} work(s))")

    if failures:
        print(f"{failures} file(s) failed.")
    return 0 if built else 1
