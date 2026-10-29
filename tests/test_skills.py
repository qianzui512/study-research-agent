"""
技能单元测试 —— 不依赖网络、不依赖 API key，纯本地可跑。

运行：
    python tests/test_skills.py
    # 或
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.skills import analyze_csv, fetch_webpage, read_pdf  # noqa: E402

CSV_PATH = ROOT / "data" / "sample_grades.csv"
PDF_PATH = ROOT / "data" / "sample_paper.pdf"


class TestCsvSkill(unittest.TestCase):
    def test_basic_shape(self) -> None:
        report = analyze_csv(str(CSV_PATH))
        self.assertEqual(report["row_count"], 12)
        self.assertEqual(report["column_count"], 8)
        self.assertEqual(len(report["columns"]), 8)

    def test_numeric_column_stats(self) -> None:
        report = analyze_csv(str(CSV_PATH))
        cols = {c["name"]: c for c in report["columns"]}
        math = cols["grade_math"]
        self.assertEqual(math["inferred_type"], "number")
        # 手算校验：68,72,76,79,82,85,87,88,90,91,93,95 → min 68, max 95
        self.assertEqual(math["min"], 68.0)
        self.assertEqual(math["max"], 95.0)
        self.assertAlmostEqual(math["mean"], 83.8333, places=3)

    def test_text_column_top_values(self) -> None:
        report = analyze_csv(str(CSV_PATH))
        cols = {c["name"]: c for c in report["columns"]}
        major = cols["major"]
        self.assertEqual(major["inferred_type"], "text")
        # 4 个专业，每个 3 人
        self.assertEqual(major["unique"], 4)
        self.assertEqual(major["top_values"][0]["count"], 3)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            analyze_csv(str(ROOT / "data" / "nope.csv"))


class TestPdfSkill(unittest.TestCase):
    def test_extract_first_page(self) -> None:
        result = read_pdf(str(PDF_PATH), start_page=1, end_page=1)
        self.assertGreaterEqual(result["page_count"], 3)
        self.assertEqual(result["pages_returned"], 1)
        text = result["pages"][0]["text"]
        self.assertGreater(len(text), 50)
        # 标题或摘要里的关键词应当出现
        self.assertTrue("检索增强" in text or "Retrieval" in text or "摘要" in text or "Abstract" in text)

    def test_page_range_and_truncation(self) -> None:
        result = read_pdf(str(PDF_PATH), start_page=2, end_page=3, max_chars=200)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(result["total_chars"], 200)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            read_pdf(str(ROOT / "data" / "nope.pdf"))


class TestWebSkill(unittest.TestCase):
    def test_rejects_non_http_scheme(self) -> None:
        with self.assertRaises(ValueError):
            fetch_webpage("ftp://example.com/file")

    def test_rejects_bare_path(self) -> None:
        with self.assertRaises(ValueError):
            fetch_webpage("/etc/passwd")

    def test_real_fetch_optional(self) -> None:
        """联网时才跑；断网则跳过，不视为失败。"""
        try:
            result = fetch_webpage("https://example.com", max_chars=500)
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"网络不可用，跳过真实抓取: {exc}")
        self.assertEqual(result["status_code"], 200)
        self.assertIn("Example Domain", result["title"])
        self.assertLessEqual(result["char_count"], 500 + 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
