# Word Template Converter / 文档智能助手

一款基于 **LangChain**、**LangGraph** 和 **FastAPI** 开发的智能 Word 文档处理工具。利用大语言模型（LLM），本项目能够阅读文档内容及其“弱格式”信号，实现极其复杂的提取、整合和排版。

工具主要提供了两个基于 Web 界面的核心功能：
1. ✨ **Word 对比**：可视化左右对比两份 Word 文档，标红格式变更、标黄内容变更。
2. 🔄 **Word 模板智能套用与转换**：通过上传“内容文件”与“格式模板文件”，由 AI 负责重组和排版，输出符合要求的新文件。

服务秉持**即用即弃**理念：不做长期数据库落盘，转换生成的文件带有倒计时销毁机制。

---

## 🚀 特性

- **智能模板处理**：不再受限于常规字符串替换，而是由 LLM 通读你的整份 Word 模板提取它的结构规约；再分析你提供的纯内容文档，完成重组编排。
- **差异对齐渲染**：不仅仅比对文字差异，深层到段落的「字号、加粗、斜体、对齐方式、样式绑定」来检查对原有模板格式的破坏。
- **现代化无状态 Web UI**：上传即转换、自带动态日志和处理进度模拟。
- **开箱即用，内置限流机制**：提供了按分钟的 IP 熔断器，以防范过度的并发接口滥用。
- **支持 OpenAI / Anthropic**：可灵活配置使用的底层 LLM 驱动（支持兼容 API 转发）。

---

## 🛠️ 如何拉取和本地部署运行

整个项目推荐使用轻量级、极速的 `uv` 工具管理 Python 环境。

### 1. 拉取仓库

```bash
git clone https://github.com/aliveriver/convert.git
cd convert
```

### 2. 准备 Python 虚拟环境与依赖
请确保你的机器上已安装了较新的 `python 3.10` 及 `uv` 依赖包管理工具：

```bash
# 1. 创建使用 Python 3.10 的虚拟环境
uv venv --python 3.10

# 2. 激活虚拟环境 (Windows/PowerShell)
.\.venv\Scripts\Activate.ps1
# 或是 Linux/Mac
# source .venv/bin/activate

# 3. 安装所需依赖
uv pip install -r requirements.txt
```

### 3. 配置密钥 (.env)
复制示例环境变量模版：
```bash
cp .env.example .env
# Windows 用户请使用：
# Copy-Item .env.example .env
```
接着编辑器打开 `.env` 文件填入 LLM 的 API 配置。比如你可以将 `LLM_PROVIDER` 设为 `openai`，填上 `OPENAI_API_KEY` 以及 `OPENAI_BASE_URL`。

### 4. 运行服务

```bash
# 启动 FastAPI 应用，监听 8000 端口
uvicorn word_agent.server:app --host 0.0.0.0 --port 8000
```
成功启动后：
👉 浏览器访问：[http://localhost:8000/](http://localhost:8000/) 即可查看可视化 UI 并进行文档转换或通过顶栏导航至对比工具。

---

## 🐳 如何通过 Docker 运行部署

如果你不想处理环境或打算发布到生产服务器，使用项目内自带的 `docker-compose.yml` 运行会更加方便：

### 1. 克隆与配置

```bash
git clone https://github.com/aliveriver/convert.git
cd convert

# 配置你的 .env 文件 (不要跳过这一步！Docker 容器需要读取里面的 Key)
cp .env.example .env
# 编辑 .env ...
```

### 2. 构建与运行容器

确保你的 Docker Daemon 正在运行，执行：
```bash
docker compose up -d --build
```

### 3. 访问并使用

服务启动后，它会自动把容器的 8000 端口映射到宿主机的 8000 端口：
👉 浏览器访问：[http://localhost:8000/](http://localhost:8000/)

你可以随时通过以下命令停止运行：
```bash
docker compose down
```

---

## 📂 项目结构简述

- `frontend/` - 前端 Web 界面代码（`index.html` 与 `compare.html`）
- `word_agent/` - Python 核心包：
  - `server.py` - FastAPI Web 服务器核心代码及路由
  - `graph.py` & `subagents.py` - LangGraph 代理控制节点流
  - `docx_io.py` - 操作修改与弱特征提取 Word 文件
  - `compare_util.py` - 内置基于内存的文本与样式比对模块
- `prompts/` - 外置 Prompt 存放与配置中心
- `config/settings.yaml` - 综合全局文档体积解析设置项
