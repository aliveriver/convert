"""LaTeX 渲染器 — 将 GeneratedDocument 写入 .tex 文件。"""

from __future__ import annotations

from pathlib import Path

from word_agent.models import GeneratedDocument, GeneratedParagraph
from word_agent.renderers import DocumentRenderer

_ROLE_TO_LATEX: dict[str, tuple[str, str]] = {
    "title": ("\\title{", "}\n\\maketitle\n"),
    "heading_1": ("\\section{", "}\n"),
    "heading_2": ("\\subsection{", "}\n"),
    "heading_3": ("\\subsubsection{", "}\n"),
    "body": ("", "\n\n"),
    "list_bullet": ("  \\item ", "\n"),
    "list_number": ("  \\item ", "\n"),
    "table": ("", "\n\n"),
    "code_block": ("\\begin{lstlisting}\n", "\n\\end{lstlisting}\n"),
    "blockquote": ("\\begin{quote}\n", "\n\\end{quote}\n"),
}

_PREAMBLE = r"""\documentclass[12pt,a4paper]{article}
\usepackage[UTF8]{ctex}
\usepackage{geometry}
\usepackage{listings}
\usepackage{graphicx}
\geometry{left=2.5cm,right=2.5cm,top=2.5cm,bottom=2.5cm}

"""


def _escape_latex(text: str) -> str:
    """转义 LaTeX 特殊字符（不处理 code_block 内容）。"""

    replacements = [
        ("\\", "\\textbackslash{}"),
        ("&", "\\&"),
        ("%", "\\%"),
        ("$", "\\$"),
        ("#", "\\#"),
        ("_", "\\_"),
        ("{", "\\{"),
        ("}", "\\}"),
        ("~", "\\textasciitilde{}"),
        ("^", "\\textasciicircum{}"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


class LatexRenderer(DocumentRenderer):
    def render(
        self,
        document: GeneratedDocument,
        output_path: Path,
        template_path: Path | None = None,
    ) -> Path:
        lines: list[str] = [_PREAMBLE]
        lines.append("\\begin{document}\n\n")

        in_itemize = False
        in_enumerate = False

        for para in document.paragraphs:
            role = para.role

            if role != "list_bullet" and in_itemize:
                lines.append("\\end{itemize}\n\n")
                in_itemize = False
            if role != "list_number" and in_enumerate:
                lines.append("\\end{enumerate}\n\n")
                in_enumerate = False

            if role == "list_bullet" and not in_itemize:
                lines.append("\\begin{itemize}\n")
                in_itemize = True
            elif role == "list_number" and not in_enumerate:
                lines.append("\\begin{enumerate}\n")
                in_enumerate = True

            prefix, suffix = _ROLE_TO_LATEX.get(role, ("", "\n\n"))

            if role == "code_block":
                lines.append(f"{prefix}{para.text}{suffix}")
            else:
                lines.append(f"{prefix}{_escape_latex(para.text)}{suffix}")

        if in_itemize:
            lines.append("\\end{itemize}\n\n")
        if in_enumerate:
            lines.append("\\end{enumerate}\n\n")

        lines.append("\\end{document}\n")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("".join(lines), encoding="utf-8")
        return output_path
