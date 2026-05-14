"""文档格式转换 agent 的命令行入口。

支持 .docx / .tex / .md 格式的任意组合输入输出。

示例：
    python -m word_agent.cli --template template.docx --content content.md --output out/result.tex
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from word_agent.models import SUPPORTED_INPUT_EXTENSIONS, SUPPORTED_OUTPUT_EXTENSIONS


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""

    parser = argparse.ArgumentParser(
        description="根据模板和内容文档生成新文档，支持 docx/tex/md 格式自由组合。"
    )
    parser.add_argument("--template", required=True, type=Path, help="模板文件 (.docx/.tex/.md)")
    parser.add_argument("--content", required=True, type=Path, help="内容文件 (.docx/.tex/.md)")
    parser.add_argument("--output", required=True, type=Path, help="输出路径（扩展名决定输出格式: .docx/.tex/.md）")
    parser.add_argument("--config", default=Path("config/settings.yaml"), type=Path, help="YAML 配置路径")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="运行日志级别",
    )
    parser.add_argument(
        "--debug-graph",
        type=Path,
        help="导出 LangGraph Mermaid 可视化调试文件的路径前缀",
    )
    parser.add_argument(
        "--debug-graph-png",
        action="store_true",
        help="同时尝试导出 LangGraph PNG 图",
    )
    return parser


def configure_logging(level: str) -> None:
    """配置命令行日志输出。"""

    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    """根据命令行参数运行文档生成流程。"""

    args = build_parser().parse_args()
    configure_logging(args.log_level)

    template_ext = args.template.suffix.lower()
    content_ext = args.content.suffix.lower()
    output_ext = args.output.suffix.lower()

    if template_ext not in SUPPORTED_INPUT_EXTENSIONS:
        raise SystemExit(f"模板格式不支持: {template_ext}，支持: {', '.join(sorted(SUPPORTED_INPUT_EXTENSIONS))}")
    if content_ext not in SUPPORTED_INPUT_EXTENSIONS:
        raise SystemExit(f"内容格式不支持: {content_ext}，支持: {', '.join(sorted(SUPPORTED_INPUT_EXTENSIONS))}")
    if output_ext not in SUPPORTED_OUTPUT_EXTENSIONS:
        raise SystemExit(f"输出格式不支持: {output_ext}，支持: {', '.join(sorted(SUPPORTED_OUTPUT_EXTENSIONS))}")

    from word_agent.config import load_settings
    from word_agent.observability import configure_langsmith

    logging.info("加载配置: %s", args.config)
    settings = load_settings(args.config)
    configure_langsmith(settings.langsmith)

    from word_agent.graph import WordAgent
    from word_agent.llm import build_chat_model

    logging.info("初始化 LLM provider=%s model=%s", settings.llm.provider, settings.llm.model)
    llm = build_chat_model(settings.llm)
    agent = WordAgent(llm=llm, settings=settings)
    if args.debug_graph is not None:
        debug_paths = agent.save_graph_debug(args.debug_graph, include_png=args.debug_graph_png)
        for kind, path in debug_paths.items():
            logging.info("已导出 LangGraph %s: %s", kind, path)

    logging.info("开始文档转换: %s + %s -> %s", template_ext, content_ext, output_ext)
    final_state = agent.run(
        template_path=args.template,
        content_path=args.content,
        output_path=args.output,
    )
    print(f"已生成: {final_state['written_path']}")


if __name__ == "__main__":
    main()
