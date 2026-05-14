"""统一文档输入适配层。

根据文件扩展名选择合适的解析方式：
- .docx 使用 docx_io 的丰富提取（保留完整格式元数据）
- .tex / .md 使用 parsers/ 模块提取 UnifiedParagraph，再转为 block_dict
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from word_agent.parsers import UnifiedParagraph, get_parser


def _unified_to_block(index: int, para: UnifiedParagraph) -> dict[str, Any]:
    """将 UnifiedParagraph 转为主 pipeline 使用的 block_dict 格式。"""

    fmt = para.format or {}
    semantic = fmt.get("semantic") or {}

    is_table = semantic.get("element_type") == "table"
    return {
        "index": index,
        "text": para.text,
        "style": fmt.get("style") or "",
        "alignment": fmt.get("alignment") or "",
        "font_name": fmt.get("font_name"),
        "font_size_pt": fmt.get("font_size_pt"),
        "bold": fmt.get("bold", False),
        "italic": fmt.get("italic", False),
        "line_spacing": fmt.get("line_spacing"),
        "space_before_pt": fmt.get("space_before_pt"),
        "space_after_pt": fmt.get("space_after_pt"),
        "numbering_hint": None,
        "text_length": len(para.text),
        "source_type": fmt.get("source_type", "unknown"),
        "is_table_row": is_table,
    }


def _unified_to_blocks(paragraphs: list[UnifiedParagraph]) -> list[dict[str, Any]]:
    """批量转换 UnifiedParagraph 列表为 block_dict 列表。"""

    return [_unified_to_block(i, p) for i, p in enumerate(paragraphs)]


def _is_docx(path: Path) -> bool:
    return path.suffix.lower() == ".docx"


def extract_template(path: Path, max_chars: int = 0) -> dict[str, Any]:
    """统一模板提取接口，返回与 docx_io.extract_template_profile 兼容的 dict。"""

    if _is_docx(path):
        from word_agent.docx_io import extract_template_profile
        return extract_template_profile(path, max_chars)

    parser = get_parser(path)
    paragraphs = parser.parse(path)
    blocks = _unified_to_blocks(paragraphs)

    all_text_blocks = [
        {"index": b["index"], "text": b["text"], "source_type": b["source_type"]}
        for b in blocks
    ]

    return {
        "path": str(path),
        "sections": [],
        "paragraph_styles": [],
        "tables": [],
        "all_template_text_blocks": all_text_blocks,
        "paragraph_blocks": blocks,
        "visible_text": "\n".join(b["text"] for b in blocks if b["text"].strip()),
    }


def extract_content(
    path: Path,
    max_chars: int = 0,
    max_blocks: int = 0,
    block_text_limit: int = 0,
) -> dict[str, Any]:
    """统一内容提取接口，返回与 docx_io.extract_content_profile 兼容的 dict。"""

    if _is_docx(path):
        from word_agent.docx_io import extract_content_profile
        return extract_content_profile(path, max_chars, max_blocks, block_text_limit)

    parser = get_parser(path)
    paragraphs = parser.parse(path)
    blocks = _unified_to_blocks(paragraphs)

    if max_blocks > 0:
        blocks = blocks[:max_blocks]
    if block_text_limit > 0:
        for block in blocks:
            if len(block["text"]) > block_text_limit:
                block["text"] = block["text"][:block_text_limit]
                block["text_length"] = block_text_limit

    visible_text = "\n".join(b["text"] for b in blocks if b["text"].strip())
    if max_chars > 0 and len(visible_text) > max_chars:
        visible_text = visible_text[:max_chars]

    return {
        "path": str(path),
        "blocks": blocks,
        "tables": [],
        "ordered_visible_text": visible_text,
    }
