"""
Skill 1 —— 网页正文抓取 (Web Fetch)

用途：给智能体一个 URL，它能把网页的「正文」抠出来，供后续总结/引用。
这属于作业要求里的第一类外部工具：fetching a web page。

设计要点
- 用 httpx 发请求（带超时 + 重定向 + 浏览器 UA，避免被基础反爬拦截）
- 用 BeautifulSoup 剥掉 script/style/nav/footer 等噪音标签
- 截断到 max_chars，防止把 LLM 的上下文塞爆
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import httpx
from bs4 import BeautifulSoup

# 伪装成常见浏览器，减少 403
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 这些标签里的内容基本是噪音，直接扔掉
_NOISE_TAGS = ["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg"]


@dataclass
class FetchResult:
    url: str
    status_code: int
    title: str
    text: str
    truncated: bool
    char_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fetch_webpage(url: str, max_chars: int = 4000, timeout: float = 15.0) -> dict[str, Any]:
    """抓取网页并返回清洗后的正文。

    Args:
        url: 目标网页地址，必须是 http/https。
        max_chars: 正文最大返回字符数，超出会截断。
        timeout: 请求超时秒数。

    Returns:
        含 url / status_code / title / text / truncated / char_count 的字典。
    """
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"只支持 http/https 链接，收到: {url!r}")

    with httpx.Client(headers=_HEADERS, follow_redirects=True, timeout=timeout) as client:
        resp = client.get(url)
        resp.raise_for_status()
        html = resp.text
        status_code = resp.status_code
        final_url = str(resp.url)

    soup = BeautifulSoup(html, "html.parser")

    title = (soup.title.string or "").strip() if soup.title else ""

    for tag in soup(_NOISE_TAGS):
        tag.decompose()

    # 优先取 <article> / <main>，否则退回 body
    container = soup.find("article") or soup.find("main") or soup.body or soup
    raw_text = container.get_text(separator="\n", strip=True)

    # 压掉连续空行
    lines = [ln.strip() for ln in raw_text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars] + f"\n\n...[已截断，原文共 {len(raw_text)} 字符]"

    return FetchResult(
        url=final_url,
        status_code=status_code,
        title=title,
        text=text,
        truncated=truncated,
        char_count=len(text),
    ).to_dict()
