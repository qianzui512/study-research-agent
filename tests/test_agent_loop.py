"""
编排循环集成测试 —— 用脚本化 LLM 驱动**真实的** MCP 服务端跑完整闭环。

这是本项目最有价值的一个测试：它不需要 API key，却真实验证了
「决策 → MCP 调用 → 结果回灌 → 再决策 → 收敛」这条链路。

运行：
    python tests/test_agent_loop.py
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.loop import StudyScout, mcp_tools_to_openai  # noqa: E402
from demo_offline import CSV_PATH, PDF_PATH, ScriptedLLM  # noqa: E402


class TestSchemaConversion(unittest.TestCase):
    """MCP 的 tool 描述 → OpenAI function-calling 格式。"""

    def test_converts_mcp_tool_to_openai_format(self) -> None:
        class FakeTool:
            name = "tool_demo"
            description = "示例工具"
            inputSchema = {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            }

        out = mcp_tools_to_openai([FakeTool()])
        self.assertEqual(len(out), 1)
        fn = out[0]["function"]
        self.assertEqual(out[0]["type"], "function")
        self.assertEqual(fn["name"], "tool_demo")
        self.assertEqual(fn["description"], "示例工具")
        self.assertIn("x", fn["parameters"]["properties"])

    def test_missing_schema_falls_back_to_empty_object(self) -> None:
        class BareTool:
            name = "tool_bare"
            description = ""
            inputSchema = None

        out = mcp_tools_to_openai([BareTool()])
        self.assertEqual(out[0]["function"]["parameters"], {"type": "object", "properties": {}})


class TestOrchestrationLoop(unittest.TestCase):
    """端到端：真实 MCP 子进程 + 真实工具 + 脚本化 LLM。"""

    def test_full_loop_with_real_mcp_server(self) -> None:
        plan = [
            ("tool_list_local_files", {}),
            ("tool_analyze_csv", {"path": str(CSV_PATH), "preview_rows": 1}),
            ("tool_read_pdf", {"path": str(PDF_PATH), "start_page": 1, "end_page": 1}),
        ]
        scout = StudyScout(llm=ScriptedLLM(plan))  # type: ignore[arg-type]

        events: list[str] = []
        scout.on_event = lambda kind, data: events.append(kind)

        result = asyncio.run(scout.run("请盘点资料并分析成绩表，再读一下论文第一页。"))

        # 4 个工具都被 MCP 服务端暴露出来
        self.assertEqual(len(result["tools_available"]), 4)
        # 3 次工具调用 + 1 次收敛 = 4 轮
        self.assertEqual(result["steps"], 4)
        self.assertEqual(len(result["trace"]), 3)
        # 三次调用全部成功
        self.assertTrue(all(not t["is_error"] for t in result["trace"]))
        # 事件回调按预期触发
        self.assertIn("session_start", events)
        self.assertIn("tool_call", events)
        self.assertIn("final_answer", events)
        # 最终答案是「有据可查」的：引用了真实的 CSV 统计值
        self.assertIn("83.8333", result["answer"])
        self.assertIn("12 条记录", result["answer"])

    def test_loop_reports_tool_failure_without_crashing(self) -> None:
        """工具失败时，循环应当把错误回灌给 LLM 而不是崩溃。"""
        plan = [("tool_read_pdf", {"path": str(ROOT / "data" / "not_here.pdf")})]
        scout = StudyScout(llm=ScriptedLLM(plan))  # type: ignore[arg-type]

        result = asyncio.run(scout.run("读一个不存在的文件。"))

        self.assertEqual(len(result["trace"]), 1)
        self.assertTrue(result["trace"][0]["is_error"])
        # 错误信息应当是可操作的，而不是一句 "Error executing tool"
        self.assertIn("文件不存在", result["trace"][0]["result_preview"])

    def test_path_guard_blocks_outside_access(self) -> None:
        """路径白名单应当拦住项目目录之外的读取。"""
        plan = [("tool_analyze_csv", {"path": "C:/Windows/System32/drivers/etc/hosts"})]
        scout = StudyScout(llm=ScriptedLLM(plan))  # type: ignore[arg-type]

        result = asyncio.run(scout.run("读一下系统文件。"))

        self.assertTrue(result["trace"][0]["is_error"])
        self.assertIn("安全策略拒绝", result["trace"][0]["result_preview"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
