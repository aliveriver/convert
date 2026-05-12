"""HTML 解析器 — 基于 BeautifulSoup 提取段落、inline style 视觉值和标签语义。"""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from word_agent.parsers import DocumentParser, UnifiedParagraph, empty_format

_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "li", "div", "section", "article"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class HtmlParser(DocumentParser):
    @staticmethod
    def supported_extensions() -> list[str]:
        return [".html", ".htm"]

    def parse(self, file_path: Path) -> list[UnifiedParagraph]:
        content = file_path.read_text(encoding="utf-8")
        soup = BeautifulSoup(content, "html.parser")
        body = soup.body if soup.body else soup
        results: list[UnifiedParagraph] = []
        _walk_elements(body, results)
        return results


def _walk_elements(element: Tag, results: list[UnifiedParagraph]):
    for child in element.children:
        if not isinstance(child, Tag):
            continue
        tag_name = child.name.lower()

        if tag_name in _BLOCK_TAGS:
            text = child.get_text(separator="", strip=True)
            if not text:
                continue
            fmt = _extract_format(child, tag_name)
            results.append(UnifiedParagraph(text=text, format=fmt))
        elif tag_name in ("ul", "ol"):
            _walk_list(child, results, "ordered" if tag_name == "ol" else "unordered", 0)
        elif tag_name in ("table", "header", "footer", "main", "nav"):
            _walk_elements(child, results)


def _walk_list(element: Tag, results: list[UnifiedParagraph], list_type: str, depth: int):
    for child in element.children:
        if not isinstance(child, Tag):
            continue
        if child.name.lower() == "li":
            text = child.get_text(separator="", strip=True)
            if text:
                fmt = _extract_format(child, "li")
                fmt["semantic"]["list_type"] = list_type
                fmt["semantic"]["list_depth"] = depth
                results.append(UnifiedParagraph(text=text, format=fmt))
            for sub in child.children:
                if isinstance(sub, Tag) and sub.name.lower() in ("ul", "ol"):
                    _walk_list(sub, results, "ordered" if sub.name.lower() == "ol" else "unordered", depth + 1)


def _extract_format(el: Tag, tag_name: str) -> dict:
    fmt = empty_format("html")
    style_str = el.get("style", "")
    styles = _parse_inline_style(style_str)

    if "font-size" in styles:
        fmt["font_size_pt"] = _parse_size_to_pt(styles["font-size"])
    if "font-family" in styles:
        fmt["font_name"] = styles["font-family"].split(",")[0].strip().strip("'\"")
    if "font-weight" in styles:
        w = styles["font-weight"].lower()
        fmt["bold"] = w in ("bold", "bolder", "700", "800", "900")
    if "font-style" in styles:
        fmt["italic"] = styles["font-style"].lower() in ("italic", "oblique")
    if "text-align" in styles:
        fmt["alignment"] = styles["text-align"].lower()
    if "line-height" in styles:
        fmt["line_spacing"] = _parse_line_height(styles["line-height"])
    if "margin-top" in styles:
        fmt["space_before_pt"] = _parse_size_to_pt(styles["margin-top"])
    if "margin-bottom" in styles:
        fmt["space_after_pt"] = _parse_size_to_pt(styles["margin-bottom"])
    if "text-indent" in styles:
        fmt["first_line_indent_pt"] = _parse_size_to_pt(styles["text-indent"])

    if not fmt["bold"]:
        fmt["bold"] = _has_bold_child(el) or tag_name in _HEADING_TAGS
    if not fmt["italic"]:
        fmt["italic"] = _has_italic_child(el)

    sem = {"element_type": "paragraph", "heading_level": None, "list_type": None, "list_depth": None, "code": False}
    if tag_name in _HEADING_TAGS:
        sem["element_type"] = "heading"
        sem["heading_level"] = int(tag_name[1])
    elif tag_name == "blockquote":
        sem["element_type"] = "blockquote"
    elif tag_name == "pre":
        sem["element_type"] = "code_block"
        sem["code"] = True
        if not fmt["font_name"]:
            fmt["font_name"] = "monospace"
    elif tag_name == "li":
        sem["element_type"] = "list_item"
    fmt["semantic"] = sem
    return fmt


def _parse_inline_style(style_str: str) -> dict[str, str]:
    result = {}
    if not style_str:
        return result
    for part in style_str.split(";"):
        part = part.strip()
        if ":" not in part:
            continue
        key, val = part.split(":", 1)
        result[key.strip().lower()] = val.strip()
    return result


def _parse_size_to_pt(value: str) -> float | None:
    value = value.strip().lower()
    m = re.match(r"([\d.]+)\s*(pt|px|em|rem|cm|mm|in)", value)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    if unit == "pt":
        return round(num, 1)
    elif unit == "px":
        return round(num * 0.75, 1)
    elif unit in ("em", "rem"):
        return round(num * 12, 1)
    elif unit == "cm":
        return round(num * 28.35, 1)
    elif unit == "mm":
        return round(num * 2.835, 1)
    elif unit == "in":
        return round(num * 72, 1)
    return None


def _parse_line_height(value: str) -> float | str | None:
    value = value.strip().lower()
    m = re.match(r"([\d.]+)\s*(pt|px)?", value)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    if unit == "pt":
        return f"{round(num, 1)}pt"
    elif unit == "px":
        return f"{round(num * 0.75, 1)}pt"
    return round(num, 2)


def _has_bold_child(el: Tag) -> bool:
    for tag in ("b", "strong"):
        if el.find(tag):
            return True
    return False


def _has_italic_child(el: Tag) -> bool:
    for tag in ("i", "em"):
        if el.find(tag):
            return True
    return False
