"""DOCX 解析器 — 包装现有 compare_util 中的格式提取逻辑。"""

from __future__ import annotations

from pathlib import Path

from docx import Document

from word_agent.compare_util import _effective_format
from word_agent.parsers import DocumentParser, UnifiedParagraph


class DocxParser(DocumentParser):
    @staticmethod
    def supported_extensions() -> list[str]:
        return [".docx"]

    def parse(self, file_path: Path) -> list[UnifiedParagraph]:
        doc = Document(file_path)
        results = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            fmt = _effective_format(para)
            fmt["source_type"] = "docx"
            fmt["semantic"] = _infer_semantic(fmt)
            results.append(UnifiedParagraph(text=text, format=fmt))
        return results


def _infer_semantic(fmt: dict) -> dict:
    """从 DOCX 样式名推断语义信息。"""
    style = (fmt.get("style") or "").lower()
    sem = {"element_type": "paragraph", "heading_level": None, "list_type": None, "list_depth": None, "code": False}
    if "heading" in style:
        sem["element_type"] = "heading"
        for i in range(1, 7):
            if str(i) in style:
                sem["heading_level"] = i
                break
    elif "list" in style or "bullet" in style:
        sem["element_type"] = "list_item"
        sem["list_type"] = "ordered" if "number" in style else "unordered"
    elif "code" in style:
        sem["element_type"] = "code_block"
        sem["code"] = True
    elif "quote" in style:
        sem["element_type"] = "blockquote"
    return sem
