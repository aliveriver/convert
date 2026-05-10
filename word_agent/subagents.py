"""用于独立分析 Word 文档的专用子 agent。

TemplateDocumentAgent 会把模板全文和样式画像交给 LLM。
ContentDocumentAgent 会把内容全文和弱格式证据交给 LLM，使其在不依赖 Word 样式的情况下恢复结构。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from time import perf_counter

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from word_agent.config import AppSettings, read_prompt
from word_agent.docx_io import extract_content_profile, extract_template_profile
from word_agent.llm import message_content_to_text, parse_content_structure_with_repair
from word_agent.models import ContentStructure, ContentStructureItem

logger = logging.getLogger(__name__)


def _chunk_blocks(
    blocks: list[dict[str, object]],
    chunk_size: int,
    overlap: int,
) -> list[list[dict[str, object]]]:
    """Split source blocks for LLM analysis without discarding any extracted text."""

    if chunk_size <= 0 or len(blocks) <= chunk_size:
        return [blocks] if blocks else []
    safe_overlap = min(max(overlap, 0), chunk_size - 1)
    step = chunk_size - safe_overlap
    chunks: list[list[dict[str, object]]] = []
    for start in range(0, len(blocks), step):
        chunk = blocks[start : start + chunk_size]
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(blocks):
            break
    return chunks


def _table_summaries(profile: dict[str, object]) -> list[dict[str, object]]:
    """Keep table shape hints while avoiding a second full copy of table cell text."""

    summaries: list[dict[str, object]] = []
    for table in profile.get("tables", []):
        if isinstance(table, dict):
            summaries.append(
                {
                    "index": table.get("index"),
                    "rows": table.get("rows"),
                    "columns": table.get("columns"),
                }
            )
    return summaries


def _compact_content_profile(
    profile: dict[str, object],
    blocks: list[dict[str, object]],
    chunk_index: int,
    chunk_count: int,
) -> dict[str, object]:
    """Build the per-call payload sent to the content-analysis LLM."""

    source_indices = [block.get("index") for block in blocks if isinstance(block.get("index"), int)]
    return {
        "path": profile.get("path"),
        "chunk_index": chunk_index,
        "chunk_count": chunk_count,
        "source_index_range": [min(source_indices), max(source_indices)] if source_indices else [],
        "blocks": blocks,
        "tables": _table_summaries(profile),
    }


def _compact_profile_for_state(profile: dict[str, object]) -> dict[str, object]:
    """Return a small state payload so LangGraph traces do not store the full document twice."""

    return {
        "path": profile.get("path"),
        "block_count": len(profile.get("blocks", [])),
        "table_count": len(profile.get("tables", [])),
    }


def _append_unique(items: list[str], value: str) -> None:
    """Append text once while preserving first-seen order."""

    normalized = value.strip()
    if normalized and normalized not in items:
        items.append(normalized)


def _merge_content_structures(structures: list[ContentStructure]) -> ContentStructure:
    """Merge chunk-level structure results, resolving overlap duplicates by confidence."""

    title = next((item.title for item in structures if item.title.strip()), "")
    items_by_index: dict[int, ContentStructureItem] = {}
    must_keep_facts: list[str] = []
    uncertain_items: list[str] = []

    for structure in structures:
        for item in structure.items:
            existing = items_by_index.get(item.source_index)
            if existing is None or item.confidence > existing.confidence:
                items_by_index[item.source_index] = item
        for fact in structure.must_keep_facts:
            _append_unique(must_keep_facts, fact)
        for uncertain in structure.uncertain_items:
            _append_unique(uncertain_items, uncertain)

    return ContentStructure(
        title=title,
        items=[items_by_index[index] for index in sorted(items_by_index)],
        must_keep_facts=must_keep_facts,
        uncertain_items=uncertain_items,
    )


class TemplateDocumentAgent:
    """使用所有提取文本和格式信号分析模板 DOCX。"""

    def __init__(
        self,
        llm: BaseChatModel,
        settings: AppSettings,
        prompt_path: Path,
    ) -> None:
        self.llm = llm
        self.settings = settings
        self.prompt = read_prompt(prompt_path)

    def run(self, template_path: Path) -> dict[str, object]:
        """返回模板画像和 LLM 推断出的行文要求。"""

        profile = extract_template_profile(
            template_path,
            self.settings.document.max_template_chars,
        )
        block_count = len(profile.get("all_template_text_blocks", []))
        profile_without_full_text = dict(profile)
        profile_without_full_text.pop("all_template_text_blocks", None)
        profile_without_full_text.pop("visible_text", None)
        logger.info("模板提取完成，全文块=%s，表格数=%s", block_count, len(profile.get("tables", [])))
        prompt = (
            "【模板全文块：请完整阅读，由你判断哪些是要求、示例、占位符或正文】\n"
            f"{json.dumps(profile.get('all_template_text_blocks', []), ensure_ascii=False, indent=2)}\n\n"
            "【模板结构、样式、表格、页眉页脚等完整画像】\n"
            f"{json.dumps(profile_without_full_text, ensure_ascii=False, indent=2)}"
        )
        start_time = perf_counter()
        logger.info("调用 LLM 总结模板行文要求")
        response = self.llm.invoke(
            [
                SystemMessage(content=self.prompt),
                HumanMessage(content=prompt),
            ]
        )
        requirements = message_content_to_text(response)
        logger.info("模板要求总结完成，字符数=%s，耗时 %.2fs", len(requirements), perf_counter() - start_time)
        return {
            "template_profile": profile,
            "format_requirements": requirements,
        }


class ContentDocumentAgent:
    """分析非规整内容 DOCX，并推断标题和正文结构。"""

    def __init__(
        self,
        llm: BaseChatModel,
        settings: AppSettings,
        prompt_path: Path,
    ) -> None:
        self.llm = llm
        self.settings = settings
        self.prompt = read_prompt(prompt_path)

    def run(self, content_path: Path) -> dict[str, object]:
        """Return a merged structure analysis while keeping each LLM input bounded."""

        profile = extract_content_profile(
            content_path,
            self.settings.document.max_content_chars,
            self.settings.document.max_content_blocks,
            self.settings.document.content_block_text_limit,
        )
        blocks = profile.get("blocks", [])
        if not isinstance(blocks, list):
            blocks = []
        chunks = _chunk_blocks(
            blocks,
            self.settings.document.content_analysis_chunk_size,
            self.settings.document.content_analysis_chunk_overlap,
        )
        logger.info(
            "内容提取完成，内容块=%s，表格数=%s，分析分块=%s",
            len(blocks),
            len(profile.get("tables", [])),
            len(chunks),
        )
        if not chunks:
            return {
                "content_profile": _compact_profile_for_state(profile),
                "content_analysis": "内容文档未提取到可见文本块。",
                "content_block_count": 0,
                "content_analysis_chunk_count": 0,
                "content_structure": ContentStructure(),
            }

        start_time = perf_counter()
        structures: list[ContentStructure] = []
        logger.info("调用 LLM 分块判断内容结构")
        for chunk_index, chunk in enumerate(chunks, start=1):
            chunk_payload = _compact_content_profile(profile, chunk, chunk_index, len(chunks))
            response = self.llm.invoke(
                [
                    SystemMessage(content=self.prompt),
                    HumanMessage(
                        content=(
                            "下面是用户 Word 内容的一个连续分块。全文会被分块完整处理；"
                            "请只分析当前分块中的 blocks，保留原始 source_index，"
                            "不要因为没有看到全文其他部分而改写或补造事实。\n"
                            f"{json.dumps(chunk_payload, ensure_ascii=False, indent=2)}"
                        )
                    ),
                ]
            )
            content_analysis = message_content_to_text(response)
            structure = parse_content_structure_with_repair(self.llm, content_analysis)
            structures.append(structure)
            logger.info(
                "内容结构分块完成 %s/%s，源块=%s，结构项=%s",
                chunk_index,
                len(chunks),
                len(chunk),
                len(structure.items),
            )

        content_structure = _merge_content_structures(structures)
        logger.info(
            "内容结构合并完成，结构项=%s，必须保留事实=%s，耗时 %.2fs",
            len(content_structure.items),
            len(content_structure.must_keep_facts),
            perf_counter() - start_time,
        )
        return {
            "content_profile": _compact_profile_for_state(profile),
            "content_analysis": f"内容已按 {len(chunks)} 个分块完成结构分析并合并。",
            "content_block_count": len(blocks),
            "content_analysis_chunk_count": len(chunks),
            "content_structure": content_structure,
        }

