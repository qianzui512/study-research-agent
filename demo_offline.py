"""
离线演示 —— 不需要任何 API key，也能证明整条架构是通的。

关键设计：这里**复用真实的编排循环**（agent.loop.StudyScout），
只把「LLM」换成一个脚本化的桩（ScriptedLLM）。也就是说：

    ✅ MCP 服务端是真的（真实 stdio 子进程，真实 JSON-RPC 握手）
    ✅ MCP 客户端是真的（真实 list_tools / call_tool）
    ✅ 编排循环是真的（真实的决策 → 调用 → 回灌 → 再决策）
    ✅ 三个工具是真的（真的读 CSV、真的解析 PDF）
    ⬜ 只有「决策」这一步是脚本写死的，因为离线环境没有 LLM

用法：
    python demo_offline.py
    python demo_offline.py --with-web     # 额外演示网页抓取（需要联网）
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from agent.loop import StudyScout  # noqa: E402

DATA = ROOT / "data"
CSV_PATH = DATA / "sample_grades.csv"
PDF_PATH = DATA / "sample_paper.pdf"


# ---------------------------------------------------------------------------
# 脚本化的「假 LLM」—— 只负责按剧本产出工具调用，模拟 LLM 的决策
# ---------------------------------------------------------------------------
@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction


@dataclass
class FakeMessage:
    content: str | None = None
    tool_calls: list[FakeToolCall] | None = field(default=None)


class ScriptedLLM:
    """按剧本走：前 N 轮各调一个工具，最后基于真实工具结果写总结。"""

    def __init__(self, plan: list[tuple[str, dict[str, Any]]]) -> None:
        self.plan = plan

    def chat(self, messages: list[dict[str, Any]],
             tools: list[dict[str, Any]] | None = None,
             temperature: float = 0.2) -> FakeMessage:
        done = sum(len(m.get("tool_calls") or []) for m in messages if m.get("role") == "assistant")

        if done < len(self.plan):
            name, args = self.plan[done]
            return FakeMessage(
                content=f"我需要先调用 {name} 来取证。",
                tool_calls=[FakeToolCall(
                    id=f"call_{done}",
                    function=FakeFunction(name=name, arguments=json.dumps(args, ensure_ascii=False)),
                )],
            )

        return FakeMessage(content=self._compose_answer(messages), tool_calls=None)

    @staticmethod
    def _last_tool_payload(messages: list[dict[str, Any]], tool_name: str) -> str | None:
        """从消息历史里取指定工具最近一次的返回内容。"""
        # assistant 消息里记录了调用顺序，tool 消息按顺序对应
        call_order: list[str] = []
        for m in messages:
            if m.get("role") == "assistant":
                for tc in m.get("tool_calls") or []:
                    call_order.append(tc["function"]["name"])
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        for name, tm in zip(call_order, tool_msgs):
            if name == tool_name:
                return tm.get("content")
        return None

    def _compose_answer(self, messages: list[dict[str, Any]]) -> str:
        """基于真实工具返回的数据，拼出有据可查的总结（不是硬编码的假话）。"""
        lines: list[str] = []

        files_raw = self._last_tool_payload(messages, "tool_list_local_files")
        if files_raw:
            try:
                files = json.loads(files_raw)
                names = ", ".join(f["name"] for f in files.get("files", []))
                lines.append(f"**本地资料盘点**：在 data 目录下找到 {files.get('count', 0)} 个文件 —— {names}。")
            except json.JSONDecodeError:
                pass

        csv_raw = self._last_tool_payload(messages, "tool_analyze_csv")
        if csv_raw:
            try:
                report = json.loads(csv_raw)
                lines.append(
                    f"**成绩表概览**：共 {report['row_count']} 条记录、{report['column_count']} 个字段。"
                )
                for col in report.get("columns", []):
                    if col["name"].startswith("grade_"):
                        lines.append(
                            f"- `{col['name']}`：均值 {col['mean']}，中位数 {col['median']}，"
                            f"区间 [{col['min']}, {col['max']}]"
                        )
            except json.JSONDecodeError:
                pass

        pdf_raw = self._last_tool_payload(messages, "tool_read_pdf")
        if pdf_raw:
            try:
                pdf = json.loads(pdf_raw)
                first = (pdf.get("pages") or [{}])[0]
                snippet = (first.get("text") or "").replace("\n", " ")[:160]
                lines.append(
                    f"**论文摘要**：PDF 共 {pdf['page_count']} 页，本次读取 {pdf['pages_returned']} 页。"
                    f"开头内容：{snippet}…"
                )
            except json.JSONDecodeError:
                pass

        web_raw = self._last_tool_payload(messages, "tool_fetch_webpage")
        if web_raw:
            try:
                page = json.loads(web_raw)
                lines.append(
                    f"**网页抓取**：成功抓取《{page.get('title', '(无标题)')}》，"
                    f"正文 {page.get('char_count', 0)} 字符。"
                )
            except json.JSONDecodeError:
                pass

        lines.append(
            "\n以上每一条结论都来自真实工具调用，可对照上方的调用轨迹逐条核验。"
            "接入真实 LLM 后，同样的流程会由模型自行决定调用哪些工具、调用几次。"
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 演示主流程
# ---------------------------------------------------------------------------
def _printer(kind: str, data: dict[str, Any]) -> None:
    if kind == "session_start":
        print(f"\n[MCP] 已通过 stdio 连上服务端，发现 {len(data['tools'])} 个工具：")
        for t in data["tools"]:
            print(f"      • {t}")
    elif kind == "tool_call":
        args = json.dumps(data["arguments"], ensure_ascii=False)
        print(f"\n[第 {data['step']} 轮] 决策 → 调用 {data['tool']}({args})")
    elif kind == "tool_result":
        status = "❌ 失败" if data["is_error"] else "✅ 成功"
        preview = data["preview"].replace("\n", " ")[:150]
        print(f"          结果 {status}: {preview}…")
    elif kind == "final_answer":
        print(f"\n[第 {data['step']} 轮] 决策 → 材料已足够，生成最终答案")
    elif kind == "max_steps_reached":
        print("\n[!] 达到最大轮数")


async def main() -> None:
    with_web = "--with-web" in sys.argv

    plan: list[tuple[str, dict[str, Any]]] = [
        ("tool_list_local_files", {}),
        ("tool_analyze_csv", {"path": str(CSV_PATH), "preview_rows": 2}),
        ("tool_read_pdf", {"path": str(PDF_PATH), "start_page": 1, "end_page": 1}),
    ]
    if with_web:
        plan.append(("tool_fetch_webpage", {"url": "https://example.com", "max_chars": 800}))

    print("=" * 68)
    print("StudyScout 离线演示（无需 API key）")
    print("=" * 68)
    print(f"用户提问：请先看看本地有哪些资料，然后分析成绩表，并读一下那篇论文的第一页。")
    print(f"\n编排计划（由脚本化 LLM 提供）：{[p[0] for p in plan]}")

    scout = StudyScout(llm=ScriptedLLM(plan), on_event=_printer)  # type: ignore[arg-type]
    result = await scout.run(
        "请先看看本地有哪些资料，然后分析成绩表，并读一下那篇论文的第一页。"
    )

    print("\n" + "=" * 68)
    print("最终回答")
    print("=" * 68)
    print(result["answer"])
    print("=" * 68)
    print(f"统计：{result['steps']} 轮决策，{len(result['trace'])} 次工具调用")
    for item in result["trace"]:
        flag = "错误" if item["is_error"] else "成功"
        print(f"  · 第{item['step']}轮 {item['tool']} → {flag}")


if __name__ == "__main__":
    asyncio.run(main())
