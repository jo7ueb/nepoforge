"""JSONC 読み込み (json5 ラッパ) のテスト (#47)。"""

from __future__ import annotations

import unittest

from nepoforge.jsonc import loads
from nepoforge.store import NepoforgeError


class TestJsonc(unittest.TestCase):
    def test_parses_comments_and_trailing_commas(self) -> None:
        text = """
        {
          // 行コメント
          "a": 1, /* ブロック
                     コメント */
          "b": [1, 2,],
          "c": {"x": "http://example.com",},
        }
        """
        data = loads(text)
        self.assertEqual(data, {"a": 1, "b": [1, 2], "c": {"x": "http://example.com"}})

    def test_comment_markers_inside_strings_survive(self) -> None:
        text = '{"url": "https://x.test/a//b", "s": "/* not a comment */"}'
        data = loads(text)
        self.assertEqual(data["url"], "https://x.test/a//b")
        self.assertEqual(data["s"], "/* not a comment */")

    def test_broken_jsonc_raises(self) -> None:
        with self.assertRaises(NepoforgeError):
            loads("{not json")


if __name__ == "__main__":
    unittest.main()
