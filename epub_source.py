"""EPUB 2/3 reading: spine order, then whichever chapter structure the book
actually has — its NCX, its headings, or a repeated marker paragraph."""

import io
import os
import posixpath
import re
import zipfile
from urllib.parse import unquote
from xml.etree import ElementTree as ET

import blocks as B
import chapters as C
import metadata as M
import txt_source

# Documents that are packaging rather than story. A book's own chapter files
# never carry these names, so matching the stem is safe.
EXTRA_STEMS = re.compile(
    r"^(cover|title(page)?|toc|contents|nav|copyright|colophon|dedication|"
    r"about|ad|advert\w*|promo|목차|표지|판권)[-_ ]?\d*$", re.I)
MIN_DOC_CHARS = 150


def _tag(element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "cp949", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class _Epub:
    def __init__(self, path: str):
        self.zip = zipfile.ZipFile(path)
        self._names = {n.lower(): n for n in self.zip.namelist()}
        self.opf_path = self._find_opf()
        self.opf_dir = posixpath.dirname(self.opf_path)

    def read(self, path: str) -> "bytes | None":
        for candidate in (path, unquote(path)):
            name = self._names.get(candidate.lower())
            if name:
                return self.zip.read(name)
        return None

    def resolve(self, base_dir: str, href: str) -> str:
        # NCX and OPF hrefs are URL-escaped; zip entry names are not, so a title
        # with a space only matches once the escapes are undone.
        href = unquote(href.split("#", 1)[0])
        return posixpath.normpath(posixpath.join(base_dir, href)) if base_dir else href

    def _find_opf(self) -> str:
        container = self.read("META-INF/container.xml")
        if container:
            for element in ET.fromstring(container).iter():
                if _tag(element) == "rootfile" and element.get("full-path"):
                    return element.get("full-path")
        for name in self.zip.namelist():
            if name.lower().endswith(".opf"):
                return name
        raise ValueError("no OPF package document")


def _parse_opf(book: _Epub):
    root = ET.fromstring(book.read(book.opf_path))
    dc: dict[str, list[str]] = {}
    subjects: list[str] = []
    manifest: dict[str, tuple[str, str]] = {}
    spine: list[str] = []
    ncx_id = None

    for element in root.iter():
        name = _tag(element)
        if name == "metadata":
            for child in element:
                key = _tag(child)
                value = (child.text or "").strip()
                if not value:
                    continue
                if key == "subject":
                    subjects.append(value)
                else:
                    dc.setdefault(key, []).append(value)
        elif name == "item":
            item_id = element.get("id") or ""
            manifest[item_id] = (element.get("href") or "", element.get("media-type") or "")
        elif name == "spine":
            ncx_id = element.get("toc")
            for child in element:
                if _tag(child) == "itemref" and child.get("idref"):
                    spine.append(child.get("idref"))

    ncx_href = None
    if ncx_id and ncx_id in manifest:
        ncx_href = manifest[ncx_id][0]
    else:
        for href, media in manifest.values():
            if "dtbncx" in media:
                ncx_href = href
                break
    return dc, subjects, manifest, spine, ncx_href


class _Doc:
    def __init__(self, href: str, source: str, keep_images: bool = True):
        self.href = href
        self.source = source
        self.blocks, self.anchors, self.dropped_images = B.parse_html(source, keep_images)
        self.offset = 0

    @property
    def stem(self) -> str:
        return posixpath.splitext(posixpath.basename(self.href))[0]

    @property
    def group(self) -> str:
        return posixpath.dirname(self.href)

    @property
    def char_count(self) -> int:
        return sum(len(b.text) for b in self.blocks)

    def doc_title(self) -> str:
        for block in self.blocks:
            if block.kind == "h" and block.text:
                return block.text
        found = re.search(r"<title[^>]*>(.*?)</title>", self.source, re.I | re.S)
        if found:
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", found.group(1))).strip()
            if title:
                return title
        return self.stem


def _ncx_points(book: _Epub, ncx_href: str) -> "list[tuple[str, str, str]]":
    """(label, resolved href, fragment) in document order."""
    data = book.read(book.resolve(book.opf_dir, ncx_href))
    if not data:
        return []
    ncx_dir = posixpath.dirname(book.resolve(book.opf_dir, ncx_href))
    out = []
    for element in ET.fromstring(data).iter():
        if _tag(element) != "navpoint":
            continue
        label = ""
        src = ""
        for child in element.iter():
            name = _tag(child)
            if name == "text" and not label:
                label = (child.text or "").strip()
            elif name == "content" and not src:
                src = child.get("src") or ""
        if not src:
            continue
        href, _, fragment = src.partition("#")
        out.append((label, book.resolve(ncx_dir, href), fragment))
    return out


def _ncx_usable(points, docs) -> bool:
    known = {d.href for d in docs}
    points = [p for p in points if p[1] in known]
    labels = [p[0] for p in points]
    if len(points) < 2:
        return False
    if sum(1 for l in labels if not l or M.looks_like_hash(l)) * 2 > len(labels):
        return False
    return len(set(labels)) > 1


def _split_by_ncx(points, docs, flat) -> "list[C.ChapterDraft]":
    by_href = {d.href: d for d in docs}
    starts = []
    for label, href, fragment in points:
        doc = by_href.get(href)
        if doc is None:
            continue
        index = doc.offset + (doc.anchors.get(fragment, 0) if fragment else 0)
        starts.append((index, label))
    starts.sort(key=lambda s: s[0])
    # Two navPoints on one spot (a nested NCX repeats its parent) are one chapter.
    deduped = []
    for index, label in starts:
        if deduped and deduped[-1][0] == index:
            continue
        deduped.append((index, label))

    out = []
    for i, (index, label) in enumerate(deduped):
        end = deduped[i + 1][0] if i + 1 < len(deduped) else len(flat)
        body = flat[index:end]
        # The navPoint usually lands on the chapter's own heading; don't repeat it.
        if body and body[0].kind == "h" and body[0].text == label:
            body = body[1:]
        out.append(C.ChapterDraft(label, body))
    return out


def _split_group(docs) -> "list[C.ChapterDraft]":
    """Chapter structure inside one directory of the book."""
    flat = [b for d in docs for b in d.blocks]
    headings = sum(1 for b in flat if b.kind == "h" and b.text)
    if headings >= 2:
        drafts = C.split_on_headings(flat)
        if drafts:
            return drafts
    family = C.pick_family(flat)
    if family is not None:
        drafts = C.split_on_markers(flat, family[1])
        for draft in drafts:
            C.fold_subtitle(draft)
        if drafts:
            return drafts
    if headings == 1:
        drafts = C.split_on_headings(flat)
        if drafts:
            return drafts
    return [C.ChapterDraft(d.doc_title(), d.blocks) for d in docs]


def load(source, options, name=None, mtime=None) -> "tuple[M.WorkMeta, list[C.ChapterDraft]]":
    """`source` is a path, or the file's bytes together with its `name` - a
    volume inside a zip is read straight from memory."""
    if isinstance(source, (bytes, bytearray)):
        handle = io.BytesIO(bytes(source))
        label = name or "untitled.epub"
    else:
        handle = source
        label = name or source
        if mtime is None:
            mtime = os.path.getmtime(source)

    book = _Epub(handle)
    dc, subjects, manifest, spine, ncx_href = _parse_opf(book)

    docs: list[_Doc] = []
    preface: "M.WorkMeta | None" = None
    preface_href = ""
    for idref in spine:
        href, media = manifest.get(idref, ("", ""))
        if not href or ("html" not in media and not href.lower().endswith((".html", ".xhtml", ".htm"))):
            continue
        resolved = book.resolve(book.opf_dir, href)
        data = book.read(resolved)
        if data is None:
            continue
        text = _decode(data)
        if preface is None:
            found = M.parse_ao3_preface(text)
            if found:
                preface, preface_href = found, resolved
        docs.append(_Doc(resolved, text, not options.no_images))

    if not options.keep_front_matter:
        # The AO3 preface page is the work's metadata, which is already read into
        # `preface`; left in, it would take chapter 1 and shift every number.
        docs = [d for d in docs
                if d.href != preface_href
                and not EXTRA_STEMS.match(d.stem)
                and d.char_count >= MIN_DOC_CHARS]
    if not docs:
        return M.parse_filename(_stem(label)), []

    offset = 0
    for doc in docs:
        doc.offset = offset
        offset += len(doc.blocks)
    flat = [b for d in docs for b in d.blocks]

    points = _ncx_points(book, ncx_href) if ncx_href else []
    if _ncx_usable(points, docs):
        drafts = _split_by_ncx(points, docs, flat)
    else:
        drafts = []
        seen_groups = []
        for doc in docs:
            if doc.group not in seen_groups:
                seen_groups.append(doc.group)
        for group in seen_groups:
            drafts.extend(_split_group([d for d in docs if d.group == group]))

    drafts = C.strip_title_echoes(drafts)
    if not options.keep_front_matter:
        drafts = C.drop_front_matter(drafts)
    drafts = C.drop_empty(drafts, MIN_DOC_CHARS if len(drafts) > 1 else 1)
    drafts = C.split_long(drafts, options.max_chars)

    # Metadata: the AO3 preface is richest, then the OPF, then the filename.
    meta = preface or M.WorkMeta()
    meta.merge_missing(M.parse_opf_meta(dc, subjects))
    meta.merge_missing(M.parse_filename(_stem(label)))
    sample = " ".join(b.text for b in flat[:40])
    meta.language = M.normalize_language(options.language or meta.language, sample)
    if not meta.published:
        meta.published = txt_source.date_from(mtime)
    meta.dropped_images = sum(d.dropped_images for d in docs)
    return meta, drafts


def _stem(name: str) -> str:
    return os.path.splitext(os.path.basename(name))[0]
