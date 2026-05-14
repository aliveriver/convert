"""基于规则的内容块预分类，减少需要 LLM 判断的块数量。"""

from __future__ import annotations

import re

from word_agent.models import ContentStructureItem

_NUMBERING_PATTERNS = [
    re.compile(r"^第[一二三四五六七八九十\d]+[章节篇部]"),
    re.compile(r"^[一二三四五六七八九十]+[、.]"),
    re.compile(r"^（[一二三四五六七八九十\d]+）"),
    re.compile(r"^\d+\.\d+"),
    re.compile(r"^\d+[、.]"),
]

_NOTE_KEYWORDS = ["填写说明", "撰写要求", "请在此处填写", "注意事项", "格式要求", "示例"]


def pre_classify_block(block: dict) -> ContentStructureItem | None:
    """尝试用规则对单个块做确定性分类，返回 None 表示需要 LLM。"""

    text = (block.get("text") or "").strip()
    index = block.get("index", 0)

    if not text:
        return ContentStructureItem(source_index=index, role="note", text=text, confidence=0.99)

    if block.get("is_table_row"):
        return ContentStructureItem(source_index=index, role="table", text=text, confidence=0.95)

    text_len = len(text)

    for kw in _NOTE_KEYWORDS:
        if kw in text and text_len < 60:
            return ContentStructureItem(source_index=index, role="note", text=text, confidence=0.90)

    if text_len > 80 and text.endswith(("。", "；", "：", ".", "!", "？")):
        bold = block.get("bold", False)
        alignment = (block.get("alignment") or "").upper()
        if not bold and alignment != "CENTER":
            return ContentStructureItem(source_index=index, role="body", text=text, confidence=0.90)

    return None


def split_by_confidence(
    blocks: list[dict],
) -> tuple[list[ContentStructureItem], list[dict]]:
    """将块列表分为已确定和需要 LLM 判断两组。

    Returns:
        (classified, uncertain) — classified 是规则已确定的项，
        uncertain 是需要送 LLM 的原始 block。
    """

    classified: list[ContentStructureItem] = []
    uncertain: list[dict] = []

    for block in blocks:
        result = pre_classify_block(block)
        if result is not None:
            classified.append(result)
        else:
            uncertain.append(block)

    return classified, uncertain
