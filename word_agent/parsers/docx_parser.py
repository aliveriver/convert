"""DOCX 解析器 — 包装现有 compare_util 中的格式提取逻辑。"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

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
            text = _extract_paragraph_text(para, doc.part)
            if not text:
                continue
            fmt = _effective_format(para)
            fmt["source_type"] = "docx"
            fmt["semantic"] = _infer_semantic(fmt)
            results.append(UnifiedParagraph(text=text, format=fmt))
        return results


def _extract_paragraph_text(paragraph, doc_part) -> str:
    """提取段落文本，将超链接转为 [显示文本](URL) 格式。"""
    rels = doc_part.rels
    parts = []
    for child in paragraph._element:
        if child.tag == qn("w:hyperlink"):
            r_id = child.get(qn("r:id"))
            display = "".join(
                node.text or ""
                for node in child.iter(qn("w:t"))
            )
            if r_id and r_id in rels:
                url = rels[r_id].target_ref
                parts.append(f"[{display}]({url})")
            else:
                parts.append(display)
        elif child.tag == qn("w:r"):
            for t_node in child.iter(qn("w:t")):
                parts.append(t_node.text or "")
    return "".join(parts).strip()


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
