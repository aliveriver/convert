"""FastAPI Web 服务，将 Word 模板格式化功能暴露为 HTTP API。"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from queue import Empty, Queue

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from word_agent.config import load_settings
from word_agent.graph import WordAgent
from word_agent.llm import build_chat_model
from word_agent.models import SUPPORTED_INPUT_EXTENSIONS, SUPPORTED_OUTPUT_EXTENSIONS
from word_agent.observability import configure_langsmith
from word_agent.compare_util import get_doc_diff, get_doc_diff_universal
from word_agent.format_convert import format_convert, SUPPORTED_INPUT_EXTS, SUPPORTED_OUTPUT_EXTS

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

SSE_KEEPALIVE_INTERVAL = 15  # SSE 保活间隔（秒）
SSE_TIMEOUT = 90  # SSE 连接超时（秒），超过此时间无消息则认为断开

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
logger.info("服务初始化完成: llm=%s, max_concurrent=%d, daily_limit=%d, file_ttl=%ds",
            settings.llm.model if hasattr(settings.llm, 'model') else 'unknown',
            MAX_CONCURRENT, DAILY_CONVERT_LIMIT, FILE_TTL)

@app.get("/")
async def root():
    return RedirectResponse(url="/frontend/index.html")

@app.get("/convert")
async def page_convert():
    return RedirectResponse(url="/frontend/index.html")

@app.get("/compare")
async def page_compare():
    return RedirectResponse(url="/frontend/compare.html")

@app.get("/format-convert")
async def page_format_convert():
    return RedirectResponse(url="/frontend/format_convert.html")

@app.get("/api/health")
async def health():
    with _store_lock:
        active_tasks = len(_file_store)
    logger.debug("健康检查: active_tasks=%d, daily_count=%d", active_tasks, _daily_count)
    return {"status": "ok", "active_tasks": active_tasks, "daily_count": _daily_count}

SUPPORTED_COMPARE_EXTS = {".docx", ".md", ".markdown", ".html", ".htm", ".tex"}


@app.post("/api/compare")
@limiter.limit("5/minute")
async def compare_docs(
    request: Request,
    doc1: UploadFile = File(..., description="First document file"),
    doc2: UploadFile = File(..., description="Second document file"),
):
    for f, name in [(doc1, "doc1"), (doc2, "doc2")]:
        if not f.filename:
            return JSONResponse(status_code=400, content={"detail": f"{name} 文件名为空"})
        ext = Path(f.filename).suffix.lower()
        if ext not in SUPPORTED_COMPARE_EXTS:
            return JSONResponse(
                status_code=400,
                content={"detail": f"{name} 不支持的格式 '{ext}'，支持: {', '.join(sorted(SUPPORTED_COMPARE_EXTS))}"},
            )

    tmp_dir = tempfile.mkdtemp(prefix="word_compare_")
    logger.info("对比请求: doc1=%s(%d bytes) doc2=%s(%d bytes) tmp=%s",
                doc1.filename, doc1.size or 0, doc2.filename, doc2.size or 0, tmp_dir)
    try:
        ext1 = Path(doc1.filename).suffix.lower()
        ext2 = Path(doc2.filename).suffix.lower()
        p1 = Path(tmp_dir) / f"doc1{ext1}"
        p2 = Path(tmp_dir) / f"doc2{ext2}"

        for upload, dest in [(doc1, p1), (doc2, p2)]:
            data = await upload.read()
            if len(data) > MAX_FILE_SIZE:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"文件 {upload.filename} 超过 10MB 限制"},
                )
            dest.write_bytes(data)

        logger.info("开始对比: doc1=%s doc2=%s", doc1.filename, doc2.filename)
        diff_results = get_doc_diff_universal(p1, p2)
        logger.info("对比完成: doc1=%s doc2=%s, 差异数=%d", doc1.filename, doc2.filename, len(diff_results) if isinstance(diff_results, list) else 0)

        return JSONResponse(content={"comparison": diff_results})
    except Exception as e:
        logger.exception("对比失败")
        return JSONResponse(status_code=500, content={"detail": f"对比失败: {str(e)}"})
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.post("/api/format-convert")
@limiter.limit("10/minute")
async def api_format_convert(
    request: Request,
    file: UploadFile = File(..., description="待转换文件"),
    target_format: str = Form("md", description="目标格式: docx/md/tex"),
):
    if not file.filename:
        return JSONResponse(status_code=400, content={"detail": "文件名为空"})

    input_ext = Path(file.filename).suffix.lower()
    if input_ext not in SUPPORTED_INPUT_EXTS:
        return JSONResponse(
            status_code=400,
            content={"detail": f"不支持的输入格式 '{input_ext}'，支持: {', '.join(sorted(SUPPORTED_INPUT_EXTS))}"},
        )

    target_format = target_format.lower().strip(".")
    if f".{target_format}" not in SUPPORTED_OUTPUT_EXTS:
        return JSONResponse(
            status_code=400,
            content={"detail": f"不支持的目标格式 '{target_format}'，支持: docx, md, tex"},
        )

    target_ext = f".{target_format}"
    if input_ext in (".md", ".markdown") and target_ext == ".md":
        return JSONResponse(status_code=400, content={"detail": "输入格式与目标格式相同"})
    if input_ext in (".tex", ".latex") and target_ext == ".tex":
        return JSONResponse(status_code=400, content={"detail": "输入格式与目标格式相同"})
    if input_ext == ".docx" and target_ext == ".docx":
        return JSONResponse(status_code=400, content={"detail": "输入格式与目标格式相同"})

    tmp_dir = tempfile.mkdtemp(prefix="word_fmt_convert_")
    logger.info("格式转换请求: file=%s target=%s tmp=%s", file.filename, target_format, tmp_dir)

    try:
        input_path = Path(tmp_dir) / f"input{input_ext}"
        data = await file.read()
        if len(data) > MAX_FILE_SIZE:
            return JSONResponse(status_code=413, content={"detail": f"文件超过 10MB 限制"})
        input_path.write_bytes(data)

        output_path = format_convert(input_path, target_format)

        file_id = str(uuid.uuid4())
        with _store_lock:
            _file_store[file_id] = {
                "path": output_path,
                "created_at": time.time(),
                "tmp_dir": tmp_dir,
            }

        logger.info("格式转换完成: file_id=%s output=%s", file_id[:8], output_path.name)
        return JSONResponse(content={
            "file_id": file_id,
            "filename": output_path.name,
            "download_url": f"/api/download/{file_id}",
        })
    except Exception as e:
        logger.exception("格式转换失败")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return JSONResponse(status_code=500, content={"detail": f"转换失败: {str(e)}"})


@app.post("/api/convert")
@limiter.limit("3/minute")
async def convert(
    request: Request,
    template: UploadFile = File(..., description="模板文件 (.docx/.tex/.md)"),
    content: UploadFile = File(..., description="内容文件 (.docx/.tex/.md)"),
    output_format: str | None = None,
):
    global _daily_count, _daily_reset_date

    today = time.strftime("%Y-%m-%d")
    if today != _daily_reset_date:
        logger.info("日期切换，重置每日计数: %s -> %s", _daily_reset_date, today)
        _daily_count = 0
        _daily_reset_date = today

    if _daily_count >= DAILY_CONVERT_LIMIT:
        logger.warning("每日转换上限已达: %d/%d", _daily_count, DAILY_CONVERT_LIMIT)
        return JSONResponse(status_code=429, content={"detail": f"今日转换次数已达上限（{DAILY_CONVERT_LIMIT}次），请明天再试"})

    if not _concurrent_sem.acquire(blocking=False):
        logger.warning("并发上限，拒绝请求: template=%s content=%s", template.filename, content.filename)
        return JSONResponse(status_code=503, content={"detail": "服务器繁忙，请稍后再试"})

    for f, name in [(template, "template"), (content, "content")]:
        if not f.filename:
            logger.warning("转换请求参数错误: %s 文件名为空", name)
            _concurrent_sem.release()
            return JSONResponse(status_code=400, content={"detail": f"{name} 文件名为空"})
        ext = Path(f.filename).suffix.lower()
        if ext not in SUPPORTED_INPUT_EXTENSIONS:
            logger.warning("转换请求参数错误: %s 格式不支持=%s", name, ext)
            _concurrent_sem.release()
            return JSONResponse(
                status_code=400,
                content={"detail": f"{name} 不支持的格式 '{ext}'，支持: {', '.join(sorted(SUPPORTED_INPUT_EXTENSIONS))}"},
            )

    template_ext = Path(template.filename).suffix.lower()
    if output_format:
        out_ext = output_format if output_format.startswith(".") else f".{output_format}"
    else:
        out_ext = template_ext if template_ext in SUPPORTED_OUTPUT_EXTENSIONS else ".docx"

    if out_ext not in SUPPORTED_OUTPUT_EXTENSIONS:
        _concurrent_sem.release()
        return JSONResponse(
            status_code=400,
            content={"detail": f"不支持的输出格式 '{out_ext}'，支持: {', '.join(sorted(SUPPORTED_OUTPUT_EXTENSIONS))}"},
        )

    tmp_dir = tempfile.mkdtemp(prefix="word_convert_")
    template_path = Path(tmp_dir) / f"template{template_ext}"
    content_ext = Path(content.filename).suffix.lower()
    content_path = Path(tmp_dir) / f"content{content_ext}"
    output_path = Path(tmp_dir) / f"output{out_ext}"
    logger.info("转换请求接收: template=%s content=%s output_format=%s tmp=%s",
                template.filename, content.filename, out_ext, tmp_dir)

    for upload, dest in [(template, template_path), (content, content_path)]:
        data = await upload.read()
        if len(data) > MAX_FILE_SIZE:
            logger.warning("文件过大: %s size=%d bytes", upload.filename, len(data))
            _concurrent_sem.release()
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return JSONResponse(
                status_code=413,
                content={"detail": f"文件 {upload.filename} 超过 10MB 限制"},
            )
        dest.write_bytes(data)
        logger.debug("文件写入: %s -> %s (%d bytes)", upload.filename, dest, len(data))

    file_id = uuid.uuid4().hex
    log_path = Path(tmp_dir) / "convert.log"
    log_path.touch()
    logger.info("分配 file_id=%s, template=%s content=%s", file_id, template.filename, content.filename)

    with _store_lock:
        _file_store[file_id] = {"path": None, "log_path": log_path, "tmp_dir": tmp_dir, "created_at": time.time()}

    def event_stream():
        progress_queue: Queue = Queue()
        sse_start_time = time.time()
        keepalive_count = 0
        msg_count = 0
        last_msg_time = time.time()

        def progress_cb(step, data):
            progress_queue.put({"step": step, **data})

        def run_agent():
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s", datefmt="%H:%M:%S"
            ))
            wa_logger = logging.getLogger("word_agent")
            wa_logger.addHandler(file_handler)
            try:
                logger.info("[%s] agent.run 开始执行", file_id[:8])
                agent.run(
                    template_path=template_path,
                    content_path=content_path,
                    output_path=output_path,
                    progress_callback=progress_cb,
                )
                global _daily_count
                _daily_count += 1
                with _store_lock:
                    _file_store[file_id]["path"] = output_path
                    _file_store[file_id]["created_at"] = time.time()
                logger.info("[%s] agent.run 执行成功, output=%s", file_id[:8], output_path)
                progress_queue.put({
                    "step": "done",
                    "file_id": file_id,
                    "download_url": f"/api/download/{file_id}",
                    "log_url": f"/api/download-log/{file_id}",
                    "daily_remaining": DAILY_CONVERT_LIMIT - _daily_count,
                })
            except Exception as e:
                logger.exception("[%s] agent.run 执行失败: %s", file_id[:8], e)
                progress_queue.put({"step": "error", "detail": str(e), "log_url": f"/api/download-log/{file_id}"})
            finally:
                wa_logger.removeHandler(file_handler)
                file_handler.close()
                progress_queue.put(None)
                _concurrent_sem.release()
                logger.debug("[%s] worker线程结束, 信号量已释放", file_id[:8])

        worker = threading.Thread(target=run_agent, daemon=True)
        worker.start()
        logger.info("[%s] SSE连接建立, worker线程已启动", file_id[:8])

        yield f"data: {json.dumps({'step': 'init', 'file_id': file_id, 'log_url': f'/api/download-log/{file_id}'}, ensure_ascii=False)}\n\n"

        while True:
            try:
                msg = progress_queue.get(timeout=SSE_KEEPALIVE_INTERVAL)
            except Empty:
                keepalive_count += 1
                elapsed = time.time() - sse_start_time
                idle_time = time.time() - last_msg_time
                logger.debug("[%s] SSE保活 #%d | 已连接%.0fs | 空闲%.0fs | 已发送%d条消息",
                             file_id[:8], keepalive_count, elapsed, idle_time, msg_count)
                if idle_time > SSE_TIMEOUT:
                    logger.warning("[%s] SSE连接空闲超时(%.0fs > %ds), 可能已断开",
                                   file_id[:8], idle_time, SSE_TIMEOUT)
                yield ": keepalive\n\n"
                continue
            if msg is None:
                elapsed = time.time() - sse_start_time
                logger.info("[%s] SSE流结束 | 总耗时%.1fs | 发送%d条消息 | 保活%d次",
                            file_id[:8], elapsed, msg_count, keepalive_count)
                break
            msg_count += 1
            last_msg_time = time.time()
            logger.debug("[%s] SSE发送消息 #%d: step=%s", file_id[:8], msg_count, msg.get("step"))
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"

    logger.info("开始转换(SSE): file_id=%s template=%s content=%s", file_id, template.filename, content.filename)
    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/download/{file_id}")
async def download(file_id: str):
    with _store_lock:
        entry = _file_store.get(file_id)

    if not entry:
        logger.warning("下载请求: file_id=%s 不存在", file_id[:8])
        return JSONResponse(status_code=404, content={"detail": "文件不存在或已过期"})

    if time.time() - entry["created_at"] > FILE_TTL:
        logger.info("下载请求: file_id=%s 已过期(%.0fs)", file_id[:8], time.time() - entry["created_at"])
        _remove_file(file_id)
        return JSONResponse(status_code=410, content={"detail": "文件已过期，请重新转换"})

    if not entry["path"] or not Path(entry["path"]).exists():
        logger.warning("下载请求: file_id=%s 文件不存在(path=%s)", file_id[:8], entry["path"])
        return JSONResponse(status_code=404, content={"detail": "转换尚未完成或已失败"})

    output_file = Path(entry["path"])
    ext = output_file.suffix.lower()
    media_types = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".tex": "application/x-tex",
        ".md": "text/markdown",
    }
    media_type = media_types.get(ext, "application/octet-stream")
    filename = f"converted{ext}"

    logger.info("下载成功: file_id=%s path=%s", file_id[:8], entry["path"])
    return FileResponse(
        path=str(entry["path"]),
        media_type=media_type,
        filename=filename,
    )


@app.get("/api/download-log/{file_id}")
async def download_log(file_id: str):
    with _store_lock:
        entry = _file_store.get(file_id)

    if not entry:
        return JSONResponse(status_code=404, content={"detail": "日志不存在或已过期"})

    if time.time() - entry["created_at"] > FILE_TTL:
        _remove_file(file_id)
        return JSONResponse(status_code=410, content={"detail": "日志已过期"})

    log_path = entry.get("log_path")
    if not log_path or not log_path.exists():
        return JSONResponse(status_code=404, content={"detail": "日志文件不存在"})

    return FileResponse(
        path=str(log_path),
        media_type="text/plain; charset=utf-8",
        filename=f"convert_{file_id[:8]}.log",
    )


def _remove_file(file_id: str):
    with _store_lock:
        entry = _file_store.pop(file_id, None)
    if entry:
        logger.debug("清理文件: file_id=%s tmp_dir=%s", file_id[:8], entry["tmp_dir"])
        shutil.rmtree(entry["tmp_dir"], ignore_errors=True)


def _cleanup_expired():
    """后台线程定期清理过期文件。"""
    while True:
        time.sleep(60)
        now = time.time()
        with _store_lock:
            expired = [fid for fid, e in _file_store.items() if now - e["created_at"] > FILE_TTL]
            active_count = len(_file_store)
        if expired:
            logger.info("清理检查: 活跃任务=%d, 过期=%d", active_count, len(expired))
        for fid in expired:
            _remove_file(fid)
            logger.info("已清理过期文件: %s", fid[:8])


_cleanup_thread = threading.Thread(target=_cleanup_expired, daemon=True)
_cleanup_thread.start()
logger.info("后台清理线程已启动, 检查间隔=60s, 文件TTL=%ds", FILE_TTL)
