"""LLM 服务方构建与 JSON 响应解析。"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from word_agent.config import LLMSettings
from word_agent.models import ContentStructure, GeneratedDocument


ModelT = TypeVar("ModelT", bound=BaseModel)
logger = logging.getLogger(__name__)


def build_chat_model(settings: LLMSettings) -> BaseChatModel:
    """为 OpenAI 兼容接口或 Anthropic API 创建 LangChain 聊天模型。"""

    logger.info(
        "创建聊天模型: provider=%s model=%s base_url=%s temperature=%s max_tokens=%s",
        settings.provider,
        settings.model,
        settings.base_url,
        settings.temperature,
        settings.max_tokens,
    )
    if settings.provider == "openai":
        return ChatOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            model=settings.model,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
        )
    if settings.provider == "anthropic":
        return ChatAnthropic(
            api_key=settings.api_key,
            base_url=settings.base_url,
            model=settings.model,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
        )
    raise ValueError(f"不支持的 provider: {settings.provider}")


def message_content_to_text(message: BaseMessage) -> str:
    """将不同 provider 的消息内容规范化为纯文本。"""

    content = message.content
    if isinstance(content, str):
        return content
    text_parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            text_parts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            text_parts.append(item["text"])
    return "\n".join(text_parts)


def _extract_first_json_object(raw_text: str) -> str:
    """从模型输出中按括号栈提取第一个完整 JSON 对象。"""

    start = raw_text.find("{")
    if start < 0:
        raise json.JSONDecodeError("未找到 JSON 对象起始符", raw_text, 0)

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw_text)):
        char = raw_text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw_text[start : index + 1]

    raise json.JSONDecodeError("JSON 对象括号未闭合", raw_text, start)


def parse_json_model(raw_text: str, model_type: type[ModelT]) -> ModelT:
    """解析模型返回的 JSON 载荷，并用 Pydantic 模型校验。"""

    try:
        payload: Any = json.loads(raw_text)
    except json.JSONDecodeError:
        payload = json.loads(_extract_first_json_object(raw_text))
    return model_type.model_validate(payload)


def repair_and_parse_json_model(
    llm: BaseChatModel,
    raw_text: str,
    model_type: type[ModelT],
    task_name: str,
) -> ModelT:
    """解析失败时调用 LLM 修复 JSON，并再次用 Pydantic 模型校验。"""

    schema = json.dumps(model_type.model_json_schema(), ensure_ascii=False, indent=2)
    logger.warning("%s JSON 解析失败，准备调用 LLM 进行一次 JSON 修复", task_name)
    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "你是严格的 JSON 修复器。只输出合法 JSON，不要使用 Markdown。"
                    "不要改写文本含义，不要补充新事实，只修复引号、逗号、转义、括号和字段结构。"
                )
            ),
            HumanMessage(
                content=(
                    f"目标 JSON schema：\n{schema}\n\n"
                    f"需要修复的模型输出如下：\n{raw_text}"
                )
            ),
        ]
    )
    repaired_text = message_content_to_text(response)
    logger.info("%s JSON 修复完成，修复后字符数=%s", task_name, len(repaired_text))
    return parse_json_model(repaired_text, model_type)


def parse_content_structure(raw_text: str) -> ContentStructure:
    """解析内容子 agent 返回的内容结构 JSON。"""

    return parse_json_model(raw_text, ContentStructure)


def parse_content_structure_with_repair(llm: BaseChatModel, raw_text: str) -> ContentStructure:
    """解析内容结构 JSON，失败时自动修复一次。"""

    try:
        return parse_content_structure(raw_text)
    except Exception as exc:
        logger.warning("内容结构 JSON 首次解析失败: %s", exc)
        return repair_and_parse_json_model(llm, raw_text, ContentStructure, "内容结构")


def parse_generated_document(raw_text: str) -> GeneratedDocument:
    """解析模型返回的文档 JSON 载荷，并校验其结构。"""

    return parse_json_model(raw_text, GeneratedDocument)


def parse_generated_document_with_repair(llm: BaseChatModel, raw_text: str) -> GeneratedDocument:
    """解析生成文档 JSON，失败时自动修复一次。"""

    try:
        return parse_generated_document(raw_text)
    except Exception as exc:
        logger.warning("生成文档 JSON 首次解析失败: %s", exc)
        return repair_and_parse_json_model(llm, raw_text, GeneratedDocument, "生成文档")
