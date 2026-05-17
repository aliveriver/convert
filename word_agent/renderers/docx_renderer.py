"""DOCX 渲染器 — 将 GeneratedDocument 写入 Word 文档。"""

from __future__ import annotations

from pathlib import Path

from word_agent.models import GeneratedDocument, ROLE_TO_DOCX_STYLE
from word_agent.renderers import DocumentRenderer


class DocxRenderer(DocumentRenderer):
    def __init__(self, fallback_style: str = "Normal") -> None:
        self.fallback_style = fallback_style

    def render(
        self,
        document: GeneratedDocument,
        output_path: Path,
        template_path: Path | None = None,
    ) -> Path:
        from word_agent.docx_io import write_generated_docx

        if template_path is None:
            raise ValueError("DOCX 渲染需要提供模板文件路径")

        return write_generated_docx(
            template_path=template_path,
            output_path=output_path,
            generated=document,
            fallback_style=self.fallback_style,
        )
