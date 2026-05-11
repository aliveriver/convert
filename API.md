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

上传模板文件和内容文件，服务端完成转换后返回文件 ID 和下载链接。

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
| 200 | `application/json` | 返回文件 ID 和下载地址 |

```json
{
  "file_id": "a1b2c3d4e5f6...",
  "download_url": "/api/download/a1b2c3d4e5f6..."
}
```

**错误响应**

| 状态码 | 说明 | 示例 |
|--------|------|------|
| 400 | 文件格式不正确 | `{"detail": "template 必须是 .docx 文件"}` |
| 413 | 文件超过大小限制 | `{"detail": "文件 xxx.docx 超过 10MB 限制"}` |
| 429 | 请求过于频繁 | `{"detail": "请求过于频繁，请稍后再试（每分钟最多 3 次）"}` |
| 500 | 服务端转换失败 | `{"detail": "转换失败: ..."}` |

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

### fetch (JavaScript)

```javascript
async function convertDocument(templateFile, contentFile) {
  const formData = new FormData();
  formData.append('template', templateFile);
  formData.append('content', contentFile);

  // 1. 提交转换请求
  const response = await fetch('/api/convert', {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail);
  }

  const { file_id, download_url } = await response.json();

  // 2. 下载生成的文件
  const downloadRes = await fetch(download_url);
  const blob = await downloadRes.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'converted.docx';
  a.click();
  URL.revokeObjectURL(url);
}
```

### axios

```javascript
async function convertDocument(templateFile, contentFile) {
  const formData = new FormData();
  formData.append('template', templateFile);
  formData.append('content', contentFile);

  // 1. 提交转换请求
  const { data } = await axios.post('/api/convert', formData);

  // 2. 下载生成的文件
  const downloadRes = await axios.get(data.download_url, {
    responseType: 'blob',
  });

  const url = URL.createObjectURL(downloadRes.data);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'converted.docx';
  a.click();
  URL.revokeObjectURL(url);
}
```

---

## 注意事项

- 生成的文件保留 10 分钟，过期后自动删除，需重新转换
- 无需认证，无用户系统
- CORS 已开启，支持任意域名跨域调用
- 转换耗时取决于文档长度和 LLM 响应速度，建议前端设置较长的超时时间（如 120s）
- 所有错误响应格式统一为 `{"detail": "错误描述"}`
