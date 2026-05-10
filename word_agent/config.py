"""配置加载辅助函数。

配置从 YAML 和环境变量读取，让提示词、密钥、模型和 provider 选择都保持在应用代码之外。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")


@dataclass(frozen=True)
class LLMSettings:
    """构建聊天模型所需的服务方中立配置。"""

    provider: str
    model: str
    api_key: str
    base_url: str | None
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class DocumentSettings:
    """DOCX 提取和写入时使用的限制与默认值。"""

    max_template_chars: int
    max_content_chars: int
    max_content_blocks: int
    content_block_text_limit: int
    content_analysis_chunk_size: int
    content_analysis_chunk_overlap: int
    generation_chunk_size: int
    template_cache_enabled: bool
    template_cache_dir: Path
    default_paragraph_style: str


@dataclass(frozen=True)
class LangSmithSettings:
    """LangSmith 追踪配置。"""

    enabled: bool
    project: str
    endpoint: str | None
    api_key: str | None
    run_name: str
    tags: list[str]


@dataclass(frozen=True)
class AppSettings:
    """CLI 和图节点使用的完整应用配置。"""

    llm: LLMSettings
    document: DocumentSettings
    langsmith: LangSmithSettings


def _expand_env(value: Any) -> Any:
    """展开 YAML 配置值中的 ${VAR} 和 ${VAR:-default} 字符串。"""

    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            env_value = os.getenv(name)
            if env_value is not None:
                return env_value
            if default is not None:
                return default
            return ""

        return ENV_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    return value


def _to_bool(value: Any) -> bool:
    """将 YAML 或环境变量里的布尔值转成 bool。"""

    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_settings(config_path: Path = Path("config/settings.yaml")) -> AppSettings:
    """将 `.env` 和 YAML 配置加载为类型化数据类。"""

    load_dotenv()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data = _expand_env(raw)

    provider = data["llm"]["provider"].lower()
    if provider not in {"openai", "anthropic"}:
        raise ValueError("llm.provider 必须是 'openai' 或 'anthropic'")

    provider_data = data[provider]
    api_key = provider_data.get("api_key", "")
    if not api_key:
        raise ValueError(f"缺少 provider 的 API key: {provider}")

    llm_settings = LLMSettings(
        provider=provider,
        model=provider_data["model"],
        api_key=api_key,
        base_url=provider_data.get("base_url"),
        temperature=float(data["llm"].get("temperature", 0.2)),
        max_tokens=int(data["llm"].get("max_tokens", 4096)),
    )
    document_settings = DocumentSettings(
        max_template_chars=int(data["document"].get("max_template_chars", 0)),
        max_content_chars=int(data["document"].get("max_content_chars", 0)),
        max_content_blocks=int(data["document"].get("max_content_blocks", 0)),
        content_block_text_limit=int(data["document"].get("content_block_text_limit", 0)),
        content_analysis_chunk_size=int(data["document"].get("content_analysis_chunk_size", 32)),
        content_analysis_chunk_overlap=int(data["document"].get("content_analysis_chunk_overlap", 3)),
        generation_chunk_size=int(data["document"].get("generation_chunk_size", 24)),
        template_cache_enabled=_to_bool(data["document"].get("template_cache_enabled", True)),
        template_cache_dir=Path(data["document"].get("template_cache_dir", "out/cache")),
        default_paragraph_style=data["document"].get("default_paragraph_style", "Normal"),
    )
    langsmith_data = data.get("langsmith", {})
    tags = langsmith_data.get("tags", [])
    if isinstance(tags, str):
        tags = [item.strip() for item in tags.split(",") if item.strip()]
    langsmith_settings = LangSmithSettings(
        enabled=_to_bool(langsmith_data.get("enabled", False)),
        project=langsmith_data.get("project", "word-template-agent"),
        endpoint=langsmith_data.get("endpoint") or None,
        api_key=langsmith_data.get("api_key") or None,
        run_name=langsmith_data.get("run_name", "word_generation"),
        tags=list(tags),
    )
    return AppSettings(llm=llm_settings, document=document_settings, langsmith=langsmith_settings)


def read_prompt(path: Path) -> str:
    """以 UTF-8 文本读取外部提示词文件。"""

    return path.read_text(encoding="utf-8").strip()
