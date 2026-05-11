# Word Template Converter API 文档

Base URL: `http://<your-host>:8000`

---

## 健康检查

### GET /api/health

检查服务是否正常运行。

**响应**

| 状态码 | 说明 |
|--------|------|
| 200 | 服务正常 |

```json
{
  "status": "ok"
}
```

---

## 文档转换

### POST /api/convert

上传模板文件和内容文件，通过 SSE（Server-Sent Events）实时推送转换进度，最终返回下载链接。

**请求**

- Content-Type: `multipart/form-data`
- 限流: 同一 IP 每分钟最多 3 次

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| template | file | 是 | 模板 .docx 文件，最大 10MB |
| content | file | 是 | 内容 .docx 文件，最大 10MB |

**成功响应**

| 状态码 | Content-Type | 说明 |
|--------|--------------|------|
| 200 | `text/event-stream` | SSE 流，逐条推送进度事件 |

每条事件格式为 `data: {JSON}\n\n`，事件类型如下：

```jsonc
// 工作流开始
{"step": "workflow", "status": "started"}

// 模板分析开始
{"step": "analyze_template", "status": "started"}

// 内容分析开始（与模板分析并行）
{"step": "analyze_content", "status": "started"}

// 模板分析完成
{"step": "analyze_template", "status": "completed", "elapsed": 12.3}

// 内容分析完成
{"step": "analyze_content", "status": "completed", "elapsed": 15.1}

// 文档生成开始
{"step": "generate_document", "status": "started", "total_chunks": 4}

// 生成分块进度
{"step": "generate_chunk", "current": 1, "total": 4}
{"step": "generate_chunk", "current": 2, "total": 4}

// 文档生成完成
{"step": "generate_document", "status": "completed", "elapsed": 45.2}

// 写入 DOCX 完成
{"step": "write_docx", "status": "completed", "elapsed": 1.1}

// 全部完成，返回下载链接
{"step": "done", "file_id": "abc123", "download_url": "/api/download/abc123", "daily_remaining": 49}

// 出错
{"step": "error", "detail": "错误描述"}
```

**错误响应（非 SSE，直接返回 JSON）**

以下情况不会进入 SSE 流，直接返回 JSON 错误：

| 状态码 | 说明 | 示例 |
|--------|------|------|
| 400 | 文件格式不正确 | `{"detail": "template 必须是 .docx 文件"}` |
| 413 | 文件超过大小限制 | `{"detail": "文件 xxx.docx 超过 10MB 限制"}` |
| 429 | 请求过于频繁或每日上限 | `{"detail": "请求过于频繁，请稍后再试（每分钟最多 3 次）"}` |
| 503 | 服务器繁忙 | `{"detail": "服务器繁忙，请稍后再试"}` |

---

## 文件下载

### GET /api/download/{file_id}

根据转换接口返回的 file_id 下载生成的 .docx 文件。

**路径参数**

| 参数 | 说明 |
|------|------|
| file_id | 由 `/api/convert` 返回的文件标识 |

**成功响应**

| 状态码 | Content-Type | 说明 |
|--------|--------------|------|
| 200 | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | 返回二进制 .docx 文件流 |

响应头包含：
```
Content-Disposition: attachment; filename="converted.docx"
```

**错误响应**

| 状态码 | 说明 | 示例 |
|--------|------|------|
| 404 | 文件不存在 | `{"detail": "文件不存在或已过期"}` |
| 410 | 文件已过期（超过 10 分钟） | `{"detail": "文件已过期，请重新转换"}` |

所有错误响应格式统一为：
```json
{
  "detail": "错误描述"
}
```

---

## 前端对接示例

### SSE 进度 + 下载（推荐）

```javascript
async function convertDocument(templateFile, contentFile, onProgress) {
  const formData = new FormData();
  formData.append('template', templateFile);
  formData.append('content', contentFile);

  const response = await fetch('/api/convert', {
    method: 'POST',
    body: formData,
  });

  // 非 SSE 错误（400/413/429/503）
  if (!response.ok && response.headers.get('content-type')?.includes('application/json')) {
    const error = await response.json();
    throw new Error(error.detail);
  }

  // 读取 SSE 流
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let downloadUrl = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const text = decoder.decode(value, { stream: true });
    for (const line of text.split('\n')) {
      if (!line.startsWith('data: ')) continue;
      const event = JSON.parse(line.slice(6));

      if (event.step === 'error') {
        throw new Error(event.detail);
      }
      if (event.step === 'done') {
        downloadUrl = event.download_url;
      }
      // 回调通知 UI 更新进度
      onProgress(event);
    }
  }

  // 下载文件
  if (downloadUrl) {
    const a = document.createElement('a');
    a.href = downloadUrl;
    a.download = 'converted.docx';
    a.click();
  }
}

// 使用示例
convertDocument(templateFile, contentFile, (event) => {
  console.log(event.step, event.status || '', event.current || '');
  // 根据 event.step 更新 UI 进度条/步骤指示器
});
```

---

## 注意事项

- 生成的文件保留 10 分钟，过期后自动删除，需重新转换
- 无需认证，无用户系统
- CORS 已开启，支持任意域名跨域调用
- 转换通过 SSE 实时推送进度，无需轮询
- 全局每日转换上限 100 次，最大并发 5 个任务
- nginx 反代需关闭缓冲：`proxy_buffering off;`
- 所有错误响应格式统一为 `{"detail": "错误描述"}`
