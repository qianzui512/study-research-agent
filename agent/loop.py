"""
编排循环 (Orchestration Loop) —— 智能体的「神经系统」。

职责：把 LLM 的「决策」和 MCP 工具的「执行」串成一个闭环。

    ┌─────────────┐   1. 用户提问
    │   用户       │ ─────────────────────────────┐
    └─────────────┘                              ▼
                                    ┌───────────────────────┐
                                    │  LLM (system prompt)  │
                                    └───────────┬───────────┘
                          2. 要调工具?           │  3. 直接回答?
                       ┌────────────────────────┴──────────┐
                       ▼                                   ▼
          ┌────────────────────────┐              ┌─────────────────┐
          │ MCP Client → MCP Server│              │   输出最终答案    │
          │  (stdio 标准协议)       │              └─────────────────┘
          └───────────┬────────────┘
                      │ 4. 工具返回结果
                      └──────► 回灌进消息历史，回到第 1 步（最多 MAX_STEPS 轮）

这就是作业要求的 Context Integration：智能体的「大脑」不是靠内置知识，
而是靠这条 MCP 通道去真实触达外部世界。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.llm import LLMClient, extract_reasoning
from agent.mcp_compat import get_input_schema, get_is_error, get_structured_content
from agent.prompts import build_messages

# 单次会话最多允许的工具调用轮数，防止无限循环烧 token
MAX_STEPS = 8
# 单个工具结果回灌给 LLM 时的字符上限
TOOL_RESULT_CHAR_CAP = 12000

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def server_params() -> StdioServerParameters:
    """构造启动 MCP 服务端的参数（stdio 子进程）。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        cwd=str(PROJECT_ROOT),
        env=env,
    )


def _result_to_text(result: Any) -> str:
    """把 MCP 的 CallToolResult 压成一段纯文本，喂回给 LLM。"""
    chunks: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        chunks.append(text if text is not None else str(block))

    if not chunks:
        structured = get_structured_content(result)
        if structured is not None:
            chunks.append(json.dumps(structured, ensure_ascii=False, indent=2))

    text = "\n".join(chunks).strip() or "(工具未返回任何内容)"
    if len(text) > TOOL_RESULT_CHAR_CAP:
        text = text[:TOOL_RESULT_CHAR_CAP] + "\n...[结果过长，已截断]"
    return text


def mcp_tools_to_openai(tools: list[Any]) -> list[dict[str, Any]]:
    """把 MCP 的 tool 描述转换成 OpenAI function-calling 的 tools 格式。

    这一步是两种协议的「翻译层」：MCP 用 inputSchema 描述参数，
    OpenAI 用 function.parameters，本质都是 JSON Schema。
    """
    converted = []
    for t in tools:
        converted.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": get_input_schema(t),
            },
        })
    return converted


class StudyScout:
    """智能体主体：一个 LLM + 一条 MCP 通道 + 一个编排循环。"""

    def __init__(self, llm: LLMClient | None = None,
                 on_event: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self.llm = llm
        self.on_event = on_event or (lambda kind, data: None)

    def _emit(self, kind: str, **data: Any) -> None:
        self.on_event(kind, data)

    async def run(self, user_input: str) -> dict[str, Any]:
        """跑一次完整的「提问 → 多轮工具调用 → 最终回答」。"""
        if self.llm is None:
            # 惰性创建：这样缺 API key 时报的是 LLMClient 里的友好提示，
            # 而不是一个 'NoneType' object has no attribute 'chat'
            self.llm = LLMClient()

        async with stdio_client(server_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                listed = await session.list_tools()
                mcp_tools = list(listed.tools)
                openai_tools = mcp_tools_to_openai(mcp_tools)

                self._emit("session_start", tools=[t.name for t in mcp_tools])

                messages: list[dict[str, Any]] = build_messages(user_input)
                trace: list[dict[str, Any]] = []

                for step in range(1, MAX_STEPS + 1):
                    msg = await asyncio.to_thread(
                        self.llm.chat, messages, openai_tools
                    )

                    # 没有工具调用 → 循环结束，这就是最终答案
                    if not getattr(msg, "tool_calls", None):
                        self._emit("final_answer", step=step, content=msg.content or "")
                        return {
                            "answer": msg.content or "",
                            "steps": step,
                            "trace": trace,
                            "tools_available": [t.name for t in mcp_tools],
                        }

                    # 把 LLM 这轮的「思考 + 工具请求」写进历史
                    assistant_msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    }

                    # DeepSeek 思考模式 + tools 的硬性要求：只要请求带了 tools，
                    # 后续每一轮都必须把上一轮的 reasoning_content 原样回传，
                    # 否则 API 返回 400。非思考模式该字段为空，不会写入。
                    reasoning = extract_reasoning(msg)
                    if reasoning:
                        assistant_msg["reasoning_content"] = reasoning

                    messages.append(assistant_msg)

                    # 逐个执行工具调用
                    for tc in msg.tool_calls:
                        name = tc.function.name
                        try:
                            args = json.loads(tc.function.arguments or "{}")
                        except json.JSONDecodeError as exc:
                            args = {}
                            self._emit("tool_error", tool=name, error=f"参数不是合法 JSON: {exc}")

                        self._emit("tool_call", step=step, tool=name, arguments=args)

                        try:
                            result = await session.call_tool(name, args)
                            text = _result_to_text(result)
                            is_error = get_is_error(result)
                        except Exception as exc:  # noqa: BLE001
                            text = f"工具执行异常: {type(exc).__name__}: {exc}"
                            is_error = True

                        self._emit(
                            "tool_result",
                            step=step,
                            tool=name,
                            is_error=is_error,
                            preview=text[:280],
                        )
                        trace.append({
                            "step": step,
                            "tool": name,
                            "arguments": args,
                            "is_error": is_error,
                            "result_preview": text[:600],
                        })

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": text,
                        })

                # 步数用尽仍未收敛
                self._emit("max_steps_reached", steps=MAX_STEPS)
                return {
                    "answer": "（已达到最大工具调用轮数，未能收敛出最终答案。）",
                    "steps": MAX_STEPS,
                    "trace": trace,
                    "tools_available": [t.name for t in mcp_tools],
                }


async def ask(user_input: str, verbose: bool = True) -> dict[str, Any]:
    """便捷函数：一行代码跑完一次提问。"""
    def printer(kind: str, data: dict[str, Any]) -> None:
        if not verbose:
            return
        if kind == "session_start":
            print(f"[MCP] 已连接，可用工具: {', '.join(data['tools'])}")
        elif kind == "tool_call":
            print(f"[轮次 {data['step']}] → 调用工具 {data['tool']}({json.dumps(data['arguments'], ensure_ascii=False)})")
        elif kind == "tool_result":
            flag = "错误" if data["is_error"] else "成功"
            print(f"[轮次 {data['step']}] ← {data['tool']} {flag}: {data['preview'][:120]}...")
        elif kind == "final_answer":
            print(f"[轮次 {data['step']}] ✓ 生成最终答案")

    scout = StudyScout(on_event=printer)
    return await scout.run(user_input)
