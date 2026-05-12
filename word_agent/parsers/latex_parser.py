"""LaTeX 解析器 — 基于正则提取段落、命令语义和显式格式值。"""

from __future__ import annotations

import re
from pathlib import Path

from word_agent.parsers import DocumentParser, UnifiedParagraph, empty_format

_SECTION_CMDS = {
    "chapter": 0,
    "section": 1,
    "subsection": 2,
    "subsubsection": 3,
    "paragraph": 4,
    "subparagraph": 5,
}

_HEADING_RE = re.compile(
    r"\\(chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?\{([^}]*)\}"
)
_TEXTBF_RE = re.compile(r"\\textbf\{([^}]*)\}")
_TEXTIT_RE = re.compile(r"\\textit\{([^}]*)\}")
_EMPH_RE = re.compile(r"\\emph\{([^}]*)\}")
_FONTSIZE_RE = re.compile(r"\\fontsize\{([\d.]+)\s*(pt|mm|cm)?\}")
_FONT_CMD_RE = re.compile(r"\\(tiny|scriptsize|footnotesize|small|normalsize|large|Large|LARGE|huge|Huge)\b")
_TEXTTT_RE = re.compile(r"\\texttt\{([^}]*)\}")
_COMMENT_RE = re.compile(r"(?<!\\)%.*$", re.MULTILINE)

_FONT_SIZE_MAP = {
    "tiny": 5, "scriptsize": 7, "footnotesize": 8, "small": 9,
    "normalsize": 10, "large": 12, "Large": 14.4, "LARGE": 17.3,
    "huge": 20.7, "Huge": 24.9,
}


class LatexParser(DocumentParser):
    @staticmethod
    def supported_extensions() -> list[str]:
        return [".tex"]

    def parse(self, file_path: Path) -> list[UnifiedParagraph]:
        content = file_path.read_text(encoding="utf-8")
        content = _strip_preamble(content)
        content = _COMMENT_RE.sub("", content)
        results: list[UnifiedParagraph] = []
        _parse_body(content, results)
        return results


def _strip_preamble(content: str) -> str:
    m = re.search(r"\\begin\{document\}", content)
    if m:
        content = content[m.end():]
    m = re.search(r"\\end\{document\}", content)
    if m:
        content = content[:m.start()]
    return content


def _parse_body(content: str, results: list[UnifiedParagraph]):
    env_re = re.compile(r"\\begin\{(verbatim|lstlisting|minted|itemize|enumerate)\}(.*?)\\end\{\1\}", re.DOTALL)
    last_end = 0

    for m in env_re.finditer(content):
        before = content[last_end:m.start()]
        _parse_text_blocks(before, results)
        env_name = m.group(1)
        env_body = m.group(2)

        if env_name in ("verbatim", "lstlisting", "minted"):
            text = env_body.strip()
            if text:
                fmt = empty_format("latex")
                fmt["font_name"] = "monospace"
                fmt["semantic"] = _sem(element_type="code_block", code=True)
                results.append(UnifiedParagraph(text=text, format=fmt))
        elif env_name in ("itemize", "enumerate"):
            list_type = "ordered" if env_name == "enumerate" else "unordered"
            _parse_list_env(env_body, results, list_type, 0)

        last_end = m.end()

    remaining = content[last_end:]
    _parse_text_blocks(remaining, results)


def _parse_text_blocks(text: str, results: list[UnifiedParagraph]):
    for m in _HEADING_RE.finditer(text):
        cmd = m.group(1)
        title = _clean_text(m.group(2))
        if not title:
            continue
        level = _SECTION_CMDS.get(cmd, 1) + 1
        fmt = empty_format("latex")
        fmt["bold"] = True
        font_size = _get_heading_size(level)
        if font_size:
            fmt["font_size_pt"] = font_size
        fmt["semantic"] = _sem(element_type="heading", heading_level=min(level, 6))
        results.append(UnifiedParagraph(text=title, format=fmt))

    chunks = _HEADING_RE.sub("", text)
    paragraphs = re.split(r"\n\s*\n", chunks)

    for para in paragraphs:
        para = para.strip()
        if not para or para.startswith("\\"):
            if para and not _is_skip_command(para):
                cleaned = _clean_text(para)
                if cleaned:
                    fmt = _detect_inline_format(para)
                    fmt["semantic"] = _sem(element_type="paragraph")
                    results.append(UnifiedParagraph(text=cleaned, format=fmt))
            continue
        cleaned = _clean_text(para)
        if not cleaned:
            continue
        fmt = _detect_inline_format(para)
        fmt["semantic"] = _sem(element_type="paragraph")
        results.append(UnifiedParagraph(text=cleaned, format=fmt))


def _parse_list_env(body: str, results: list[UnifiedParagraph], list_type: str, depth: int):
    items = re.split(r"\\item\b", body)
    for item_text in items[1:]:
        nested_re = re.compile(r"\\begin\{(itemize|enumerate)\}(.*?)\\end\{\1\}", re.DOTALL)
        nested_match = nested_re.search(item_text)
        if nested_match:
            before = item_text[:nested_match.start()]
            text = _clean_text(before)
            if text:
                fmt = empty_format("latex")
                fmt["semantic"] = _sem(element_type="list_item", list_type=list_type, list_depth=depth)
                results.append(UnifiedParagraph(text=text, format=fmt))
            sub_type = "ordered" if nested_match.group(1) == "enumerate" else "unordered"
            _parse_list_env(nested_match.group(2), results, sub_type, depth + 1)
        else:
            text = _clean_text(item_text)
            if text:
                fmt = empty_format("latex")
                fmt["semantic"] = _sem(element_type="list_item", list_type=list_type, list_depth=depth)
                results.append(UnifiedParagraph(text=text, format=fmt))


def _detect_inline_format(raw: str) -> dict:
    fmt = empty_format("latex")
    if _TEXTBF_RE.search(raw):
        fmt["bold"] = True
    if _TEXTIT_RE.search(raw) or _EMPH_RE.search(raw):
        fmt["italic"] = True
    if _TEXTTT_RE.search(raw):
        fmt["font_name"] = "monospace"

    m = _FONTSIZE_RE.search(raw)
    if m:
        size = float(m.group(1))
        unit = m.group(2) or "pt"
        if unit == "pt":
            fmt["font_size_pt"] = round(size, 1)
        elif unit == "mm":
            fmt["font_size_pt"] = round(size * 2.835, 1)
        elif unit == "cm":
            fmt["font_size_pt"] = round(size * 28.35, 1)

    m = _FONT_CMD_RE.search(raw)
    if m and not fmt["font_size_pt"]:
        fmt["font_size_pt"] = _FONT_SIZE_MAP.get(m.group(1))

    return fmt


def _get_heading_size(level: int) -> float | None:
    sizes = {1: 24.9, 2: 17.3, 3: 14.4, 4: 12.0, 5: 10.0, 6: 10.0}
    return sizes.get(level)


def _clean_text(text: str) -> str:
    text = _TEXTBF_RE.sub(r"\1", text)
    text = _TEXTIT_RE.sub(r"\1", text)
    text = _EMPH_RE.sub(r"\1", text)
    text = _TEXTTT_RE.sub(r"\1", text)
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?", "", text)
    text = re.sub(r"[{}]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_skip_command(text: str) -> bool:
    skip = ("\\usepackage", "\\documentclass", "\\input", "\\include",
            "\\newcommand", "\\renewcommand", "\\def", "\\let",
            "\\setlength", "\\pagestyle", "\\thispagestyle",
            "\\maketitle", "\\tableofcontents", "\\label", "\\ref",
            "\\cite", "\\bibliography", "\\bibliographystyle")
    return any(text.strip().startswith(s) for s in skip)


def _sem(element_type="paragraph", heading_level=None, list_type=None, list_depth=None, code=False):
    return {
        "element_type": element_type,
        "heading_level": heading_level,
        "list_type": list_type,
        "list_depth": list_depth,
        "code": code,
    }
