"""基于模板生成 Word 文档的 LangGraph 工作流。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
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
from word_agent.models import AgentState, ContentStructureItem, GeneratedDocument
from word_agent.subagents import ContentDocumentAgent, TemplateDocumentAgent

logger = logging.getLogger(__name__)


def _worker_count(task_count: int, configured_workers: int) -> int:
    """Return a safe worker count; set config to 1 to force serial execution."""

    if task_count <= 1:
        return 1
    return min(max(configured_workers, 1), task_count)


def _chunk_items(
    items: list[ContentStructureItem],
    chunk_size: int,
) -> list[list[ContentStructureItem]]:
    """Split structured content items for bounded final-generation calls."""

    if chunk_size <= 0 or len(items) <= chunk_size:
        return [items] if items else []
    return [items[start : start + chunk_size] for start in range(0, len(items), chunk_size)]


def _content_outline(items: list[ContentStructureItem]) -> list[dict[str, object]]:
    """Build a compact heading outline to keep each generation chunk oriented."""

    outline_roles = {"title", "heading_1", "heading_2", "heading_3"}
    outline: list[dict[str, object]] = []
    for item in items:
        if item.role not in outline_roles:
            continue
        text = item.text.strip()
        outline.append(
            {
                "source_index": item.source_index,
                "role": item.role,
                "text": text[:120],
            }
        )
    return outline


def _template_generation_context(profile: dict[str, object]) -> dict[str, object]:
    """Expose only lightweight template hints needed during generation."""

    return {
        "sections": profile.get("sections", []),
        "paragraph_styles": profile.get("paragraph_styles", []),
        "table_count": len(profile.get("tables", [])),
    }


def _merge_generated_documents(
    title_hint: str,
    parts: list[GeneratedDocument],
) -> GeneratedDocument:
    """Combine chunk-level generation outputs into one renderable document."""

    title = title_hint or next((part.title for part in parts if part.title.strip()), "")
    paragraphs = []
    for part in parts:
        paragraphs.extend(part.paragraphs)
    return GeneratedDocument(title=title, paragraphs=paragraphs)


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
        block_count = result.get("template_block_count", 0)
        cache_hit = result.get("template_cache_hit", False)
        logger.info(
            "模板子 agent 完成，全文块=%s，缓存命中=%s，耗时 %.2fs",
            block_count,
            cache_hit,
            perf_counter() - start_time,
        )
        return result

    def _analyze_content(self, state: AgentState) -> AgentState:
        """运行内容子 agent。"""

        start_time = perf_counter()
        logger.info("内容子 agent 开始分析: %s", state["content_path"])
        result = self.content_agent.run(state["content_path"])
        block_count = result.get("content_block_count", 0)
        item_count = len(result["content_structure"].items)
        chunk_count = result.get("content_analysis_chunk_count", 0)
        logger.info(
            "内容子 agent 完成，内容块=%s，分析分块=%s，结构项=%s，耗时 %.2fs",
            block_count,
            chunk_count,
            item_count,
            perf_counter() - start_time,
        )
        return result

    def _generate_document(self, state: AgentState) -> AgentState:
        """生成可渲染到最终 DOCX 的 JSON 段落。"""

        start_time = perf_counter()
        content_structure = state["content_structure"]
        content_items = content_structure.items
        item_chunks = _chunk_items(content_items, self.settings.document.generation_chunk_size)
        template_context = _template_generation_context(state["template_profile"])
        outline = _content_outline(content_items)
        logger.info(
            "最终生成开始，内容结构项=%s，生成分块=%s",
            len(content_items),
            len(item_chunks),
        )
        max_workers = _worker_count(
            len(item_chunks),
            self.settings.document.generation_max_workers,
        )
        logger.info("最终生成分块并发数=%s", max_workers)
        generated_parts_by_index: list[GeneratedDocument | None] = [None] * len(item_chunks)

        def generate_chunk(
            chunk_index: int,
            item_chunk: list[ContentStructureItem],
        ) -> tuple[int, GeneratedDocument]:
            """Generate one document chunk and return its original chunk index."""

            chunk_payload = {
                "document_title": content_structure.title,
                "chunk_index": chunk_index,
                "chunk_count": len(item_chunks),
                "outline": outline,
                "items": [item.model_dump() for item in item_chunk],
            }
            user_prompt = (
                "请根据模板要求和当前内容分块生成可写入 Word 的 JSON。"
                "全文内容已经在上游完成分块结构化分析；你本次只能生成当前分块对应的段落，"
                "不要补写当前分块没有提供的事实，也不要重复生成其他分块的正文。\n\n"
                "可用模板样式和轻量版式信息：\n"
                f"{json.dumps(template_context, ensure_ascii=False, indent=2)}\n\n"
                "从模板分析得到的格式要求：\n"
                f"{state['format_requirements']}\n\n"
                "当前内容分块 JSON：\n"
                f"{json.dumps(chunk_payload, ensure_ascii=False, indent=2)}"
            )
            response = self.llm.invoke(
                [
                    SystemMessage(content=self.generation_prompt),
                    HumanMessage(content=user_prompt),
                ]
            )
            raw_response = message_content_to_text(response)
            logger.debug("最终生成分块 %s/%s LLM 原始响应前 1000 字: %s", chunk_index, len(item_chunks), raw_response[:1000])
            try:
                generated_part = parse_generated_document_with_repair(self.llm, raw_response)
            except Exception:
                debug_path = state["output_path"].with_suffix(f".generation.chunk-{chunk_index}.raw.txt")
                debug_path.parent.mkdir(parents=True, exist_ok=True)
                debug_path.write_text(raw_response, encoding="utf-8")
                logger.exception("最终生成分块 JSON 解析和修复均失败，原始响应已保存: %s", debug_path)
                raise
            logger.info(
                "最终生成分块完成 %s/%s，段落数=%s",
                chunk_index,
                len(item_chunks),
                len(generated_part.paragraphs),
            )
            return chunk_index, generated_part

        if max_workers == 1:
            for chunk_index, item_chunk in enumerate(item_chunks, start=1):
                result_index, generated_part = generate_chunk(chunk_index, item_chunk)
                generated_parts_by_index[result_index - 1] = generated_part
        else:
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="document-generation") as executor:
                futures = [
                    executor.submit(generate_chunk, chunk_index, item_chunk)
                    for chunk_index, item_chunk in enumerate(item_chunks, start=1)
                ]
                for future in as_completed(futures):
                    result_index, generated_part = future.result()
                    generated_parts_by_index[result_index - 1] = generated_part

        generated_parts = [part for part in generated_parts_by_index if part is not None]
        generated_document = _merge_generated_documents(content_structure.title, generated_parts)
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
