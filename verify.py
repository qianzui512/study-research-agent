"""
独立验证脚本 —— 不信任任何人的口头结论，自己跑一遍。

用法：
    python verify.py

它做 7 项检查，其中第 4 项是**篡改测试**：
把成绩表里的一个数字改掉，看智能体的结论是否跟着变。
如果结论是写死的，这项必然失败。这是判断「演示是不是演戏」的关键手段。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Windows 控制台默认是 GBK（cp936），遇到编不出的字符会抛 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:  # pragma: no cover
        pass

CSV_PATH = ROOT / "data" / "sample_grades.csv"
PDF_PATH = ROOT / "data" / "sample_paper.pdf"
TAMPER_PATH = ROOT / "data" / "_verify_tampered.csv"

results: list[tuple[str, bool, str]] = []

# Windows 中文环境下 subprocess 的坑（实测于 cmd.exe / 代码页 936）：
# MCP 服务端由 SDK 强制输出 UTF-8，而父进程 text=True 时默认按 locale
# （cp936 = GBK）解码，读线程抛 UnicodeDecodeError 后 proc.stdout 变成 None，
# 表现为 "'NoneType' object has no attribute 'splitlines'"。
# 所以父子两端都必须锁死 UTF-8，且解码留 errors="replace" 兜底。
CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def run_child(args: list[str], timeout: int = 60, **kwargs: Any) -> subprocess.CompletedProcess:
    """以 UTF-8 与子进程通信，避免 Windows 中文环境下的解码崩溃。"""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=CHILD_ENV,
        cwd=str(ROOT),
        timeout=timeout,
        **kwargs,
    )



def record(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    mark = "PASS" if passed else "FAIL"
    print(f"  [{mark}] {name}")
    if detail:
        print(f"         {detail}")


# ---------------------------------------------------------------------------
# 1. 依赖真的装上了
# ---------------------------------------------------------------------------
def check_deps() -> None:
    print("\n1) 依赖检查")
    try:
        import bs4, fpdf, httpx, mcp, openai, pypdf  # noqa: F401

        record("核心依赖可导入", True, f"mcp / openai / httpx / bs4 / pypdf / fpdf")
    except Exception as exc:  # noqa: BLE001
        record("核心依赖可导入", False, str(exc))


# ---------------------------------------------------------------------------
# 2. MCP 协议是真的 —— 绕过封装，直接发裸 JSON-RPC
# ---------------------------------------------------------------------------
def check_raw_mcp_protocol() -> None:
    print("\n2) MCP 协议真实性（裸 JSON-RPC 握手，绕过所有封装）")
    payload = "\n".join([
        json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "raw-probe", "version": "1.0"},
            },
        }),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ]) + "\n"

    try:
        proc = run_child([sys.executable, "-m", "mcp_server.server"], timeout=60, input=payload)
    except subprocess.TimeoutExpired:
        record("服务端响应裸 JSON-RPC", False, "超时")
        return

    stdout = proc.stdout or ""
    lines = [ln for ln in stdout.splitlines() if ln.strip().startswith("{")]
    if not lines:
        record("服务端响应裸 JSON-RPC", False,
               f"没有收到 JSON 响应。stdout 前 200 字: {stdout[:200]!r} / "
               f"stderr 前 200 字: {(proc.stderr or '')[:200]!r}")
        return

    record("服务端响应裸 JSON-RPC", True, f"收到 {len(lines)} 条 JSON-RPC 消息")

    # 按 id 匹配响应，而不是按到达顺序 —— 服务端可能先推送通知消息，
    # 用下标取 lines[1] 会偶发取错对象，导致误判（实测出现过）。
    responses: dict[Any, dict] = {}
    for ln in lines:
        try:
            msg = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if "id" in msg:
            responses[msg["id"]] = msg

    # 校验 initialize 响应
    init = responses.get(1, {})
    server_name = init.get("result", {}).get("serverInfo", {}).get("name")
    proto = init.get("result", {}).get("protocolVersion")
    record(
        "initialize 返回合规握手",
        server_name == "study-research-agent" and bool(proto),
        f"serverInfo.name={server_name!r}, protocolVersion={proto!r}",
    )

    # 校验 tools/list
    listed = responses.get(2, {}).get("result", {}).get("tools", [])
    if listed:
        names = [t["name"] for t in listed]
        record(
            "tools/list 返回 4 个工具且带完整 schema",
            len(listed) == 4 and all("inputSchema" in t for t in listed),
            f"工具: {', '.join(names)}",
        )
    else:
        record("tools/list 返回 4 个工具且带完整 schema", False,
               f"缺少 tools/list 响应（收到的 id: {sorted(responses)}）")


# ---------------------------------------------------------------------------
# 3. 技能真的在干活
# ---------------------------------------------------------------------------
def check_skills_work() -> None:
    print("\n3) 技能真实性")
    from mcp_server.skills import analyze_csv, read_pdf

    report = analyze_csv(str(CSV_PATH))
    record(
        "CSV 解析出真实行数/列数",
        report["row_count"] == 12 and report["column_count"] == 8,
        f"{report['row_count']} 行 × {report['column_count']} 列",
    )

    # 用标准库独立复算，不采信项目自己的计算结果
    import csv as csvmod

    with CSV_PATH.open(encoding="utf-8-sig") as f:
        raw = list(csvmod.DictReader(f))
    expected_mean = statistics.fmean(float(r["grade_math"]) for r in raw)
    got_mean = {c["name"]: c for c in report["columns"]}["grade_math"]["mean"]
    record(
        "统计值与独立复算一致",
        abs(expected_mean - got_mean) < 1e-4,
        f"独立复算={expected_mean:.4f}, 项目输出={got_mean}",
    )

    # PDF 不同页应当抽出不同内容
    p1 = read_pdf(str(PDF_PATH), 1, 1)["pages"][0]["text"]
    p2 = read_pdf(str(PDF_PATH), 2, 2)["pages"][0]["text"]
    record(
        "PDF 逐页抽取内容确实不同",
        p1.strip() != p2.strip() and len(p1) > 20 and len(p2) > 20,
        f"第1页 {len(p1)} 字符 / 第2页 {len(p2)} 字符，内容不同",
    )


# ---------------------------------------------------------------------------
# 4. 篡改测试 —— 最关键的一项
# ---------------------------------------------------------------------------
async def check_tamper() -> None:
    print("\n4) 篡改测试（证明结论不是写死的）")

    from agent.loop import StudyScout
    from demo_offline import ScriptedLLM

    original = CSV_PATH.read_text(encoding="utf-8")
    try:
        # 把 2023001 的数学成绩从 88 改成 100，均值应当从 83.8333 变成 84.8333
        tampered = original.replace(",88,92,95,", ",100,92,95,")
        if tampered == original:
            record("篡改数据成功", False, "替换未生效，样例数据可能已变")
            return
        TAMPER_PATH.write_text(tampered, encoding="utf-8")

        with TAMPER_PATH.open(encoding="utf-8") as f:
            import csv as csvmod

            rows = list(csvmod.DictReader(f))
        expected = statistics.fmean(float(r["grade_math"]) for r in rows)

        plan = [("tool_analyze_csv", {"path": str(TAMPER_PATH), "preview_rows": 1})]
        scout = StudyScout(llm=ScriptedLLM(plan))  # type: ignore[arg-type]
        result = await scout.run("分析一下这份成绩表。")

        answer = result["answer"]
        has_new = f"{expected:.4f}" in answer
        has_old = "83.8333" in answer

        record(
            "篡改后的新均值出现在结论中",
            has_new,
            f"改后独立复算均值={expected:.4f}，结论中{'出现' if has_new else '未出现'}",
        )
        record(
            "原始均值不再出现（未被写死）",
            not has_old,
            "结论里没有残留 83.8333，说明结论完全由数据驱动",
        )
    finally:
        TAMPER_PATH.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 5. 安全护栏真的拦得住
# ---------------------------------------------------------------------------
async def check_path_guard() -> None:
    print("\n5) 安全护栏")
    from agent.loop import StudyScout
    from demo_offline import ScriptedLLM

    plan = [("tool_analyze_csv", {"path": "C:/Windows/System32/drivers/etc/hosts"})]
    scout = StudyScout(llm=ScriptedLLM(plan))  # type: ignore[arg-type]
    result = await scout.run("读系统文件。")

    blocked = result["trace"][0]["is_error"] and "安全策略拒绝" in result["trace"][0]["result_preview"]
    record("项目目录之外的路径被拒绝", blocked, result["trace"][0]["result_preview"][:90])


# ---------------------------------------------------------------------------
# 6. 编排循环的 trace 与真实工具输出对得上
# ---------------------------------------------------------------------------
async def check_loop_trace() -> None:
    print("\n6) 编排循环真实性")
    from agent.loop import StudyScout
    from demo_offline import ScriptedLLM

    plan = [
        ("tool_list_local_files", {}),
        ("tool_analyze_csv", {"path": str(CSV_PATH), "preview_rows": 1}),
        ("tool_read_pdf", {"path": str(PDF_PATH), "start_page": 1, "end_page": 1}),
    ]
    scout = StudyScout(llm=ScriptedLLM(plan))  # type: ignore[arg-type]
    result = await scout.run("盘点资料。")

    record(
        "走了 3 轮工具调用后收敛",
        result["steps"] == 4 and len(result["trace"]) == 3,
        f"{result['steps']} 轮决策 / {len(result['trace'])} 次调用",
    )
    record(
        "三次调用全部成功",
        all(not t["is_error"] for t in result["trace"]),
        " / ".join(t["tool"] for t in result["trace"]),
    )
    record(
        "MCP 服务端确实暴露了 4 个工具",
        len(result["tools_available"]) == 4,
        ", ".join(result["tools_available"]),
    )


# ---------------------------------------------------------------------------
# 7. 官方测试套件
# ---------------------------------------------------------------------------
def check_test_suites() -> None:
    print("\n7) 官方测试套件")
    for script, label in [("tests/test_skills.py", "技能单测"),
                          ("tests/test_agent_loop.py", "编排集成测试"),
                          ("tests/test_thinking_mode.py", "思考模式回归")]:
        proc = run_child([sys.executable, script], timeout=180)
        # unittest 把 "Ran N tests" 写在 stderr，自定义脚本写在 stdout，
        # 两路都要看，否则明明跑过了却摘不出摘要行。
        combined = (proc.stdout or "") + (proc.stderr or "")
        lines = combined.splitlines()
        summary = next((ln for ln in lines if ln.startswith("Ran ")), None) \
            or next((ln for ln in lines if ln.startswith("结果：")), None)
        record(f"{label} 全过", proc.returncode == 0, summary or combined[-150:])


async def main() -> None:
    print("=" * 70)
    print("StudyScout 独立验证报告")
    print("=" * 70)

    check_deps()
    check_raw_mcp_protocol()
    check_skills_work()
    await check_tamper()
    await check_path_guard()
    await check_loop_trace()
    check_test_suites()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 70)
    print(f"结果：{passed}/{total} 项通过")
    if passed != total:
        print("失败项：")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name}: {detail}")
    print("=" * 70)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
