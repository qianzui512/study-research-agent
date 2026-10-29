"""
MCP 协议字段兼容层。

mcp 1.x 的 Pydantic 模型用 camelCase 字段名（inputSchema / isError），
mcp 2.x 改成了 snake_case（input_schema / is_error）。
这里统一抹平差异，让上层代码不用关心装的是哪个版本。
"""

from __future__ import annotations

from typing import Any


def get_input_schema(tool: Any) -> dict[str, Any]:
    """取工具的 JSON Schema 参数定义。"""
    for attr in ("inputSchema", "input_schema"):
        schema = getattr(tool, attr, None)
        if schema:
            return schema
    return {"type": "object", "properties": {}}


def get_is_error(result: Any) -> bool:
    """取工具调用结果是否为错误。"""
    for attr in ("isError", "is_error"):
        value = getattr(result, attr, None)
        if value is not None:
            return bool(value)
    return False


def get_structured_content(result: Any) -> Any:
    """取结构化输出（若服务端返回了的话）。"""
    for attr in ("structuredContent", "structured_content"):
        value = getattr(result, attr, None)
        if value is not None:
            return value
    return None
