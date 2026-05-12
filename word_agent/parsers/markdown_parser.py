"""Markdown 解析器 — 基于 mistune AST 提取段落和语义格式。"""

from __future__ import annotations

from pathlib import Path

import mistune

from word_agent.parsers import DocumentParser, UnifiedParagraph, empty_format


class MarkdownParser(DocumentParser):
    @staticmethod
    def supported_extensions() -> list[str]:
        return [".md", ".markdown"]

    def parse(self, file_path: Path) -> list[UnifiedParagraph]:
        content = file_path.read_text(encoding="utf-8")
        md = mistune.create_markdown(renderer=None)
        tokens = md(content)
        results: list[UnifiedParagraph] = []
        _walk_tokens(tokens, results)
        return results


def _walk_tokens(tokens: list, results: list[UnifiedParagraph]):
    for token in tokens:
        tok_type = token["type"]

        if tok_type == "heading":
            text = _extract_text(token["children"])
            if not text.strip():
                continue
            fmt = empty_format("markdown")
            level = token.get("attrs", {}).get("level", 1) if "attrs" in token else token.get("level", 1)
            fmt["bold"] = True
            fmt["semantic"] = _sem(element_type="heading", heading_level=level)
            results.append(UnifiedParagraph(text=text.strip(), format=fmt))

        elif tok_type == "paragraph":
            text = _extract_text(token["children"])
            if not text.strip():
                continue
            fmt = empty_format("markdown")
            bold, italic = _detect_emphasis(token["children"])
            fmt["bold"] = bold
            fmt["italic"] = italic
            fmt["semantic"] = _sem(element_type="paragraph")
            results.append(UnifiedParagraph(text=text.strip(), format=fmt))

        elif tok_type == "block_quote":
            children = token.get("children", [])
            text = _extract_text_from_blocks(children)
            if not text.strip():
                continue
            fmt = empty_format("markdown")
            fmt["italic"] = True
            fmt["semantic"] = _sem(element_type="blockquote")
            results.append(UnifiedParagraph(text=text.strip(), format=fmt))

        elif tok_type == "block_code":
            text = token.get("raw", "") or token.get("text", "")
            if not text.strip():
                continue
            fmt = empty_format("markdown")
            fmt["font_name"] = "monospace"
            fmt["semantic"] = _sem(element_type="code_block", code=True)
            results.append(UnifiedParagraph(text=text.strip(), format=fmt))

        elif tok_type == "list":
            ordered = token.get("attrs", {}).get("ordered", False) if "attrs" in token else token.get("ordered", False)
            children = token.get("children", [])
            _walk_list_items(children, results, "ordered" if ordered else "unordered", 0)


def _sem(element_type="paragraph", heading_level=None, list_type=None, list_depth=None, code=False):
    return {
        "element_type": element_type,
        "heading_level": heading_level,
        "list_type": list_type,
        "list_depth": list_depth,
        "code": code,
    }


def _walk_list_items(items: list, results: list[UnifiedParagraph], list_type: str, depth: int):
    for item in items:
        if item.get("type") != "list_item":
            continue
        children = item.get("children", [])
        text = _extract_text_from_blocks(children)
        if text.strip():
            fmt = empty_format("markdown")
            fmt["semantic"] = _sem(element_type="list_item", list_type=list_type, list_depth=depth)
            results.append(UnifiedParagraph(text=text.strip(), format=fmt))
        for child in children:
            if child.get("type") == "list":
                sub_ordered = child.get("attrs", {}).get("ordered", False) if "attrs" in child else child.get("ordered", False)
                _walk_list_items(child.get("children", []), results, "ordered" if sub_ordered else "unordered", depth + 1)


def _extract_text_from_blocks(blocks: list) -> str:
    parts = []
    for block in blocks:
        if block.get("type") == "paragraph":
            parts.append(_extract_text(block.get("children", [])))
        elif "children" in block:
            parts.append(_extract_text(block["children"]))
        elif "raw" in block:
            parts.append(block["raw"])
        elif "text" in block:
            parts.append(block["text"])
    return " ".join(parts)


def _extract_text(children: list) -> str:
    parts = []
    for child in children:
        if "raw" in child:
            parts.append(child["raw"])
        elif "text" in child:
            parts.append(child["text"])
        elif "children" in child:
            parts.append(_extract_text(child["children"]))
    return "".join(parts)


def _detect_emphasis(children: list) -> tuple[bool, bool]:
    bold = False
    italic = False
    for child in children:
        t = child.get("type", "")
        if t == "strong":
            bold = True
        elif t == "emphasis":
            italic = True
        if bold and italic:
            break
    return bold, italic
