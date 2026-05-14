"""文档输出渲染器模块 — 将 GeneratedDocument 渲染为不同格式。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from word_agent.models import GeneratedDocument


class DocumentRenderer(ABC):
    @abstractmethod
    def render(
        self,
        document: GeneratedDocument,
        output_path: Path,
        template_path: Path | None = None,
    ) -> Path:
        """将 GeneratedDocument 渲染为目标格式并写入 output_path。"""
        ...


def get_renderer(output_path: Path) -> DocumentRenderer:
    """根据输出文件扩展名选择对应的 renderer。"""

    from word_agent.renderers.docx_renderer import DocxRenderer
    from word_agent.renderers.latex_renderer import LatexRenderer
    from word_agent.renderers.markdown_renderer import MarkdownRenderer

    ext = output_path.suffix.lower()
    registry: dict[str, type[DocumentRenderer]] = {
        ".docx": DocxRenderer,
        ".tex": LatexRenderer,
        ".latex": LatexRenderer,
        ".md": MarkdownRenderer,
        ".markdown": MarkdownRenderer,
    }

    if ext not in registry:
        raise ValueError(f"不支持的输出格式: {ext}")
    return registry[ext]()
