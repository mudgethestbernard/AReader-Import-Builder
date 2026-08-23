"""HTML/plain-text -> reader blocks.

The reader parses chapter HTML with Jsoup and only walks block-level tags
(HtmlBlockParser.handleElement), so a chapter body is emitted as a flat run of
<p>, <hr> and <hN>. Inline markup is kept to the handful of tags InlineHtml
renders; everything else is unwrapped.
"""

import html as _html
import re
from dataclasses import dataclass
from html.parser import HTMLParser

INLINE_KEEP = {"em", "strong", "i", "b", "u", "s", "strike", "sup", "sub", "code", "small"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCK_TAGS = HEADING_TAGS | {
    "p", "div", "blockquote", "li", "dt", "dd", "td", "th", "pre",
    "section", "article", "center", "figcaption",
}
SKIP_TAGS = {"script", "style", "head", "title", "svg", "template"}

# Symbols a scene-break line is built from. No "." or "…": a paragraph of bare
# ellipsis is dialogue, not a divider.
_BREAK_CHARS = set("*·•◦∙※★☆◆◇■□▲△▽○●◎+=~_-–—―─")

_TAG_RE = re.compile(r"<[^>]*>")


@dataclass
class Block:
    kind: str          # "p" | "hr" | "h" | "img"
    inline: str = ""   # sanitized inline HTML; for "img", the whole <img> tag
    level: int = 0     # heading level, 1-6

    @property
    def text(self) -> str:
        if self.kind == "img":
            return ""      # a src is not chapter text and must not be counted
        return _html.unescape(_TAG_RE.sub("", self.inline)).strip()


def is_scene_break(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 20:
        return False
    return all(ch in _BREAK_CHARS or ch.isspace() for ch in t)


def escape_text(data: str) -> str:
    return _html.escape(data, quote=False).replace("\xa0", "&nbsp;")


def is_loadable_image(src: str) -> bool:
    """The app never gets an EPUB's own image files: ArchiveImporter writes
    chapter HTML and nothing else, and only OfflineDownloadService fetches
    images into offline_images/. So only an absolute URL can ever render."""
    src = src.strip().lower()
    return src.startswith(("http://", "https://", "//"))


def blocks_to_html(blocks: list[Block]) -> str:
    out = []
    for b in blocks:
        if b.kind == "hr":
            out.append("<hr>")
        elif b.kind == "img":
            out.append(b.inline)
        elif b.kind == "h":
            out.append(f"<h{b.level}>{b.inline}</h{b.level}>")
        else:
            out.append(f"<p>{b.inline}</p>")
    return "\n".join(out)


class _BodyParser(HTMLParser):
    """Collects block-level content, remembering where each id anchor lands."""

    def __init__(self, keep_images: bool = True):
        super().__init__(convert_charrefs=True)
        self.keep_images = keep_images
        self.blocks: list[Block] = []
        self.anchors: dict[str, int] = {}
        self.dropped_images = 0
        self._buf: list[str] = []
        self._skip = 0
        self._seen_body = False
        self._in_body = False
        self._open: list[str] = []

    # -- helpers -------------------------------------------------------
    @property
    def _collecting(self) -> bool:
        return self._skip == 0 and (self._in_body or not self._seen_body)

    def _flush(self, tag: str = "p") -> None:
        raw = "".join(self._buf).strip()
        self._buf = []
        if not raw:
            return
        plain = _html.unescape(_TAG_RE.sub("", raw)).strip()
        if not plain:
            return
        if is_scene_break(plain):
            self.blocks.append(Block("hr"))
            return
        if tag in HEADING_TAGS:
            self.blocks.append(Block("h", raw, int(tag[1])))
        else:
            self.blocks.append(Block("p", raw))

    def _add_image(self, attrs: dict) -> None:
        src = (attrs.get("src") or "").strip()
        if not src:
            return
        if not self.keep_images or not is_loadable_image(src):
            self.dropped_images += 1
            return
        self._flush(self._open[-1] if self._open else "p")
        alt = escape_text((attrs.get("alt") or "").strip())
        tag = f'<img src="{escape_text(src)}"' + (f' alt="{alt}"' if alt else "") + ">"
        self.blocks.append(Block("img", tag))

    # -- HTMLParser ----------------------------------------------------
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if tag == "body":
            self._seen_body = True
            self._in_body = True
            return
        if self._skip:
            return
        for key, value in attrs:
            if key == "id" and value:
                self.anchors.setdefault(value, len(self.blocks))
        if not self._collecting:
            return
        if tag in BLOCK_TAGS:
            self._flush(self._open[-1] if self._open else "p")
            self._open.append(tag)
        elif tag == "hr":
            self._flush(self._open[-1] if self._open else "p")
            self.blocks.append(Block("hr"))
        elif tag == "img":
            self._add_image(dict(attrs))
        elif tag == "br":
            self._buf.append("<br>")
        elif tag in INLINE_KEEP:
            self._buf.append(f"<{tag}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in ("br", "hr", "img"):
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "body":
            self._flush(self._open[-1] if self._open else "p")
            self._in_body = False
            return
        if self._skip or not self._collecting:
            return
        if tag in BLOCK_TAGS:
            self._flush(tag)
            if tag in self._open:
                # Unbalanced markup happens; drop back to this tag's level.
                del self._open[self._open.index(tag):]
        elif tag in INLINE_KEEP:
            self._buf.append(f"</{tag}>")

    def handle_data(self, data):
        if self._skip or not self._collecting:
            return
        self._buf.append(escape_text(data))

    def close(self):
        super().close()
        self._flush(self._open[-1] if self._open else "p")


def parse_html(source: str, keep_images: bool = True) -> "tuple[list[Block], dict[str, int], int]":
    """Blocks, id anchors, and the count of images that could not be kept."""
    parser = _BodyParser(keep_images)
    parser.feed(source)
    parser.close()
    return parser.blocks, parser.anchors, parser.dropped_images


def parse_text_lines(lines: list[str]) -> list[Block]:
    """Plain-text lines -> blocks. Blank runs are dropped: paragraph spacing is
    the reader's, not the file's."""
    out: list[Block] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if is_scene_break(stripped):
            out.append(Block("hr"))
        else:
            out.append(Block("p", escape_text(stripped)))
    return out
