"""
Mock LLM 服务 —— 一个最小的、OpenAI 兼容的本地假模型。

为什么需要它：
    真实 API key 有额度、网络、限流等不确定性。但在答辩现场，
    我们又必须证明「智能体的真实代码路径是通的」——包括
    openai SDK 的调用、HTTP 传输、tool_calls 的解析。

    这个脚本用标准库起一个 HTTP 服务，实现 POST /v1/chat/completions，
    按剧本返回 OpenAI 格式的 tool_calls。把 LLM_BASE_URL 指向它，
    整条真实链路（LLMClient → openai SDK → HTTP → 编排循环）就能跑通，
    且完全不需要真 key、不消耗额度、断网也能用。

用法：
    # 终端 A：启动假模型（默认 8077 端口）
    python scripts/mock_llm_server.py

    # 终端 B：让智能体连它
    LLM_API_KEY=mock LLM_BASE_URL=http://127.0.0.1:8077/v1 LLM_MODEL=mock \\
        python run_agent.py "帮我分析 data/sample_grades.csv"
"""

from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CSV_PATH = DATA / "sample_grades.csv"
PDF_PATH = DATA / "sample_paper.pdf"

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8077

# 剧本：第 1 轮列文件，第 2 轮分析 CSV，第 3 轮读 PDF，第 4 轮作答
PLAN = [
    ("tool_list_local_files", {}),
    ("tool_analyze_csv", {"path": str(CSV_PATH), "preview_rows": 2}),
    ("tool_read_pdf", {"path": str(PDF_PATH), "start_page": 1, "end_page": 1}),
]


def count_tool_calls(messages: list[dict]) -> int:
    return sum(len(m.get("tool_calls") or []) for m in messages if m.get("role") == "assistant")


def last_tool_content(messages: list[dict], tool_name: str) -> str | None:
    order: list[str] = []
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                order.append(tc["function"]["name"])
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    for name, tm in zip(order, tool_msgs):
        if name == tool_name:
            return tm.get("content")
    return None


def compose_final_answer(messages: list[dict]) -> str:
    """基于真实工具返回的数据拼出结论 —— 与离线演示同一套逻辑。"""
    lines: list[str] = []

    raw = last_tool_content(messages, "tool_analyze_csv")
    if raw:
        try:
            r = json.loads(raw)
            lines.append(f"这份成绩表共 {r['row_count']} 条记录、{r['column_count']} 个字段。")
            for c in r.get("columns", []):
                if c["name"].startswith("grade_"):
                    lines.append(
                        f"- {c['name']}：均值 {c['mean']}，区间 [{c['min']}, {c['max']}]"
                    )
        except json.JSONDecodeError:
            pass

    raw = last_tool_content(messages, "tool_read_pdf")
    if raw:
        try:
            p = json.loads(raw)
            page = (p.get("pages") or [{}])[0]
            snippet = (page.get("text") or "").replace("\n", " ")[:120]
            lines.append(f"论文共 {p['page_count']} 页，第一页开头：{snippet}…")
        except json.JSONDecodeError:
            pass

    lines.append("（以上结论均由工具真实取证得出，本条回答由本地 mock 模型生成。）")
    return "\n".join(lines)


def build_response(messages: list[dict]) -> dict:
    done = count_tool_calls(messages)

    if done < len(PLAN):
        name, args = PLAN[done]
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call_{done}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }],
        }
        finish = "tool_calls"
    else:
        message = {"role": "assistant", "content": compose_final_answer(messages)}
        finish = "stop"

    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "mock-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [])

        print(f"[mock] 收到请求，历史中已有 {count_tool_calls(messages)} 次工具调用")
        payload = json.dumps(build_response(messages), ensure_ascii=False).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        pass  # 静音默认日志，保持输出干净


def main() -> None:
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[mock] OpenAI 兼容假模型已启动: http://127.0.0.1:{PORT}/v1")
    print(f"[mock] 剧本: {[p[0] for p in PLAN]}")
    print("[mock] Ctrl+C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock] 已停止")


if __name__ == "__main__":
    main()
