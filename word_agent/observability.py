"""日志与 LangSmith 追踪配置。"""

from __future__ import annotations

import logging
import os

from word_agent.config import LangSmithSettings

logger = logging.getLogger(__name__)


def configure_langsmith(settings: LangSmithSettings) -> None:
    """根据配置启用或关闭 LangSmith 追踪环境变量。"""

    if not settings.enabled:
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        logger.info("LangSmith 追踪未启用")
        return

    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_PROJECT"] = settings.project
    os.environ["LANGCHAIN_PROJECT"] = settings.project
    if settings.endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = settings.endpoint
    if settings.api_key:
        os.environ["LANGSMITH_API_KEY"] = settings.api_key

    if settings.api_key:
        logger.info("LangSmith 追踪已启用，project=%s", settings.project)
    else:
        logger.warning("LangSmith 追踪已启用，但未配置 LANGSMITH_API_KEY")
