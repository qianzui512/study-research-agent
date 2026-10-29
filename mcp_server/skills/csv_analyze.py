"""
Skill 2 —— 本地 CSV 解析与统计 (CSV Analyze)

用途：智能体读不懂原始表格，但能读懂「结构化的统计摘要」。
这个技能把一份 CSV 变成：列结构 + 类型推断 + 数值统计 + 分类 Top 值。
对应作业要求里的：parsing a CSV / querying a database。

只用标准库 csv + statistics，不引入 pandas，保证零重依赖、可离线跑。
"""

from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class ColumnProfile:
    name: str
    inferred_type: str          # "number" | "text" | "empty"
    non_empty: int
    missing: int
    # 数值列才有
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    median: float | None = None
    stdev: float | None = None
    # 文本列才有
    unique: int | None = None
    top_values: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CsvReport:
    path: str
    row_count: int
    column_count: int
    columns: list[dict[str, Any]]
    preview: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _try_float(value: str) -> float | None:
    try:
        return float(value.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _profile_column(name: str, values: list[str]) -> ColumnProfile:
    non_empty_values = [v for v in values if v is not None and str(v).strip() != ""]
    missing = len(values) - len(non_empty_values)

    if not non_empty_values:
        return ColumnProfile(name=name, inferred_type="empty", non_empty=0, missing=missing)

    numbers = [_try_float(v) for v in non_empty_values]
    is_numeric = all(n is not None for n in numbers)

    if is_numeric:
        nums = [n for n in numbers if n is not None]
        return ColumnProfile(
            name=name,
            inferred_type="number",
            non_empty=len(nums),
            missing=missing,
            min=round(min(nums), 4),
            max=round(max(nums), 4),
            mean=round(statistics.fmean(nums), 4),
            median=round(statistics.median(nums), 4),
            stdev=round(statistics.stdev(nums), 4) if len(nums) > 1 else 0.0,
        )

    counts: dict[str, int] = {}
    for v in non_empty_values:
        key = str(v).strip()
        counts[key] = counts.get(key, 0) + 1
    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:5]

    return ColumnProfile(
        name=name,
        inferred_type="text",
        non_empty=len(non_empty_values),
        missing=missing,
        unique=len(counts),
        top_values=[{"value": k, "count": v} for k, v in top],
    )


def analyze_csv(path: str, preview_rows: int = 3) -> dict[str, Any]:
    """解析 CSV 文件，返回结构与统计摘要。

    Args:
        path: CSV 文件路径（相对路径会基于当前工作目录解析）。
        preview_rows: 返回给模型看的样例行数。

    Returns:
        含 row_count / column_count / columns / preview 的字典。
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"找不到 CSV 文件: {p.resolve()}")
    if not p.is_file():
        raise ValueError(f"不是文件: {p.resolve()}")

    # utf-8-sig 能顺带吃掉 Excel 导出的 BOM
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        if not fieldnames:
            raise ValueError("CSV 没有表头，无法解析")
        rows = [dict(r) for r in reader]

    columns = [_profile_column(name, [r.get(name, "") for r in rows]).to_dict() for name in fieldnames]

    return CsvReport(
        path=str(p.resolve()),
        row_count=len(rows),
        column_count=len(fieldnames),
        columns=columns,
        preview=rows[:preview_rows],
    ).to_dict()
