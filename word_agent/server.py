"""FastAPI Web 服务，将 Word 模板格式化功能暴露为 HTTP API。"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from word_agent.config import load_settings
from word_agent.graph import WordAgent
from word_agent.llm import build_chat_model
from word_agent.observability import configure_langsmith
from word_agent.compare_util import get_doc_diff

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
FILE_TTL = 600  # 生成文件保留 10 分钟
DAILY_CONVERT_LIMIT = 100  # 全局每日转换上限
MAX_CONCURRENT = 5  # 最大同时转换任务数
MAX_STORAGE_BYTES = 1024 * 1024 * 1024 * 5  # 临时文件总量上限 5GB

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Word Template Converter", version="1.0.0")
app.state.limiter = limiter

# 存储已生成文件的元信息: {file_id: {"path": Path, "created_at": float}}
_file_store: dict[str, dict] = {}
_store_lock = threading.Lock()
_daily_count = 0
_daily_reset_date = time.strftime("%Y-%m-%d")
_concurrent_sem = threading.Semaphore(MAX_CONCURRENT)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载前端静态文件
app.mount("/frontend", StaticFiles(directory="frontend", html=True), name="frontend")


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "请求过于频繁，请稍后再试（每分钟最多 3 次）"},
    )


settings = load_settings(Path("config/settings.yaml"))
configure_langsmith(settings.langsmith)
llm = build_chat_model(settings.llm)
agent = WordAgent(llm=llm, settings=settings)

@app.get("/")
async def root():
    return RedirectResponse(url="/frontend/index.html")

@app.get("/api/health")
async def health():
    return {"status": "ok"}

@app.post("/api/compare")
@limiter.limit("5/minute")
async def compare_docs(
    request: Request,
    doc1: UploadFile = File(..., description="First .docx file"),
    doc2: UploadFile = File(..., description="Second .docx file"),
):
    for f, name in [(doc1, "doc1"), (doc2, "doc2")]:
        if not f.filename or not f.filename.endswith(".docx"):
            return JSONResponse(status_code=400, content={"detail": f"{name} 必须是 .docx 文件"})

    tmp_dir = tempfile.mkdtemp(prefix="word_compare_")
    try:
        p1 = Path(tmp_dir) / "doc1.docx"
        p2 = Path(tmp_dir) / "doc2.docx"

        for upload, dest in [(doc1, p1), (doc2, p2)]:
            data = await upload.read()
            if len(data) > MAX_FILE_SIZE:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"文件 {upload.filename} 超过 10MB 限制"},
                )
            dest.write_bytes(data)

        logger.info("开始对比: doc1=%s doc2=%s", doc1.filename, doc2.filename)
        diff_results = get_doc_diff(p1, p2)

        return JSONResponse(content={"comparison": diff_results})
    except Exception as e:
        logger.exception("对比失败")
        return JSONResponse(status_code=500, content={"detail": f"对比失败: {str(e)}"})
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.post("/api/convert")
@limiter.limit("3/minute")
async def convert(
    request: Request,
    template: UploadFile = File(..., description="模板 .docx 文件"),
    content: UploadFile = File(..., description="内容 .docx 文件"),
):
    global _daily_count, _daily_reset_date

    today = time.strftime("%Y-%m-%d")
    if today != _daily_reset_date:
        _daily_count = 0
        _daily_reset_date = today

    if _daily_count >= DAILY_CONVERT_LIMIT:
        return JSONResponse(status_code=429, content={"detail": f"今日转换次数已达上限（{DAILY_CONVERT_LIMIT}次），请明天再试"})

    if not _concurrent_sem.acquire(blocking=False):
        return JSONResponse(status_code=503, content={"detail": "服务器繁忙，请稍后再试"})

    try:
        for f, name in [(template, "template"), (content, "content")]:
            if not f.filename or not f.filename.endswith(".docx"):
                return JSONResponse(status_code=400, content={"detail": f"{name} 必须是 .docx 文件"})

        tmp_dir = tempfile.mkdtemp(prefix="word_convert_")
        try:
            template_path = Path(tmp_dir) / "template.docx"
            content_path = Path(tmp_dir) / "content.docx"
            output_path = Path(tmp_dir) / "output.docx"

            for upload, dest in [(template, template_path), (content, content_path)]:
                data = await upload.read()
                if len(data) > MAX_FILE_SIZE:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"文件 {upload.filename} 超过 10MB 限制"},
                    )
                dest.write_bytes(data)

            logger.info("开始转换: template=%s content=%s", template.filename, content.filename)
            agent.run(
                template_path=template_path,
                content_path=content_path,
                output_path=output_path,
            )

            _daily_count += 1
            file_id = uuid.uuid4().hex
            with _store_lock:
                _file_store[file_id] = {"path": output_path, "tmp_dir": tmp_dir, "created_at": time.time()}

            return JSONResponse(content={
                "file_id": file_id,
                "download_url": f"/api/download/{file_id}",
                "daily_remaining": DAILY_CONVERT_LIMIT - _daily_count,
            })
        except Exception as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            logger.exception("转换失败")
            return JSONResponse(status_code=500, content={"detail": f"转换失败: {str(e)}"})
    finally:
        _concurrent_sem.release()


@app.get("/api/download/{file_id}")
async def download(file_id: str):
    with _store_lock:
        entry = _file_store.get(file_id)

    if not entry:
        return JSONResponse(status_code=404, content={"detail": "文件不存在或已过期"})

    if time.time() - entry["created_at"] > FILE_TTL:
        _remove_file(file_id)
        return JSONResponse(status_code=410, content={"detail": "文件已过期，请重新转换"})

    return FileResponse(
        path=str(entry["path"]),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="converted.docx",
    )


def _remove_file(file_id: str):
    with _store_lock:
        entry = _file_store.pop(file_id, None)
    if entry:
        shutil.rmtree(entry["tmp_dir"], ignore_errors=True)


def _cleanup_expired():
    """后台线程定期清理过期文件。"""
    while True:
        time.sleep(60)
        now = time.time()
        with _store_lock:
            expired = [fid for fid, e in _file_store.items() if now - e["created_at"] > FILE_TTL]
        for fid in expired:
            _remove_file(fid)
            logger.info("已清理过期文件: %s", fid)


_cleanup_thread = threading.Thread(target=_cleanup_expired, daemon=True)
_cleanup_thread.start()
