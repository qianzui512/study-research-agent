"""
生成演示用的样例 PDF（仅用于本地测试 read_pdf 技能）。

优先尝试用系统中文字体生成中文论文，若找不到可用字体则回退为英文。
生成的 PDF 会被提交进仓库，所以别人 clone 后无需再跑本脚本。

用法：
    python scripts/make_sample_pdf.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "sample_paper.pdf"

# Windows / macOS 上常见的中文字体候选
CJK_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]

CN_TITLE = "基于检索增强生成的学习资料问答系统研究"
CN_ABSTRACT = (
    "摘要：大语言模型在开放域问答任务中表现出色，但在专业领域的细粒度知识上仍存在幻觉问题。"
    "本文提出一种面向学习场景的检索增强生成（RAG）框架，通过将本地讲义、论文与结构化数据"
    "统一索引，使模型在作答前必须先检索证据。实验表明，该框架在课程问答数据集上把事实准确率"
    "从 61.2% 提升到 87.5%，同时把幻觉率从 23.4% 降低到 6.1%。"
)
CN_SECTIONS = [
    (
        "1 引言",
        "学生在复习时往往需要同时查阅多份讲义、论文与成绩数据。传统的关键词检索无法理解语义，"
        "而直接使用大模型又容易产生与教材不符的表述。检索增强生成通过在生成前引入外部证据，"
        "为解决这一矛盾提供了可行路径。",
    ),
    (
        "2 方法",
        "本框架包含三个组件：一是文档解析器，负责从 PDF 与 CSV 中抽取结构化文本；"
        "二是检索器，采用稠密向量加稀疏关键词的混合召回；三是生成器，在系统提示词中约束"
        "模型只能基于检索到的证据作答，并要求标注来源页码。",
    ),
    (
        "3 实验与结论",
        "我们在 1200 条课程问答上进行了评测。结果显示，引入检索后事实准确率显著提升，"
        "尤其在需要跨文档推理的问题上收益最大。局限性在于，当检索器召回失败时，"
        "生成质量会明显下降，未来工作将聚焦于召回鲁棒性。",
    ),
]

EN_TITLE = "Retrieval-Augmented Generation for Study Material Question Answering"
EN_ABSTRACT = (
    "Abstract: Large language models excel at open-domain question answering but remain prone to "
    "hallucination on fine-grained domain knowledge. We present a retrieval-augmented generation "
    "framework for study scenarios that indexes local lecture notes, papers, and structured data so "
    "that the model must retrieve evidence before answering. On a course QA dataset, factual accuracy "
    "improves from 61.2% to 87.5% while the hallucination rate drops from 23.4% to 6.1%."
)
EN_SECTIONS = [
    (
        "1 Introduction",
        "Students reviewing for exams must consult lecture notes, papers, and grade data at the same "
        "time. Keyword search fails to capture semantics, while using a raw LLM risks statements that "
        "contradict the textbook. Retrieval-augmented generation offers a practical path by injecting "
        "external evidence before generation.",
    ),
    (
        "2 Method",
        "Our framework has three components: a document parser that extracts structured text from PDFs "
        "and CSVs, a hybrid retriever combining dense vectors with sparse keyword matching, and a "
        "generator whose system prompt restricts it to retrieved evidence and requires source citation.",
    ),
    (
        "3 Experiments and Conclusion",
        "We evaluate on 1200 course questions. Retrieval substantially improves factual accuracy, with "
        "the largest gains on questions requiring cross-document reasoning. A limitation is that "
        "generation quality degrades sharply when retrieval fails; future work targets recall robustness.",
    ),
]


def build_pdf() -> tuple[FPDF, bool]:
    """返回 (pdf, 是否使用中文字体)。"""
    for path in CJK_FONT_CANDIDATES:
        if Path(path).exists():
            try:
                pdf = FPDF()
                pdf.add_font("cjk", "", path)
                return pdf, True
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] 字体 {path} 加载失败: {exc}", file=sys.stderr)
    return FPDF(), False


def main() -> None:
    pdf, use_cjk = build_pdf()
    family = "cjk" if use_cjk else "helvetica"
    title = CN_TITLE if use_cjk else EN_TITLE
    abstract = CN_ABSTRACT if use_cjk else EN_ABSTRACT
    sections = CN_SECTIONS if use_cjk else EN_SECTIONS

    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    pdf.set_font(family, size=16)
    pdf.multi_cell(0, 10, title)
    pdf.ln(4)

    pdf.set_font(family, size=11)
    pdf.multi_cell(0, 7, abstract)
    pdf.ln(4)

    # 让内容自然分页，便于测试按页提取
    for heading, body in sections:
        pdf.set_font(family, size=13)
        pdf.multi_cell(0, 8, heading)
        pdf.ln(1)
        pdf.set_font(family, size=11)
        pdf.multi_cell(0, 7, body)
        pdf.ln(3)
        pdf.add_page()  # 每节单独一页，方便演示按页读取

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(OUT))
    print(f"[ok] 已生成 {OUT}  (字体: {'中文' if use_cjk else '英文/内置'})")


if __name__ == "__main__":
    main()
