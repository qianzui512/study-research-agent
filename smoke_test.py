"""
真实密钥跑通自检 —— 6 个阶段，逐层定位问题。

为什么要分层：真实 LLM 跑不通时，原因可能在完全不同的地方
（key 错 / 网络不通 / 工具坏 / 模型不调工具），混在一起排查会很痛苦。
本脚本把链路拆成 6 层，哪层 FAIL 就只查那一层。

用法：
    python smoke_test.py                 # 全量（需在 .env 填好真实 key）
    python smoke_test.py --skip-llm      # 只跑前 4 层（不需要 key，验证工具与协议）
    python smoke_test.py --web-url URL   # 换一个网页测试抓取
    python smoke_test.py --keep-going    # 某层失败也继续跑后续层
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Windows 控制台默认是 GBK（cp936）。遇到编不出的字符（如 ✓ / ⚠）会直接抛
# UnicodeEncodeError 中断程序。这里只放宽错误策略、不强制改编码 ——
# 强制 utf-8 在 GBK 终端里会把中文显示成乱码，反而不如原样输出。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:  # pragma: no cover
        pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

# 关掉 httpx 每次请求都打的 INFO 日志，避免污染自检输出
import logging  # noqa: E402

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

DATA = ROOT / "data"
CSV_PATH = DATA / "sample_grades.csv"
PDF_PATH = DATA / "sample_paper.pdf"
DEFAULT_WEB = "https://example.com"

# 与 agent/llm.py 保持一致的占位词判定
_PLACEHOLDER_HINTS = ("在这里填", "你的", "your-key", "your_key", "填你的", "xxx", "占位")

results: list[tuple[int, str, bool, str]] = []


def record(stage: int, name: str, ok: bool, detail: str = "") -> bool:
    results.append((stage, name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if detail:
        for line in str(detail).splitlines():
            print(f"         {line}")
    return ok


def banner(stage: int, title: str) -> None:
    print(f"\n{'=' * 66}")
    print(f"阶段 {stage}/6  {title}")
    print("=" * 66)


# ---------------------------------------------------------------------------
# 阶段 1：环境与依赖
# ---------------------------------------------------------------------------
def stage1_env() -> bool:
    banner(1, "环境与依赖")
    ok = True

    v = sys.version_info
    ok &= record(1, f"Python 版本 {v.major}.{v.minor}.{v.micro}",
                 v >= (3, 10), "" if v >= (3, 10) else "需要 3.10+（用到了 X | None 语法）")

    missing = []
    for mod, pip_name in [
        ("mcp", "mcp"), ("openai", "openai"), ("httpx", "httpx"),
        ("bs4", "beautifulsoup4"), ("pypdf", "pypdf"), ("dotenv", "python-dotenv"),
    ]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip_name)
    ok &= record(1, "核心依赖全部可导入", not missing,
                 "" if not missing else f"缺失: {missing}，请运行 pip install -r requirements.txt")

    env_file = ROOT / ".env"
    ok &= record(1, ".env 配置文件存在", env_file.exists(),
                 "" if env_file.exists() else "请复制 .env.example 为 .env")

    import os
    key = (os.getenv("LLM_API_KEY") or "").strip()
    base = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    if not key:
        shown = "(空)"
    elif any(p in key.lower() for p in _PLACEHOLDER_HINTS):
        shown = f"仍是占位符 {key!r} ← 需要替换成真实密钥"
    elif not key.isascii():
        shown = "(含非 ASCII 字符)"
    else:
        shown = f"{key[:7]}...{key[-4:]}"

    record(1, "当前配置", True,
           f"LLM_MODEL    = {model}\nLLM_BASE_URL = {base}\nLLM_API_KEY  = {shown}\n"
           f"LLM_THINKING = {os.getenv('LLM_THINKING', 'disabled')}")

    if "deepseek" in base.lower() and "deepseek-chat" in model.lower():
        record(1, "模型名检查", False,
               "deepseek-chat 已从官方文档下线，请改用 deepseek-flash 或 deepseek-v4-pro")

    return ok


# ---------------------------------------------------------------------------
# 阶段 2：数据文件
# ---------------------------------------------------------------------------
def stage2_data() -> bool:
    banner(2, "演示数据文件")
    ok = True

    if not CSV_PATH.exists():
        return record(2, "CSV 存在", False, f"缺失: {CSV_PATH}，请运行 python scripts/make_sample_pdf.py")
    import csv
    with CSV_PATH.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    ok &= record(2, f"CSV 可解析（{len(rows)} 行 × {len(rows[0]) if rows else 0} 列）", bool(rows))

    if not PDF_PATH.exists():
        return record(2, "PDF 存在", False, f"缺失: {PDF_PATH}，请运行 python scripts/make_sample_pdf.py")
    from pypdf import PdfReader
    reader = PdfReader(str(PDF_PATH))
    ok &= record(2, f"PDF 可解析（{len(reader.pages)} 页）", len(reader.pages) > 0)
    return ok


# ---------------------------------------------------------------------------
# 阶段 3 + 4：MCP 协议与四个工具
# ---------------------------------------------------------------------------
async def stage34_mcp_and_tools(web_url: str) -> bool:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    from agent.loop import _result_to_text, server_params
    from agent.mcp_compat import get_input_schema, get_is_error

    banner(3, "MCP 协议握手与工具 schema")
    ok = True

    async with stdio_client(server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            record(3, "stdio 连接 + initialize 握手成功", True)

            listed = await session.list_tools()
            tools = list(listed.tools)
            names = [t.name for t in tools]
            ok &= record(3, f"发现 {len(tools)} 个工具", len(tools) == 4, ", ".join(names))

            for t in tools:
                schema = get_input_schema(t)
                props = list((schema.get("properties") or {}).keys())
                ok &= record(3, f"  {t.name} 的 schema 完整（参数: {', '.join(props) or '无'}）",
                             bool(props) or t.name == "tool_list_local_files")

            banner(4, "四个工具直调（绕过 LLM，纯工具层）")

            cases = [
                ("tool_list_local_files", {}, "本地文件发现"),
                ("tool_analyze_csv", {"path": str(CSV_PATH)}, "CSV 统计解析"),
                ("tool_read_pdf", {"path": str(PDF_PATH), "start_page": 1, "end_page": 1}, "PDF 文本提取"),
                ("tool_fetch_webpage", {"url": web_url}, "网页正文抓取"),
            ]
            for tool, args, label in cases:
                t0 = time.time()
                try:
                    res = await session.call_tool(tool, args)
                    text = _result_to_text(res)
                    err = get_is_error(res)
                    cost = time.time() - t0
                    if err:
                        ok &= record(4, f"{label}（{tool}）", False, text[:300])
                    else:
                        snippet = " ".join(text.split())[:150]
                        ok &= record(4, f"{label}（{tool}）  {cost:.2f}s", True, snippet)
                except Exception as exc:  # noqa: BLE001
                    ok &= record(4, f"{label}（{tool}）", False,
                                 f"{type(exc).__name__}: {exc}\n"
                                 f"若为网页抓取失败，多半是网络或目标站点限制，可换 --web-url")
    return ok


# ---------------------------------------------------------------------------
# 阶段 5：LLM 连通性
# ---------------------------------------------------------------------------
def stage5_llm() -> bool:
    banner(5, "LLM 连通性（最小调用，不涉及工具）")
    from agent.llm import LLMClient

    try:
        client = LLMClient()
    except RuntimeError as exc:
        return record(5, "读取配置", False, str(exc))

    thinking = "enabled（思考模式）" if client.thinking_enabled else "disabled（非思考模式）"
    record(5, "配置校验通过", True,
           f"model={client.model}  base_url={client.base_url}\nthinking={thinking}")

    t0 = time.time()
    try:
        msg = client.chat([{"role": "user", "content": "只回复两个字：收到"}])
    except Exception as exc:  # noqa: BLE001
        from run_agent import _explain
        return record(5, "调用 chat.completions", False, _explain(exc))

    cost = time.time() - t0
    content = (msg.content or "").strip()
    ok = bool(content)
    record(5, f"模型正常回复  {cost:.2f}s", ok,
           f"回复内容: {content[:80]}" if ok else "返回内容为空")
    return ok


# ---------------------------------------------------------------------------
# 阶段 6：端到端编排（真实模型自主决策）
# ---------------------------------------------------------------------------
async def stage6_end_to_end(web_url: str) -> bool:
    banner(6, "端到端编排（真实模型自主调用工具）")
    from agent.loop import StudyScout

    question = (
        "请依次完成四件事，每件都要真的调用工具，不要凭记忆回答：\n"
        "1) 用 tool_list_local_files 看看 data 目录下有哪些资料；\n"
        "2) 用 tool_analyze_csv 分析 data/sample_grades.csv，告诉我各科平均分；\n"
        "3) 用 tool_read_pdf 读 data/sample_paper.pdf 的第 1 页，概括它的研究主题；\n"
        f"4) 用 tool_fetch_webpage 抓取 {web_url} ，告诉我它的标题。\n"
        "最后汇总成一段简短结论。"
    )
    print(f"  提问：{question.splitlines()[0]}…（共 {len(question.splitlines())} 行指令）\n")

    def printer(kind: str, data: dict) -> None:
        if kind == "session_start":
            print(f"  [MCP] 工具就绪: {', '.join(data['tools'])}")
        elif kind == "tool_call":
            args = json.dumps(data["arguments"], ensure_ascii=False)
            print(f"  [轮 {data['step']}] → {data['tool']}({args[:90]})")
        elif kind == "tool_result":
            flag = "错误" if data["is_error"] else "成功"
            print(f"  [轮 {data['step']}] ← {data['tool']} {flag}: "
                  f"{' '.join(data['preview'].split())[:100]}")
        elif kind == "final_answer":
            print(f"  [轮 {data['step']}] √ 输出最终答案")
        elif kind == "max_steps_reached":
            print(f"  [!] 达到最大轮数 {data['steps']}")

    scout = StudyScout(on_event=printer)
    try:
        result = await scout.run(question)
    except Exception as exc:  # noqa: BLE001
        from run_agent import _explain
        return record(6, "编排循环执行", False, _explain(exc))

    print("\n  ---------- 最终回答 ----------")
    print("  " + (result["answer"] or "(空)").replace("\n", "\n  ")[:1200])
    print("  ------------------------------\n")

    ok = True
    ok &= record(6, "产出了非空最终答案", bool(result["answer"].strip()))
    ok &= record(6, f"工具调用 {len(result['trace'])} 次，共 {result['steps']} 轮",
                 len(result["trace"]) > 0)

    used = {t["tool"] for t in result["trace"]}
    expected = {"tool_list_local_files", "tool_analyze_csv", "tool_read_pdf", "tool_fetch_webpage"}
    covered = used & expected
    ok &= record(6, f"工具覆盖 {len(covered)}/4：{', '.join(sorted(covered)) or '无'}",
                 len(covered) == 4,
                 "" if len(covered) == 4 else f"未触发: {', '.join(sorted(expected - used))}")

    failed = [t for t in result["trace"] if t["is_error"]]
    ok &= record(6, "所有工具调用均成功", not failed,
                 "" if not failed else f"失败: {[t['tool'] for t in failed]}")
    return ok


# ---------------------------------------------------------------------------
def check_interpreter() -> bool:
    """确认当前解释器就是项目的 .venv。

    这是最容易踩的坑：机器上同时有 conda / 系统 Python 时，`python smoke_test.py`
    可能根本没用到 .venv，于是「依赖明明装了却报缺失」。
    """
    candidates = [
        ROOT / ".venv" / "Scripts" / "python.exe",  # Windows
        ROOT / ".venv" / "bin" / "python",          # macOS / Linux
    ]
    venv_py = next((p for p in candidates if p.exists()), None)
    if venv_py is None:
        return True  # 没有 venv 就交给用户自己的环境，不拦

    cur = Path(sys.executable).resolve()
    try:
        same = cur == venv_py.resolve()
    except OSError:
        same = False
    if same:
        return True

    print()
    print("!" * 66)
    print("  [警告] 当前解释器不是项目的虚拟环境")
    print("!" * 66)
    print(f"  正在使用: {cur}")
    print(f"  应当使用: {venv_py}")
    print()
    print("  这会让依赖「明明装了却报缺失」。改用下面这条命令即可：")
    print(f'    "{venv_py}" smoke_test.py')
    print()
    print("  或在 cmd 中先激活（务必是项目内的 .venv）：")
    print(r"    .venv\Scripts\activate.bat")
    print("!" * 66)
    print()
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="StudyScout 真实密钥跑通自检")
    parser.add_argument("--skip-llm", action="store_true", help="跳过阶段 5、6（不需要 API key）")
    parser.add_argument("--web-url", default=DEFAULT_WEB, help=f"网页抓取测试目标，默认 {DEFAULT_WEB}")
    parser.add_argument("--keep-going", action="store_true", help="某阶段失败后仍继续")
    args = parser.parse_args()

    print("=" * 66)
    print("StudyScout 跑通自检")
    print("=" * 66)
    print(f"项目根目录: {ROOT}")
    print(f"解释器    : {sys.executable}")
    interp_ok = check_interpreter()

    ok = stage1_env() and interp_ok
    if not (ok or args.keep_going):
        print("\n阶段 1 未通过，先修环境。")
        return 1

    ok = stage2_data() and ok
    if not (ok or args.keep_going):
        print("\n阶段 2 未通过，先补数据文件。")
        return 1

    ok34 = asyncio.run(stage34_mcp_and_tools(args.web_url))
    ok = ok and ok34

    if not args.skip_llm:
        ok5 = stage5_llm()
        ok = ok and ok5
        if ok5 or args.keep_going:
            ok6 = asyncio.run(stage6_end_to_end(args.web_url))
            ok = ok and ok6

    total = len(results)
    passed = sum(1 for r in results if r[2])
    print("\n" + "=" * 66)
    print(f"结果：{passed}/{total} 项通过")
    print("=" * 66)

    if passed == total:
        if args.skip_llm:
            print("\n前 4 层全部通过：环境、数据、MCP 协议、四个工具都正常。")
            print("（本次跳过了阶段 5、6，尚未验证密钥与模型决策）")
            print("\n填好 .env 里的真实密钥后，跑完整版：")
            print("  python smoke_test.py")
        else:
            print("\n全链路跑通：环境 → 数据 → MCP 协议 → 4 个工具 → 密钥 → 模型决策。")
            print("可以正式使用了：")
            print('  python run_agent.py "帮我分析 data/sample_grades.csv，哪个专业成绩最好？"')
            print("  python run_agent.py          # 交互模式")
        return 0

    print("\n未通过项：")
    for stage, name, okk, detail in results:
        if not okk:
            print(f"  · [阶段 {stage}] {name}")
    print("\n按阶段定位：1=环境  2=数据  3=协议  4=工具  5=密钥  6=模型决策")
    return 1


if __name__ == "__main__":
    sys.exit(main())
