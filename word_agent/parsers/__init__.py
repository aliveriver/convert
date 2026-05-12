"""多格式文档解析器模块 — 将不同格式统一为 UnifiedParagraph 中间表示。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class UnifiedParagraph:
    text: str
    format: dict = field(default_factory=dict)


def empty_format(source_type: str) -> dict:
    return {
        "style": None,
        "alignment": None,
        "font_name": None,
        "font_size_pt": None,
        "bold": False,
        "italic": False,
        "line_spacing": None,
        "space_before_pt": None,
        "space_after_pt": None,
        "first_line_indent_pt": None,
        "source_type": source_type,
        "semantic": None,
    }


class DocumentParser(ABC):
    @staticmethod
    @abstractmethod
    def supported_extensions() -> list[str]: ...

    @abstractmethod
    def parse(self, file_path: Path) -> list[UnifiedParagraph]: ...


def get_parser(file_path: Path) -> DocumentParser:
    from word_agent.parsers.docx_parser import DocxParser
    from word_agent.parsers.html_parser import HtmlParser
    from word_agent.parsers.latex_parser import LatexParser
    from word_agent.parsers.markdown_parser import MarkdownParser

    ext = file_path.suffix.lower()
    registry: dict[str, type[DocumentParser]] = {}
    for cls in [DocxParser, MarkdownParser, HtmlParser, LatexParser]:
        for supported_ext in cls.supported_extensions():
            registry[supported_ext] = cls

    if ext not in registry:
        raise ValueError(f"不支持的文件格式: {ext}")
    return registry[ext]()
