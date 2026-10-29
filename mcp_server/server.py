"""
MCP 服务端 —— 把「技能」以标准 Model Context Protocol 暴露给任意 LLM 客户端。

这是作业里 Context Integration 那一项的核心：
智能体的「大脑」（LLM）通过标准协议连接到「本地环境 / API」。

运行方式（stdio 传输）：
    python -m mcp_server.server

或直接在 Cursor / Claude Desktop 的 MCP 配置里指向本文件（见 mcp_config.example.json）。

注意：FastMCP 会根据函数的类型注解 + docstring 自动生成 JSON Schema，
也就是作业里说的 "Pydantic models for function arguments" —— 这部分样板
完全由框架生成，我们只需要写业务逻辑。
"""

from __future__ import annotations

import functools
import logging
import sys
from pathlib import Path
from typing import Any

import httpx

# stdio 传输时 stderr 会透传到客户端控制台。httpx 每次请求都打一行 INFO，
# 会把 agent 的进度输出冲得很乱，这里只保留警告及以上。
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# 允许 `python mcp_server/server.py` 直接运行（把项目根塞进 sys.path）
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 兼容 mcp 1.x 与 2.x：2.x 把 FastMCP 改名为 MCPServer
try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer  # type: ignore
except ImportError:  # mcp < 2.0
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore

# ToolError = 「可预期的失败」。抛出它，客户端会拿到 is_error=True
# 且能读到我们写的错误文案，模型就能据此自我纠正；普通异常则会被
# 服务端吞成一句 "Error executing tool xxx"，模型完全不知道发生了什么。
try:
    from mcp.server.mcpserver.exceptions import ToolError  # mcp >= 2.0
except ImportError:  # mcp < 2.0 会直接把异常消息透传，用普通异常即可
    class ToolError(Exception):  # type: ignore[no-redef]
        """mcp 1.x 回退实现。"""

from mcp_server.skills import analyze_csv, fetch_webpage, read_pdf  # noqa: E402

mcp = MCPServer("study-research-agent")


def agent_tool(fn):
    """把底层异常翻译成「模型读得懂、能据此纠正」的错误消息。

    这是智能体工程里容易被忽略但很关键的一环：工具的报错文案，
    本质上也是 prompt 的一部分。
    """
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except FileNotFoundError as exc:
            raise ToolError(
                f"文件不存在：{exc}。请先用 tool_list_local_files 查看有哪些可用文件，"
                f"或向用户确认正确的路径。"
            ) from exc
        except PermissionError as exc:
            raise ToolError(
                f"路径被安全策略拒绝：{exc}。本服务只允许读取项目内的 data/ 目录。"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ToolError(
                f"网页返回错误状态码 {exc.response.status_code}：{exc.request.url}。"
                f"该页面可能已失效或禁止抓取，请换一个来源。"
            ) from exc
        except httpx.HTTPError as exc:
            raise ToolError(
                f"网络请求失败（{type(exc).__name__}）：{exc}。请检查网络或换一个 URL。"
            ) from exc
        except ValueError as exc:
            raise ToolError(f"参数不合法：{exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"工具执行出错（{type(exc).__name__}）：{exc}") from exc

    return wrapper

# 允许智能体访问的本地目录白名单（防止路径穿越到系统盘任意位置）
ALLOWED_ROOTS = [
    Path(__file__).resolve().parents[1] / "data",
    Path.cwd(),
]


def _guard_path(path: str) -> None:
    """确保请求的本地路径落在白名单目录内。"""
    resolved = Path(path).expanduser().resolve()
    allowed = [r.resolve() for r in ALLOWED_ROOTS]
    if not any(resolved == a or a in resolved.parents for a in allowed):
        raise PermissionError(
            f"拒绝访问白名单之外的路径: {resolved}\n允许的根目录: {[str(a) for a in allowed]}"
        )


# --------------------------------------------------------------------------
# Tool 1: 网页抓取
# --------------------------------------------------------------------------
@mcp.tool()
@agent_tool
def tool_fetch_webpage(url: str, max_chars: int = 4000) -> dict[str, Any]:
    """抓取一个网页并返回清洗后的正文文本。

    当你需要阅读某个在线资料、文档或新闻页面时使用本工具。
    返回结果包含标题、正文和是否被截断的标记。

    Args:
        url: 网页地址，必须以 http:// 或 https:// 开头。
        max_chars: 正文最大返回字符数，默认 4000，超长会自动截断。
    """
    return fetch_webpage(url, max_chars=max_chars)


# --------------------------------------------------------------------------
# Tool 2: CSV 解析与统计
# --------------------------------------------------------------------------
@mcp.tool()
@agent_tool
def tool_analyze_csv(path: str, preview_rows: int = 3) -> dict[str, Any]:
    """解析一个本地 CSV 文件，返回列结构、类型推断与统计摘要。

    当你需要分析成绩表、实验数据、调查问卷等表格文件时使用本工具。
    数值列会给出 min/max/mean/median/stdev，文本列会给出 Top 值分布。

    Args:
        path: CSV 文件路径。
        preview_rows: 返回的样例行数，默认 3。
    """
    _guard_path(path)
    return analyze_csv(path, preview_rows=preview_rows)


# --------------------------------------------------------------------------
# Tool 3: PDF 文本提取
# --------------------------------------------------------------------------
@mcp.tool()
@agent_tool
def tool_read_pdf(path: str, start_page: int = 1, end_page: int | None = None,
                  max_chars: int = 8000) -> dict[str, Any]:
    """提取本地 PDF 文件的文本内容，按页返回。

    当你需要总结论文、讲义、教材等 PDF 资料时使用本工具。
    返回结果按页组织，便于你引用具体页码。

    Args:
        path: PDF 文件路径。
        start_page: 起始页（从 1 开始）。
        end_page: 结束页；不填表示读到最后一页。
        max_chars: 总字符上限，默认 8000。
    """
    _guard_path(path)
    return read_pdf(path, start_page=start_page, end_page=end_page, max_chars=max_chars)


# --------------------------------------------------------------------------
# Tool 4: 本地文件发现（辅助编排）
# --------------------------------------------------------------------------
@mcp.tool()
@agent_tool
def tool_list_local_files(directory: str | None = None) -> dict[str, Any]:
    """列出可访问目录下的 CSV / PDF / TXT 文件。

    在不确定用户提供了哪些本地资料时，先用本工具探查，再决定调用哪个解析工具。

    Args:
        directory: 要列出的目录；不填则列出默认 data 目录。
    """
    root = Path(directory).expanduser() if directory else ALLOWED_ROOTS[0]
    if directory:
        _guard_path(directory)
    if not root.exists():
        raise FileNotFoundError(f"目录不存在: {root}")

    wanted = {".csv", ".pdf", ".txt", ".md", ".tsv"}
    files = [
        {"name": f.name, "path": str(f.resolve()), "size_bytes": f.stat().st_size}
        for f in sorted(root.rglob("*"))
        if f.is_file() and f.suffix.lower() in wanted
    ]
    return {"directory": str(root.resolve()), "count": len(files), "files": files}


if __name__ == "__main__":
    # 默认 stdio 传输，可被任何 MCP 客户端拉起
    mcp.run()
