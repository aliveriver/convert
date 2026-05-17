"""Markdown 渲染器 — 将 GeneratedDocument 写入 .md 文件。"""

from __future__ import annotations

from pathlib import Path

from word_agent.models import GeneratedDocument
from word_agent.renderers import DocumentRenderer

_ROLE_TO_MD_PREFIX: dict[str, str] = {
    "title": "# ",
    "heading_1": "## ",
    "heading_2": "### ",
    "heading_3": "#### ",
    "body": "",
    "list_bullet": "- ",
    "list_number": "1. ",
    "table": "",
    "code_block": "",
    "blockquote": "> ",
}


class MarkdownRenderer(DocumentRenderer):
    def render(
        self,
        document: GeneratedDocument,
        output_path: Path,
        template_path: Path | None = None,
    ) -> Path:
        lines: list[str] = []

        for para in document.paragraphs:
            role = para.role
            text = para.text

            if role == "code_block":
                lines.append(f"```\n{text}\n```")
            else:
                prefix = _ROLE_TO_MD_PREFIX.get(role, "")
                lines.append(f"{prefix}{text}")

            lines.append("")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return output_path
