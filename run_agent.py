"""
命令行入口 —— 真正调用 LLM 跑智能体。

用法：
    python run_agent.py "帮我分析一下 data/sample_grades.csv 里哪个专业成绩最好"
    python run_agent.py            # 进入交互模式
    python run_agent.py --tools    # 只列出 MCP 暴露了哪些工具，不调用 LLM
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

# Windows 控制台默认是 GBK（cp936），遇到编不出的字符会抛 UnicodeEncodeError。
# 只放宽错误策略、不强制改编码，避免在 GBK 终端里把中文变成乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:  # pragma: no cover
        pass


async def list_tools_only() -> None:
    """不依赖 LLM，直接连上 MCP 服务端把工具清单打出来。"""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    from agent.loop import server_params
    from agent.mcp_compat import get_input_schema

    async with stdio_client(server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            print(f"MCP 服务端暴露了 {len(listed.tools)} 个工具：\n")
            for t in listed.tools:
                params = ", ".join(get_input_schema(t).get("properties", {}).keys())
                print(f"  • {t.name}({params})")
                first_line = (t.description or "").strip().splitlines()[0]
                print(f"      {first_line}\n")


async def run_once(question: str) -> None:
    from agent.loop import ask

    result = await ask(question, verbose=True)
    print("\n" + "=" * 68)
    print("最终回答：")
    print("=" * 68)
    print(result["answer"])
    print("=" * 68)
    print(f"共 {result['steps']} 轮，调用工具 {len(result['trace'])} 次。")


async def interactive() -> None:
    from agent.loop import ask

    print("StudyScout 交互模式 —— 输入问题回车，输入 exit 退出。\n")
    while True:
        try:
            q = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q.lower() in {"exit", "quit", "q"}:
            break
        if not q:
            continue
        result = await ask(q, verbose=True)
        print("\n" + result["answer"] + "\n")


def _unwrap(exc: BaseException) -> BaseException:
    """剥掉 asyncio TaskGroup 套上的 ExceptionGroup 外壳，取出真正的异常。

    stdio_client / ClientSession 内部用 TaskGroup 做结构化并发，任何发生在
    子任务里的错误都会被包成 ExceptionGroup("unhandled errors in a TaskGroup")。
    不剥壳的话，用户看到的永远是这句毫无信息量的话。
    """
    seen = 0
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions and seen < 10:
        inner = [e for e in exc.exceptions
                 if not isinstance(e, (asyncio.CancelledError, GeneratorExit))]
        exc = (inner or list(exc.exceptions))[0]
        seen += 1
    return exc


def _explain(exc: Exception) -> str:
    """把常见异常翻译成一句能直接照做的中文提示。"""
    exc = _unwrap(exc)  # type: ignore[assignment]

    if isinstance(exc, RuntimeError):
        return str(exc)

    try:
        import openai
    except ImportError:  # pragma: no cover
        return f"{type(exc).__name__}: {exc}"

    if isinstance(exc, openai.AuthenticationError):
        return ("密钥无效（HTTP 401）。请检查 .env 里的 LLM_API_KEY 是否复制完整、"
                "有没有多出空格或换行。")
    if isinstance(exc, openai.RateLimitError):
        return "触发限流或余额不足（HTTP 429）。请检查账户额度，或稍后再试。"
    if isinstance(exc, openai.NotFoundError):
        return ("模型不存在（HTTP 404）。请检查 .env 里的 LLM_MODEL 是否拼写正确，"
                "以及它是否属于你当前使用的供应商。")
    if isinstance(exc, openai.APIConnectionError):
        return ("连不上 API 服务。请检查网络，或确认 .env 里的 LLM_BASE_URL 是否正确。")
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def main() -> None:
    args = sys.argv[1:]

    if args and args[0] in {"--tools", "-t"}:
        asyncio.run(list_tools_only())
        return

    debug = "--debug" in args
    args = [a for a in args if a != "--debug"]

    try:
        if args:
            asyncio.run(run_once(" ".join(args)))
        else:
            asyncio.run(interactive())
    except KeyboardInterrupt:
        print()
    except Exception as exc:  # noqa: BLE001
        if debug:
            raise
        sys.stdout.flush()  # 保证 stdout 的进度日志先于 stderr 的报错出现
        print(f"\n[错误] {_explain(exc)}\n", file=sys.stderr)
        print("（加 --debug 参数可查看完整堆栈）", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
