"""
Skill 3 —— 本地 PDF 文本提取 (PDF Read)

用途：把学生本地下载的论文 / 讲义 PDF 变成纯文本，交给智能体总结。
对应作业要求里点名的场景：summarizing local PDF readings。

用 pypdf 逐页抽取文本，并按页标注来源，方便模型引用页码。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from pypdf import PdfReader


@dataclass
class PageText:
    page: int
    chars: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PdfResult:
    path: str
    page_count: int
    pages_returned: int
    truncated: bool
    total_chars: int
    pages: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_pdf(path: str, start_page: int = 1, end_page: int | None = None,
             max_chars: int = 8000) -> dict[str, Any]:
    """提取 PDF 指定页码区间的文本。

    Args:
        path: PDF 文件路径。
        start_page: 起始页（从 1 开始，含）。
        end_page: 结束页（含）；None 表示到最后一页。
        max_chars: 总返回字符上限，超出即截断。

    Returns:
        含 page_count / pages / truncated 的字典。
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"找不到 PDF 文件: {p.resolve()}")

    reader = PdfReader(str(p))
    total_pages = len(reader.pages)

    start = max(1, start_page)
    end = total_pages if end_page is None else min(end_page, total_pages)

    pages: list[PageText] = []
    collected = 0
    truncated = False

    for idx in range(start - 1, end):
        page_text = (reader.pages[idx].extract_text() or "").strip()
        if collected + len(page_text) > max_chars:
            remain = max_chars - collected
            if remain > 0:
                pages.append(PageText(page=idx + 1, chars=remain, text=page_text[:remain]))
            truncated = True
            break
        pages.append(PageText(page=idx + 1, chars=len(page_text), text=page_text))
        collected += len(page_text)

    return PdfResult(
        path=str(p.resolve()),
        page_count=total_pages,
        pages_returned=len(pages),
        truncated=truncated,
        total_chars=sum(pg.chars for pg in pages),
        pages=[pg.to_dict() for pg in pages],
    ).to_dict()
