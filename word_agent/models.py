"""Word 生成工作流共享数据模型。

本模块集中维护 LangGraph 状态和 LLM 文档输出 schema，让 CLI、graph 节点和
DOCX 写入器依赖同一组契约。
"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from pydantic import BaseModel, Field


class ContentStructureItem(BaseModel):
    """LLM 对非规整内容文档中单个源文本块的判断。"""

    source_index: int = Field(description="提取出的内容块索引")
    role: str = Field(description="title | heading_1 | heading_2 | heading_3 | body | list_item | table | note")
    text: str = Field(description="该结构项对应的源文本")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(default="", description="该判断的简短依据")


class ContentStructure(BaseModel):
    """用户内容文档的结构化解读。"""

    title: str = Field(default="", description="可能的文档标题")
    items: list[ContentStructureItem] = Field(default_factory=list)
    must_keep_facts: list[str] = Field(default_factory=list)
    uncertain_items: list[str] = Field(default_factory=list)


class GeneratedParagraph(BaseModel):
    """要写入输出 DOCX 的单个段落。"""

    style: str = Field(default="Normal", description="Word 段落样式名称")
    text: str = Field(description="可见段落文本")


class GeneratedDocument(BaseModel):
    """DOCX 渲染前由 LLM 返回的结构化文档。"""

    title: str = Field(default="", description="文档标题")
    paragraphs: list[GeneratedParagraph] = Field(default_factory=list)


class AgentState(TypedDict, total=False):
    """在 LangGraph 节点之间传递的可变状态。"""

    template_path: Path
    content_path: Path
    output_path: Path
    template_profile: dict
    content_profile: dict
    format_requirements: str
    content_analysis: str
    content_block_count: int
    content_analysis_chunk_count: int
    content_structure: ContentStructure
    generated_document: GeneratedDocument
    written_path: Path
