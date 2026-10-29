# StudyScout —— 一个会「真的去查」的学习研究助手

> 一个通过 **MCP（Model Context Protocol）** 连接外部工具的自定义 AI 智能体。
> 它不靠模型的内置记忆回答问题，而是先调用工具取证，再基于证据作答。

学生复习时要在讲义、论文、成绩表之间来回切换。通用的聊天机器人要么查不到本地资料，
要么凭印象编造教材里根本没有的说法。**StudyScout 的设计目标就是消除这种幻觉**——
它被强制要求「先取证，后回答」，每一条结论都必须能溯源到工具返回的原始材料。

---

## 一、作业要求对照表

| 作业要求 (Must-Haves) | 本项目的实现 | 代码位置 |
|---|---|---|
| **Tool Use / Skills**：至少 2 个不同的功能性技能 | 实现了 **4 个**技能：网页抓取、CSV 统计解析、PDF 文本提取、本地文件发现 | `mcp_server/skills/` |
| **Context Integration**：使用 MCP 或标准 function calling 连接本地环境 / API | **真正的 MCP 服务端**（官方 Python SDK，stdio 传输，JSON-RPC 握手），同时提供 OpenAI 标准 function-calling 的协议翻译层 | `mcp_server/server.py`<br>`agent/loop.py::mcp_tools_to_openai` |
| **Vibe Coding 约束**：样板代码交给 AI，精力集中在 system prompt 和编排循环 | MCP 服务端骨架、JSON Schema 生成、HTTP 请求逻辑、PDF/CSV 解析样板均由 AI 生成；**系统提示词与编排循环的设计是刻意手工打磨的核心** | `agent/prompts.py`<br>`agent/loop.py` |

**加分项**（超出要求的部分）：

- 完整的**错误处理链路**：工具的报错文案被设计成「模型能读懂并据此纠正」的形式
- **路径白名单**安全护栏，防止智能体读到系统盘任意文件
- **无需 API key 的离线演示**，可直接验证整条 MCP 链路
- **25 项测试全部通过**（10 项技能单测 + 5 项编排链路集成测试 + 10 项思考模式回归，含真实联网抓取）
- 兼容 `mcp` 1.x 与 2.x 两个大版本

---

## 二、架构

```
                          ┌──────────────────────────────┐
                          │        用户提问               │
                          └──────────────┬───────────────┘
                                         ▼
    ┌────────────────────────────────────────────────────────────────┐
    │                    agent/loop.py  ——  编排循环                  │
    │                                                                │
    │   ① 把 system prompt + 用户问题发给 LLM                          │
    │   ② LLM 返回「要调用某个工具」或「直接回答」                        │
    │   ③ 若调用工具 → 通过 MCP 通道执行 → 结果回灌进消息历史 → 回到 ①   │
    │   ④ 若直接回答 → 输出最终答案                                    │
    └───────────────┬────────────────────────────────┬───────────────┘
                    │                                │
         system prompt │                    MCP 协议 (JSON-RPC over stdio)
                    ▼                                ▼
    ┌───────────────────────┐        ┌───────────────────────────────────┐
    │  agent/prompts.py     │        │      mcp_server/server.py         │
    │  ─────────────────    │        │      ──────────────────────       │
    │  • 角色定位            │        │  tool_fetch_webpage   ← httpx+bs4 │
    │  • 铁律：先取证后回答   │        │  tool_analyze_csv     ← csv+stats │
    │  • 禁止编造 / 标注来源  │        │  tool_read_pdf        ← pypdf     │
    │  • 输出格式规范         │        │  tool_list_local_files            │
    └───────────────────────┘        └───────────────────────────────────┘
                                                   │
                                                   ▼
                                     ┌──────────────────────────┐
                                     │  外部世界 / 本地环境       │
                                     │  网页 · CSV · PDF 文件     │
                                     └──────────────────────────┘
```

关键点：**「大脑」和「手脚」是通过标准协议解耦的**。
同一个 MCP 服务端，不仅能被本项目的编排循环使用，还能直接被 Cursor、
Claude Desktop 等任何 MCP 客户端挂载（见 `mcp_config.example.json`）。

---

## 三、目录结构

```
study-research-agent/
├── agent/                      # 智能体（大脑 + 编排）
│   ├── prompts.py              # ★ 系统提示词 —— 本项目刻意打磨的核心之一
│   ├── loop.py                 # ★ 编排循环 —— 本项目刻意打磨的核心之二
│   ├── llm.py                  # LLM 客户端（任何 OpenAI 兼容接口）
│   └── mcp_compat.py           # MCP 1.x / 2.x 字段差异兼容层
├── mcp_server/                 # MCP 服务端（手脚）
│   ├── server.py               # 4 个工具的 MCP 封装 + 错误翻译 + 路径白名单
│   └── skills/
│       ├── web_fetch.py        # 技能 1：网页正文抓取
│       ├── csv_analyze.py      # 技能 2：CSV 解析与统计
│       └── pdf_read.py         # 技能 3：PDF 文本提取
├── data/                       # 演示资料
│   ├── sample_grades.csv       # 12 名学生成绩表
│   └── sample_paper.pdf        # 4 页中文论文（RAG 主题）
├── scripts/
│   ├── make_sample_pdf.py      # 生成演示用 PDF
│   └── mock_llm_server.py      # 本地 mock 的 OpenAI 兼容服务（无需真 key 验证真实代码路径）
├── tests/
│   ├── test_skills.py          # 10 项技能单元测试
│   ├── test_agent_loop.py      # 5 项编排链路集成测试（真实 MCP 子进程）
│   └── test_thinking_mode.py   # 10 项思考模式回归（reasoning_content 回传）
├── smoke_test.py               # ★ 6 层自检（环境→数据→协议→工具→密钥→决策）
├── verify.py                   # ★ 独立验证脚本（16 项检查，含篡改测试）
├── demo_offline.py             # ★ 无需 API key 的完整链路演示
├── run_agent.py                # 真实运行入口（命令行 / 交互模式）
├── run.bat                     # ★ Windows 启动器，强制使用项目 .venv
├── mcp_config.example.json     # 挂到 Cursor / Claude Desktop 的配置示例
├── requirements.txt
├── .gitignore                  # 排除 .venv / .env / 构建中间产物
├── .gitattributes              # 统一换行符，保证 .bat 克隆后仍是 CRLF
└── .env.example                # ★ 配置模板：复制为 .env 后填密钥（.env 不上传）
```

---

## 四、快速开始

> **⚠ 先看这一条：`python` 可能不是你以为的那个 Python**
>
> 机器上同时装了 conda / 系统 Python 时，`python smoke_test.py` 很可能跑在 conda 的
> base 环境上，于是报「`mcp` / `openai` / `pypdf` 未安装」—— 而项目 `.venv` 里其实全都有。
> 本项目自带 `run.bat`，强制使用项目内的 `.venv`：
>
> ```bat
> run.bat smoke_test.py
> run.bat run_agent.py --tools
> run.bat run_agent.py "分析 data/sample_grades.csv"
> ```
>
> 不用 `run.bat` 时，请写全路径：`.venv\Scripts\python.exe smoke_test.py`。
> 下文为简洁起见仍写作 `python`，请自行替换。

### 1. 装依赖

```bash
cd study-research-agent
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

### 2. 先跑离线演示（不需要任何 API key）

```bash
python demo_offline.py
```

这会启动真实的 MCP 子进程、完成真实协议握手、真实执行工具，把整条链路走一遍。

### 3. 跑测试与独立验证

```bash
python tests/test_skills.py        # 技能单测（10 项）
python tests/test_agent_loop.py    # 编排链路集成测试（5 项，会真实拉起 MCP 子进程）
python verify.py                   # 独立验证（16 项，含篡改测试）★ 最有说服力
```

`verify.py` 里有一项**篡改测试**：它改掉成绩表里的一个数字，检查智能体的结论是否跟着变。
如果结论是写死的，这一项必然失败 —— 这是判断「演示是不是演戏」最直接的手段。

### 4. 配置 LLM 并真实运行

> 逐步流程：申请密钥 → 复制 `.env.example` 为 `.env` → 填 key → `smoke_test.py` 自检 →
> 逐个工具跑一遍。填错时的报错含义见本节下方「填错时会看到什么」。

克隆下来的仓库里没有 `.env`（密钥不会上传），**先把模板复制成 `.env`**：

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

然后打开 `.env`，**只改一行**：

```ini
LLM_API_KEY=sk-在这里填你的密钥      # ← 只改这一行就能跑
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
LLM_THINKING=disabled
```

`.env.example` 里已经把 OpenAI / 通义千问 / Kimi / 智谱 / 本地 Ollama 五组配置写好并注释着，
换供应商时把对应三行取消注释即可。改完先自检，再正式运行：

```bash
python smoke_test.py               # ★ 6 层自检，逐层定位问题
python run_agent.py --tools        # 只看 MCP 暴露了哪些工具（不花钱）
python run_agent.py "帮我分析一下 data/sample_grades.csv，哪个专业的成绩最好？"
python run_agent.py                # 进入交互模式，输入 exit 退出
```

**配置项说明**

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `LLM_API_KEY` | 是 | — | 密钥。必须是纯 ASCII，不能留中文占位符 |
| `LLM_BASE_URL` | 否 | `https://api.openai.com/v1` | 任何 OpenAI 兼容端点 |
| `LLM_MODEL` | 否 | `gpt-4o-mini` | 模型名，需与供应商匹配 |
| `LLM_THINKING` | 否 | `disabled` | DeepSeek 专用：`disabled` / `enabled` |
| `LLM_REASONING_EFFORT` | 否 | `high` | `LLM_THINKING=enabled` 时生效：`low` / `high` / `max` |

**DeepSeek 的两个坑**（2026-10 官方文档实测确认）：

1. **模型名已变**。当前是 `deepseek-flash` / `deepseek-v4-pro`，
   老教程里的 `deepseek-chat` 已从官方文档下线。
2. **思考模式默认开启，且与 tools 冲突**。官方要求：携带 `tools` 的请求，
   后续每轮必须完整回传 `reasoning_content`，否则返回 400。
   本项目默认 `LLM_THINKING=disabled` 规避；开启时 `agent/loop.py` 会自动回传，
   由 `tests/test_thinking_mode.py` 专门验证。

**填错时会看到什么**（都做了中文提示，不会甩堆栈）：

```
[错误] LLM_API_KEY 还是占位符（当前值：sk-在这里填你的密钥）。请打开项目根目录的 .env …
[错误] LLM_API_KEY 含有非 ASCII 字符（可能是中文），密钥必须是一串英文字母和数字 …
[错误] 密钥无效（HTTP 401）。请检查 .env 里的 LLM_API_KEY 是否复制完整 …
[错误] 模型不存在（HTTP 404）。请检查 .env 里的 LLM_MODEL 是否拼写正确 …
```

需要看完整堆栈时加 `--debug`：

```bash
python run_agent.py "你的问题" --debug
```

**没有 API key 也想验证真实代码路径？** 用本地 mock 模型（跑的是完全相同的
`LLMClient → OpenAI SDK → HTTP → 编排循环` 链路，只是模型输出是脚本化的）：

```bash
# 终端 A
python scripts/mock_llm_server.py 8077

# 终端 B
LLM_API_KEY=mock LLM_BASE_URL=http://127.0.0.1:8077/v1 LLM_MODEL=mock \
  python run_agent.py "帮我看看本地有哪些资料，分析一下成绩表，再读读那篇论文"
```

### 5.（可选）挂到 Cursor / Claude Desktop

把 `mcp_config.example.json` 里的内容粘进你的 MCP 配置，改好绝对路径，
重启客户端后就能在任意对话里直接使用这 4 个工具。

---

## 五、四个技能

| 工具 | 做什么 | 技术实现 |
|---|---|---|
| `tool_fetch_webpage` | 抓网页并抠出正文，剥掉 script/style/nav 等噪音，按需截断 | `httpx` + `BeautifulSoup` |
| `tool_analyze_csv` | 解析 CSV，自动推断每列类型；数值列给 min/max/mean/median/stdev，文本列给 Top 值分布 | 标准库 `csv` + `statistics` |
| `tool_read_pdf` | 按页提取 PDF 文本，支持页码范围与字符上限 | `pypdf` |
| `tool_list_local_files` | 探查本地有哪些可用资料，供智能体决定下一步调用哪个工具 | `pathlib` |

工具的**参数 JSON Schema 完全由类型注解自动生成**，不需要手写：

```python
@mcp.tool()
@agent_tool
def tool_read_pdf(path: str, start_page: int = 1, end_page: int | None = None,
                  max_chars: int = 8000) -> dict[str, Any]:
    """提取本地 PDF 文件的文本内容，按页返回。
    ...
    """
```

上面这段函数签名，MCP SDK 会自动生成如下 schema 交给 LLM——
这正是作业里说的「Pydantic models for function arguments」由 AI/框架代劳的部分：

```json
{
  "type": "object",
  "properties": {
    "path":       {"type": "string",  "description": "PDF 文件路径。"},
    "start_page": {"type": "integer", "default": 1},
    "end_page":   {"type": ["integer", "null"]},
    "max_chars":  {"type": "integer", "default": 8000}
  },
  "required": ["path"]
}
```

---

## 六、编排循环是怎么工作的

`agent/loop.py` 的核心只有 30 行左右：

```python
for step in range(1, MAX_STEPS + 1):
    msg = llm.chat(messages, openai_tools)        # ① 让 LLM 决策

    if not msg.tool_calls:                        # ④ 不再调工具 → 收敛
        return {"answer": msg.content, ...}

    messages.append(...)                          # 记下 LLM 的工具请求

    for tc in msg.tool_calls:                     # ② 通过 MCP 执行
        result = await session.call_tool(name, args)
        messages.append({"role": "tool", ...})    # ③ 结果回灌 → 回到 ①
```

三个设计决策值得说明：

1. **`MAX_STEPS = 8`**：硬性上限，防止模型陷入「反复调同一个工具」的死循环烧 token。
2. **工具结果截断（12000 字符）**：防止一次抓取把上下文窗口撑爆。
3. **协议翻译层**：MCP 用 `inputSchema` 描述参数，OpenAI 用 `function.parameters`，
   两者本质都是 JSON Schema，`mcp_tools_to_openai()` 负责这层翻译。
   这样换任何 LLM 供应商都不用改工具代码。

---

## 七、Vibe Coding 说明（哪些是 AI 写的，哪些是我写的）

作业要求「用 AI 写样板，把精力留给 system prompt 和编排循环」。实际分工：

**交给 AI 生成的样板**：

- MCP 服务端的注册骨架、stdio 传输配置
- 由类型注解自动生成 JSON Schema 的参数模型
- `httpx` 请求逻辑、`BeautifulSoup` 清洗规则、`pypdf` 抽取调用
- CSV 的读取与 `statistics` 统计计算
- MCP 1.x / 2.x 的 API 差异排查与兼容层
- 单元测试的用例骨架、演示脚本的脚手架

**我刻意手工设计的部分**：

- **系统提示词（`agent/prompts.py`）**——这是整个项目的灵魂。里面 6 条「铁律」
  是反复推敲的结果，尤其是「禁止编造」和「标注来源」两条，直接决定了智能体
  会不会退化成普通聊天机器人。工具的报错文案也按 prompt 的标准来写：
  报错要说清楚「发生了什么 + 下一步该怎么做」，因为模型要读它。
- **编排循环的结构（`agent/loop.py`）**——终止条件、截断策略、错误回灌方式、
  事件回调机制（用于可观测性）。
- **路径白名单的边界**——安全护栏划在哪里。
- **离线演示的设计**——复用真实编排循环、只替换 LLM 桩，
  这样演示才真正证明了架构可用，而不是演了一场戏。

---

## 八、验证证据

### 测试全过（25 项）

```
$ python tests/test_skills.py
test_basic_shape ................. ok      # CSV 12 行 8 列
test_numeric_column_stats ........ ok      # grade_math 均值 83.8333 / 区间 [68,95]
test_text_column_top_values ...... ok      # 4 个专业各 3 人
test_extract_first_page .......... ok      # PDF 4 页，中文正常抽取
test_page_range_and_truncation ... ok
test_real_fetch_optional ......... ok      # 真实抓取 example.com
Ran 10 tests in 0.595s
OK

$ python tests/test_agent_loop.py
test_full_loop_with_real_mcp_server ......... ok   # 真实 MCP 子进程 + 4 轮闭环
test_loop_reports_tool_failure_without_crash ok   # 工具失败不崩溃，错误回灌给模型
test_path_guard_blocks_outside_access ....... ok   # 路径白名单生效
test_converts_mcp_tool_to_openai_format ..... ok   # 协议翻译层
test_missing_schema_falls_back_to_empty_obj . ok
Ran 5 tests in 8.369s
OK
```

### 离线演示（真实 MCP 链路）

```
[MCP] 已通过 stdio 连上服务端，发现 4 个工具

[第 1 轮] 决策 → 调用 tool_list_local_files({})
          结果 ✅ 成功: {..., "count": 2, "files": [sample_grades.csv, sample_paper.pdf]}
[第 2 轮] 决策 → 调用 tool_analyze_csv({"path": "...sample_grades.csv"})
          结果 ✅ 成功: {..., "row_count": 12, "column_count": 8, ...}
[第 3 轮] 决策 → 调用 tool_read_pdf({"path": "...sample_paper.pdf", "start_page": 1})
          结果 ✅ 成功: {..., "page_count": 4, ...}
[第 4 轮] 决策 → 材料已足够，生成最终答案

最终回答（节选）：
  **成绩表概览**：共 12 条记录、8 个字段。
  - grade_math：均值 83.8333，中位数 86.0，区间 [68.0, 95.0]
  **论文摘要**：PDF 共 4 页，本次读取 1 页。
  开头内容：基于检索增强生成的学习资料问答系统研究 摘要：大语言模型…
```

### 独立验证（16/16，含篡改测试）

```
$ python verify.py
1) 依赖检查
  [PASS] 核心依赖可导入
2) MCP 协议真实性（裸 JSON-RPC 握手，绕过所有封装）
  [PASS] 服务端响应裸 JSON-RPC
  [PASS] initialize 返回合规握手          serverInfo.name='study-research-agent'
  [PASS] tools/list 返回 4 个工具且带完整 schema
3) 技能真实性
  [PASS] CSV 解析出真实行数/列数
  [PASS] 统计值与独立复算一致              独立复算=83.8333, 项目输出=83.8333
  [PASS] PDF 逐页抽取内容确实不同
4) 篡改测试（证明结论不是写死的）
  [PASS] 篡改后的新均值出现在结论中         改后独立复算均值=84.8333，结论中出现
  [PASS] 原始均值不再出现（未被写死）
5) 安全护栏
  [PASS] 项目目录之外的路径被拒绝
6) 编排循环真实性
  [PASS] 走了 3 轮工具调用后收敛
  [PASS] 三次调用全部成功
  [PASS] MCP 服务端确实暴露了 4 个工具
7) 官方测试套件
  [PASS] 技能单测 全过
  [PASS] 编排集成测试 全过
  [PASS] 思考模式回归 全过
结果：16/16 项通过
```

### 真实代码路径验证（mock 模型）

真实 LLM 调用路径通过本地 mock 模型验证，跑的是完全相同的
`LLMClient → openai SDK → HTTP → tool_calls 解析 → 编排循环` 链路：

```
$ LLM_API_KEY=mock LLM_BASE_URL=http://127.0.0.1:8077/v1 LLM_MODEL=mock \
    python run_agent.py "帮我看看本地有哪些资料，分析一下成绩表，再读读那篇论文"
[MCP] 已连接，可用工具: tool_fetch_webpage, tool_analyze_csv, tool_read_pdf, tool_list_local_files
[轮次 1] → 调用工具 tool_list_local_files({})
[轮次 2] → 调用工具 tool_analyze_csv({"path": "...sample_grades.csv"})
[轮次 3] → 调用工具 tool_read_pdf({"path": "...sample_paper.pdf"})
[轮次 4] ✓ 生成最终答案
共 4 轮，调用工具 3 次。
```

### 错误处理（工具报错对模型可读）

```
[路径白名单拦截]  is_error=True
   → 路径被安全策略拒绝：... 本服务只允许读取项目内的 data/ 目录。
[文件不存在]      is_error=True
   → 文件不存在：... 请先用 tool_list_local_files 查看有哪些可用文件。
[非法 URL]        is_error=True
   → 参数不合法：只支持 http/https 链接，收到: 'ftp://x.com'
```

### 局限（如实说明）

**真实付费 API 未被调用过。** 我手上没有可用的 API 额度，所以「模型决策」这一步
是用本地 mock 模型和脚本化 LLM 验证的。除模型输出外的全部链路（协议握手、工具执行、
结果回灌、错误处理、收敛判断）都已被真实验证。填入真实 key 后即可直接运行。

---

## 九、可以继续扩展的方向

- **加更多技能**：SQLite 查询、arXiv 检索、日历读写、邮件草稿
- **换传输方式**：把 stdio 换成 streamable-http，让服务端跑在远程
- **加记忆**：把对话历史持久化，让智能体跨会话记住用户的研究方向
- **流式输出**：把 LLM 的响应改成流式，提升交互体验
- **评测集**：构造一批「有标准答案」的问答，量化幻觉率

---

## 十、许可

本项目为课程作业，可自由使用与修改。
