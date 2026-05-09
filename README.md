# Word Template Agent

一个基于 LangGraph / LangChain 的 Python agent：读取 Word 模板全文和格式信号，让 LLM 自行总结行文要求；再读取用户 Word 内容全文，识别标题/正文/列表/表格等结构，最后生成符合模板要求的新 `.docx` 文档。

## 环境

```powershell
uv venv --python 3.10
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`，选择 `LLM_PROVIDER=openai` 或 `LLM_PROVIDER=anthropic`。

OpenAI 兼容接口可以通过 `OPENAI_BASE_URL` 指向第三方兼容服务。

## LangSmith

项目支持 LangSmith 追踪。编辑 `.env`：

```powershell
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=word-template-agent
```

启用后，LangGraph 运行会带上 `run_name`、`tags` 和输入输出文件路径等 metadata。你可以在 LangSmith 里查看模板子 agent、内容子 agent、最终生成节点以及 LLM 调用链路。

## 使用

```powershell
python -m word_agent.cli `
  --template path\to\template.docx `
  --content path\to\content.docx `
  --output out\generated.docx
```

运行时会默认输出 `INFO` 级别日志，包含模板分析、内容分析、最终生成、DOCX 写入等节点的进度、耗时和关键统计。

调试时可以提高日志级别：

```powershell
python -m word_agent.cli `
  --template path\to\template.docx `
  --content path\to\content.docx `
  --output out\generated.docx `
  --log-level DEBUG
```

导出 LangGraph Mermaid 可视化文件：

```powershell
python -m word_agent.cli `
  --template path\to\template.docx `
  --content path\to\content.docx `
  --output out\generated.docx `
  --debug-graph debug\word_agent_graph
```

这会生成 `debug\word_agent_graph.mmd`。如果需要同时尝试生成 PNG，可以加上 `--debug-graph-png`；PNG 渲染取决于本地环境或外部渲染能力，失败时不影响主流程。

## 项目结构

- `word_agent/docx_io.py`：读取模板结构、提取正文、写出 DOCX
- `word_agent/subagents.py`：模板文档子 agent 与内容文档子 agent
- `word_agent/llm.py`：OpenAI / Anthropic 模型适配与 JSON 解析
- `word_agent/graph.py`：LangGraph 节点编排
- `word_agent/cli.py`：命令行入口
- `config/settings.yaml`：模型、Provider、文档限制配置
- `prompts/*.md`：外置提示词

## 工作流

1. 模板文档子 agent 读取模板 `.docx` 的页面、页眉页脚、段落样式、正文段落、表格和全部可见文本块，不做关键词筛选，让 LLM 自行判断哪些是要求、示例、占位符或正文。
2. 内容文档子 agent 读取用户内容 `.docx` 的全部可见文本块，保留段落顺序、样式名、字号、加粗、对齐、编号、文本长度等弱格式证据，让 LLM 判断标题、层级、正文、列表、表格和备注。
3. LangGraph 并行执行模板分析和内容分析，随后汇合到最终生成节点。
4. 最终生成节点根据模板显式要求、模板格式分析、内容结构分析和原始内容，生成严格 JSON 结构的标题和段落列表。
5. 以模板文档为母版，清空正文但保留页面设置与样式，写入新内容。
