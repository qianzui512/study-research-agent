"""
回归测试 —— DeepSeek 思考模式 + tools 的 reasoning_content 回传。

背景（官方文档明确要求）：
    携带 tools 的请求，后续所有请求必须完整回传 reasoning_content，
    即使该轮未实际调用工具；否则 API 返回 400。
    —— https://api-docs.deepseek.com/zh-cn/guides/thinking_mode#tool-calls

本测试用一个会记录请求体的假服务端，验证三件事：
    1. thinking 参数只发给 DeepSeek 端点（发给 OpenAI 会 400）
    2. 第一轮的 reasoning_content 确实被写回第二轮请求的 messages
    3. 关闭思考模式时不会凭空塞 reasoning_content

不需要真实 API key，也不联网。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass


class _Handler(BaseHTTPRequestHandler):
    bodies: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        _Handler.bodies.append(body)

        if len(_Handler.bodies) == 1:
            message = {
                "role": "assistant",
                "content": "",
                "reasoning_content": "用户想看本地资料，我先列出 data 目录。",
                "tool_calls": [{
                    "id": "call_test_1",
                    "type": "function",
                    "function": {"name": "tool_list_local_files", "arguments": "{}"},
                }],
            }
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": "data 目录下有 2 个文件。"}
            finish = "stop"

        payload = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "deepseek-flash",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # 静默
        pass


def main() -> int:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # 路径里带 deepseek 字样，让 LLMClient 认为是 DeepSeek 端点从而注入 thinking
    os.environ["LLM_API_KEY"] = "sk-test-only-000000"
    os.environ["LLM_BASE_URL"] = f"http://127.0.0.1:{port}/deepseek"
    os.environ["LLM_MODEL"] = "deepseek-flash"
    os.environ["LLM_THINKING"] = "enabled"

    import asyncio

    from agent.llm import LLMClient
    from agent.loop import StudyScout

    passed = 0
    total = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, total
        total += 1
        passed += 1 if ok else 0
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if detail:
            print(f"         {detail}")

    print("=" * 66)
    print("思考模式 reasoning_content 回传测试")
    print("=" * 66)

    client = LLMClient()
    check("LLMClient 识别为 DeepSeek 端点", client._extra_body is not None,
          f"extra_body={client._extra_body}")
    check("thinking=enabled 被正确解析", client.thinking_enabled)

    result = asyncio.run(StudyScout().run("看看本地有哪些资料"))

    check("编排循环正常收敛", bool(result["answer"]),
          f"answer={result['answer'][:60]!r}  轮数={result['steps']}")
    check("发生了 2 次模型请求", len(_Handler.bodies) == 2,
          f"实际 {len(_Handler.bodies)} 次")

    if len(_Handler.bodies) >= 1:
        first = _Handler.bodies[0]
        check("首轮请求携带 thinking 参数",
              first.get("thinking") == {"type": "enabled"},
              f"thinking={first.get('thinking')}  reasoning_effort={first.get('reasoning_effort')}")
        check("首轮请求携带 4 个工具的 schema",
              len(first.get("tools") or []) == 4)

    if len(_Handler.bodies) >= 2:
        second = _Handler.bodies[1]
        assistant_msgs = [m for m in second["messages"] if m.get("role") == "assistant"]
        with_reasoning = [m for m in assistant_msgs if m.get("reasoning_content")]
        check("第二轮请求回传了 reasoning_content", bool(with_reasoning),
              f"回传内容={with_reasoning[0]['reasoning_content'][:40]!r}" if with_reasoning
              else "未找到 reasoning_content，真实 DeepSeek 会返回 400")
        tool_msgs = [m for m in second["messages"] if m.get("role") == "tool"]
        check("工具结果以 role=tool 正确回传", len(tool_msgs) == 1,
              f"tool 消息数={len(tool_msgs)}")

    # 关闭思考模式时不应出现 reasoning_content
    _Handler.bodies.clear()
    os.environ["LLM_THINKING"] = "disabled"
    client_off = LLMClient()
    asyncio.run(StudyScout(llm=client_off).run("再看看资料"))
    if _Handler.bodies:
        has_rc = any(m.get("reasoning_content")
                     for m in _Handler.bodies[0]["messages"]
                     if m.get("role") == "assistant")
        check("thinking=disabled 时不注入 reasoning_content", not has_rc)
        check("thinking=disabled 时请求体带 disabled 标记",
              _Handler.bodies[0].get("thinking") == {"type": "disabled"},
              f"thinking={_Handler.bodies[0].get('thinking')}")

    server.shutdown()

    print("\n" + "=" * 66)
    print(f"结果：{passed}/{total} 项通过")
    print("=" * 66)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
