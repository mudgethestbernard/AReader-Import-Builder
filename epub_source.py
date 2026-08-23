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

# epubmerge appends its own word to every level of a merged book's titles.
_ANTHOLOGY_TAIL = re.compile(r"(\s+Anthology)+\s*$", re.I)


def _tag(element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "cp949", "latin-1"):
        try:
            return M.nfc(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    return M.nfc(data.decode("utf-8", errors="replace"))


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


class _NavPoint:
    """One NCX entry. `section` is the entry directly above it, which in a
    merged anthology is the volume this chapter belongs to."""

    def __init__(self, label, href, fragment, depth, section):
        self.label = label
        self.href = href
        self.fragment = fragment
        self.depth = depth
        self.section = section


def _ncx_points(book: _Epub, ncx_href: str) -> "list[_NavPoint]":
    """The NCX flattened in document order, each entry keeping its depth and the
    section it sits under. An NCX nests: volumes hold chapters."""
    data = book.read(book.resolve(book.opf_dir, ncx_href))
    if not data:
        return []
    ncx_dir = posixpath.dirname(book.resolve(book.opf_dir, ncx_href))
    out: "list[_NavPoint]" = []

    def own(element, name):
        """A navPoint's own label/content, not one belonging to a child."""
        for child in element:
            if _tag(child) == name:
                return child
            if _tag(child) == "navlabel" and name == "text":
                for grand in child:
                    if _tag(grand) == "text":
                        return grand
        return None

    def walk(element, depth, parent):
        for child in element:
            if _tag(child) != "navpoint":
                continue
            text = own(child, "text")
            content = own(child, "content")
            label = M.nfc((text.text or "").strip()) if text is not None else ""
            src = content.get("src") or "" if content is not None else ""
            if src:
                href, _, fragment = src.partition("#")
                # The entry directly above a chapter is its volume. An outer one
                # names the whole anthology, which every chapter shares.
                out.append(_NavPoint(label, book.resolve(ncx_dir, href), fragment,
                                     depth, parent))
            walk(child, depth + 1, label)

    for element in ET.fromstring(data).iter():
        if _tag(element) == "navmap":
            walk(element, 0, "")
            break
    return out


def _ncx_usable(points, docs) -> bool:
    known = {d.href for d in docs}
    points = [p for p in points if p.href in known]
    labels = [p.label for p in points]
    if len(points) < 2:
        return False
    if sum(1 for l in labels if not l or M.looks_like_hash(l)) * 2 > len(labels):
        return False
    return len(set(labels)) > 1


def _split_by_ncx(points, docs, flat) -> "list[C.ChapterDraft]":
    by_href = {d.href: d for d in docs}
    located = []
    for order, point in enumerate(points):
        doc = by_href.get(point.href)
        if doc is None:
            continue
        index = doc.offset + (doc.anchors.get(point.fragment, 0) if point.fragment else 0)
        located.append((index, order, point))
    # A volume entry sits on the same spot as its own first chapter. The deeper
    # of the two names the chapter; the shallower only names the volume.
    best = {}
    for index, order, point in located:
        current = best.get(index)
        if current is None or point.depth > current[1].depth:
            best[index] = (order, point)
    chosen = sorted(best.items(), key=lambda kv: kv[0])

    out = []
    for i, (index, (_, point)) in enumerate(chosen):
        end = chosen[i + 1][0] if i + 1 < len(chosen) else len(flat)
        body = flat[index:end]
        # The navPoint usually lands on the chapter's own heading; don't repeat it.
        if body and body[0].kind == "h" and body[0].text == point.label:
            body = body[1:]
        draft = C.ChapterDraft(point.label, body)
        draft.section = point.section if point.section != point.label else ""
        out.append(draft)
    _label_by_section(out)
    return out


def _label_by_section(drafts) -> None:
    """Prefix chapters with the volume the NCX filed them under, but only when
    leaving them bare would be ambiguous - a set that numbers its chapters from
    one in every volume repeats every name."""
    sections = [d.section for d in drafts]
    if not any(sections):
        return
    titles = [d.title for d in drafts]
    if len(set(titles)) == len(titles):
        return
    names = [_ANTHOLOGY_TAIL.sub("", s).strip() for s in dict.fromkeys(sections) if s]
    base = M.common_base_title(names)
    for draft in drafts:
        if not draft.section:
            continue
        label = M.volume_label(_ANTHOLOGY_TAIL.sub("", draft.section).strip(), base)
        if label and draft.title:
            draft.title = f"{label} - {draft.title}"


def _group_volume_title(docs) -> str:
    """A merged anthology opens each volume's directory with that volume's own
    title, above its table of contents. That line is the volume's name."""
    for doc in docs[:1]:
        for block in doc.blocks[:3]:
            text = block.text
            if text and len(text) <= 40 and not C.FRONT_MATTER.match(text):
                return text
    return ""


def _label_by_volume(groups) -> None:
    """Prefix each volume's chapters with the volume, but only when leaving them
    bare would be ambiguous. Volumes that number their chapters from 1 every
    time collide; ones numbered straight through the set do not, and gain
    nothing from the prefix."""
    titles = [g["volume_title"] for g in groups]
    if len(groups) < 2 or not all(titles):
        return
    seen, collides = set(), False
    for group in groups:
        for draft in group["drafts"]:
            if draft.title in seen:
                collides = True
            seen.add(draft.title)
    if not collides:
        return

    base = M.common_base_title(titles)
    for group in groups:
        label = M.volume_label(group["volume_title"], base)
        if not label:
            continue
        for draft in group["drafts"]:
            draft.title = f"{label} - {draft.title}" if draft.title else label


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
        seen_groups = []
        for doc in docs:
            if doc.group not in seen_groups:
                seen_groups.append(doc.group)
        groups = []
        for group in seen_groups:
            members = [d for d in docs if d.group == group]
            groups.append({
                "volume_title": _group_volume_title(members),
                "drafts": _split_group(members),
            })
        _label_by_volume(groups)
        drafts = [d for g in groups for d in g["drafts"]]

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
