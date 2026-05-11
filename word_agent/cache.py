"""Local JSON cache helpers for expensive document analysis steps.

This module keeps cache key generation and disk IO away from agent logic.
Use `TemplateAnalysisCache` with a template path plus prompt/model settings to
reuse stable template-analysis results across repeated runs.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = "template-analysis-v1"


class TemplateAnalysisCache:
    """Stores and loads template-analysis payloads keyed by file and prompt hashes."""

    def __init__(self, cache_dir: Path, enabled: bool) -> None:
        self.cache_dir = cache_dir
        self.enabled = enabled

    def build_key(
        self,
        template_path: Path,
        prompt: str,
        provider: str,
        model: str,
        max_template_chars: int,
    ) -> str:
        """Return a stable hash for the template, prompt, model, and extraction settings."""

        digest = hashlib.sha256()
        digest.update(CACHE_SCHEMA_VERSION.encode("utf-8"))
        digest.update(str(template_path.resolve()).encode("utf-8"))
        digest.update(provider.encode("utf-8"))
        digest.update(model.encode("utf-8"))
        digest.update(str(max_template_chars).encode("utf-8"))
        digest.update(prompt.encode("utf-8"))
        with template_path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def load(self, key: str) -> dict[str, Any] | None:
        """Load cached JSON if caching is enabled and the cache file exists."""

        if not self.enabled:
            return None
        cache_path = self._cache_path(key)
        if not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("模板分析缓存读取失败，将重新分析: %s", exc)
            return None
        logger.info("命中模板分析缓存: %s", cache_path)
        return payload

    def save(self, key: str, payload: dict[str, Any]) -> Path | None:
        """Persist JSON cache and return the written path when caching is enabled."""

        if not self.enabled:
            return None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self._cache_path(key)
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("写入模板分析缓存: %s", cache_path)
        return cache_path

    def _cache_path(self, key: str) -> Path:
        """Return the JSON file path for a cache key."""

        return self.cache_dir / f"template_analysis_{key}.json"
