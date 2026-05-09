"""基于模板生成 Word 文档的 LangGraph 工作流。"""

from __future__ import annotations

import json
import logging
from time import perf_counter
import warnings
from pathlib import Path

from langchain_core._api.deprecation import LangChainPendingDeprecationWarning
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

warnings.filterwarnings(
    "ignore",
    message="The default value of `allowed_objects` will change.*",
    category=LangChainPendingDeprecationWarning,
    module="langgraph.cache.base.*",
)

from langgraph.graph import END, START, StateGraph

from word_agent.config import AppSettings, read_prompt
from word_agent.docx_io import write_generated_docx
from word_agent.llm import message_content_to_text, parse_generated_document_with_repair
from word_agent.models import AgentState
from word_agent.subagents import ContentDocumentAgent, TemplateDocumentAgent

logger = logging.getLogger(__name__)


class WordAgent:
    """由 LangGraph 驱动的 Word 模板生成 agent。"""

    def __init__(
        self,
        llm: BaseChatModel,
        settings: AppSettings,
        format_prompt_path: Path = Path("prompts/format_extraction.md"),
        generation_prompt_path: Path = Path("prompts/document_generation.md"),
        content_prompt_path: Path = Path("prompts/content_analysis.md"),
    ) -> None:
        self.llm = llm
        self.settings = settings
        self.generation_prompt = read_prompt(generation_prompt_path)
        self.template_agent = TemplateDocumentAgent(llm, settings, format_prompt_path)
        self.content_agent = ContentDocumentAgent(llm, settings, content_prompt_path)
        self.graph = self._build_graph()

    def _build_graph(self):
        """创建生成前并行执行的模板分析和内容分析分支。"""

        graph = StateGraph(AgentState)
        graph.add_node("analyze_template", self._analyze_template)
        graph.add_node("analyze_content", self._analyze_content)
        graph.add_node("generate_document", self._generate_document)
        graph.add_node("write_docx", self._write_docx)

        graph.add_edge(START, "analyze_template")
        graph.add_edge(START, "analyze_content")
        graph.add_edge(["analyze_template", "analyze_content"], "generate_document")
        graph.add_edge("generate_document", "write_docx")
        graph.add_edge("write_docx", END)
        return graph.compile()

    def run(self, template_path: Path, content_path: Path, output_path: Path) -> AgentState:
        """执行完整文档生成工作流。"""

        initial_state: AgentState = {
            "template_path": template_path,
            "content_path": content_path,
            "output_path": output_path,
        }
        final_state: AgentState = dict(initial_state)
        start_time = perf_counter()
        logger.info("LangGraph 开始执行: template=%s content=%s output=%s", template_path, content_path, output_path)
        runtime_config = {
            "run_name": self.settings.langsmith.run_name,
            "tags": self.settings.langsmith.tags,
            "metadata": {
                "llm_provider": self.settings.llm.provider,
                "llm_model": self.settings.llm.model,
                "template_path": str(template_path),
                "content_path": str(content_path),
                "output_path": str(output_path),
            },
        }
        logger.debug("LangGraph 运行配置: %s", runtime_config)
        for chunk in self.graph.stream(initial_state, config=runtime_config, stream_mode="updates"):
            for node_name, update in chunk.items():
                if not isinstance(update, dict):
                    logger.debug("节点 %s 返回非 dict 更新: %s", node_name, type(update).__name__)
                    continue
                final_state.update(update)
                logger.info("节点完成: %s，更新字段=%s", node_name, ", ".join(update.keys()))
        logger.info("LangGraph 执行完成，耗时 %.2fs", perf_counter() - start_time)
        return final_state

    def save_graph_debug(self, output_prefix: Path, include_png: bool = False) -> dict[str, Path]:
        """导出 LangGraph 的 Mermaid 调试文件，并按需生成 PNG。"""

        if output_prefix.suffix.lower() in {".mmd", ".md", ".png"}:
            base_path = output_prefix.with_suffix("")
        else:
            base_path = output_prefix
        base_path.parent.mkdir(parents=True, exist_ok=True)

        graph_view = self.graph.get_graph()
        mermaid_path = base_path.with_suffix(".mmd")
        mermaid_path.write_text(graph_view.draw_mermaid(), encoding="utf-8")
        exported = {"mermaid": mermaid_path}

        if include_png:
            png_path = base_path.with_suffix(".png")
            try:
                png_path.write_bytes(graph_view.draw_mermaid_png())
                exported["png"] = png_path
            except Exception as exc:
                logger.warning("PNG 图导出失败，仅保留 Mermaid 源码: %s", exc)
        return exported

    def _analyze_template(self, state: AgentState) -> AgentState:
        """运行模板子 agent。"""

        start_time = perf_counter()
        logger.info("模板子 agent 开始分析: %s", state["template_path"])
        result = self.template_agent.run(state["template_path"])
        block_count = len(result["template_profile"].get("all_template_text_blocks", []))
        logger.info("模板子 agent 完成，全文块=%s，耗时 %.2fs", block_count, perf_counter() - start_time)
        return result

    def _analyze_content(self, state: AgentState) -> AgentState:
        """运行内容子 agent。"""

        start_time = perf_counter()
        logger.info("内容子 agent 开始分析: %s", state["content_path"])
        result = self.content_agent.run(state["content_path"])
        block_count = len(result["content_profile"].get("blocks", []))
        item_count = len(result["content_structure"].items)
        logger.info("内容子 agent 完成，内容块=%s，结构项=%s，耗时 %.2fs", block_count, item_count, perf_counter() - start_time)
        return result

    def _generate_document(self, state: AgentState) -> AgentState:
        """生成可渲染到最终 DOCX 的 JSON 段落。"""

        start_time = perf_counter()
        template_text_blocks = state["template_profile"].get("all_template_text_blocks", [])
        logger.info("最终生成开始，模板块=%s，内容结构项=%s", len(template_text_blocks), len(state["content_structure"].items))
        template_blocks_json = json.dumps(template_text_blocks, ensure_ascii=False, indent=2)
        content_structure_json = state["content_structure"].model_dump_json(indent=2)
        user_prompt = (
            "模板全文块，供你核对格式要求、说明文字、示例文字和占位符：\n"
            f"{template_blocks_json}\n\n"
            "从模板分析得到的格式要求：\n"
            f"{state['format_requirements']}\n\n"
            "内容结构分析 JSON：\n"
            f"{content_structure_json}\n\n"
            "内容结构分析原文：\n"
            f"{state['content_analysis']}\n\n"
            "用户原始内容，保留事实但允许重组结构：\n"
            f"{state['content_text']}"
        )
        response = self.llm.invoke(
            [
                SystemMessage(content=self.generation_prompt),
                HumanMessage(content=user_prompt),
            ]
        )
        raw_response = message_content_to_text(response)
        logger.debug("最终生成 LLM 原始响应前 1000 字: %s", raw_response[:1000])
        try:
            generated_document = parse_generated_document_with_repair(self.llm, raw_response)
        except Exception:
            debug_path = state["output_path"].with_suffix(".generation.raw.txt")
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(raw_response, encoding="utf-8")
            logger.exception("最终生成 JSON 解析和修复均失败，原始响应已保存: %s", debug_path)
            raise
        logger.info(
            "最终生成完成，标题=%s，段落数=%s，耗时 %.2fs",
            generated_document.title,
            len(generated_document.paragraphs),
            perf_counter() - start_time,
        )
        return {"generated_document": generated_document}

    def _write_docx(self, state: AgentState) -> AgentState:
        """将生成段落写入基于模板的 DOCX 文件。"""

        start_time = perf_counter()
        logger.info("开始写入 DOCX: %s", state["output_path"])
        written_path = write_generated_docx(
            template_path=state["template_path"],
            output_path=state["output_path"],
            generated=state["generated_document"],
            fallback_style=self.settings.document.default_paragraph_style,
        )
        logger.info("DOCX 写入完成: %s，耗时 %.2fs", written_path, perf_counter() - start_time)
        return {"written_path": written_path}
