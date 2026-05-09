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

logger = logging.getLogger(__name__)


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
        logger.info("模板提取完成，全文块=%s，表格数=%s", block_count, len(profile.get("tables", [])))
        prompt = (
            "【模板全文块：请完整阅读，由你判断哪些是要求、示例、占位符或正文】\n"
            f"{json.dumps(profile.get('all_template_text_blocks', []), ensure_ascii=False, indent=2)}\n\n"
            "【模板结构、样式、表格、页眉页脚等完整画像】\n"
            f"{json.dumps(profile, ensure_ascii=False, indent=2)}"
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
        """返回内容画像、原始文本和 LLM 结构分析结果。"""

        profile = extract_content_profile(
            content_path,
            self.settings.document.max_content_chars,
            self.settings.document.max_content_blocks,
            self.settings.document.content_block_text_limit,
        )
        logger.info("内容提取完成，内容块=%s，表格数=%s", len(profile.get("blocks", [])), len(profile.get("tables", [])))
        start_time = perf_counter()
        logger.info("调用 LLM 判断内容结构")
        response = self.llm.invoke(
            [
                SystemMessage(content=self.prompt),
                HumanMessage(
                    content=(
                        "用户 Word 内容全文块、弱格式证据和可见文本如下。"
                        "请完整阅读并自行判断标题、正文、列表、表格和备注：\n"
                        f"{json.dumps(profile, ensure_ascii=False, indent=2)}"
                    )
                ),
            ]
        )
        content_analysis = message_content_to_text(response)
        content_structure = parse_content_structure_with_repair(self.llm, content_analysis)
        logger.info(
            "内容结构判断完成，结构项=%s，必须保留事实=%s，耗时 %.2fs",
            len(content_structure.items),
            len(content_structure.must_keep_facts),
            perf_counter() - start_time,
        )
        return {
            "content_profile": profile,
            "content_text": profile["ordered_visible_text"],
            "content_analysis": content_analysis,
            "content_structure": content_structure,
        }
