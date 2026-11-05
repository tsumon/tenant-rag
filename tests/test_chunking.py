import unittest

from tenantrag.chunking import chunk_text


class ChunkingTests(unittest.TestCase):
    def assert_lossless(self, text, **options):
        chunks = chunk_text(text, **options)
        self.assertEqual(chunks, chunk_text(text, **options))
        if not text:
            self.assertEqual(chunks, [])
            return chunks
        self.assertEqual(chunks[0]["start"], 0)
        self.assertEqual(chunks[-1]["end"], len(text))
        for index, chunk in enumerate(chunks):
            self.assertEqual(set(chunk), {"text", "start", "end"})
            self.assertEqual(chunk["text"], text[chunk["start"]:chunk["end"]])
            self.assertGreater(chunk["end"], chunk["start"])
            self.assertLessEqual(len(chunk["text"]), options.get("max_chars", 1000))
            if index:
                self.assertEqual(chunks[index - 1]["end"] - chunk["start"],
                                 options.get("overlap", 100))
                self.assertGreater(chunk["start"], chunks[index - 1]["start"])
        rebuilt = chunks[0]["text"] + "".join(
            chunk["text"][options.get("overlap", 100):] for chunk in chunks[1:])
        self.assertEqual(rebuilt, text)
        return chunks

    def test_empty_short_whitespace(self):
        for text in ("", "a", " \t\r\n", "  leading and trailing  "):
            self.assert_lossless(text)

    def test_unicode_exact_offsets(self):
        text = "中文说明🌏e\u0301\r\n第二段🙂\n\n" * 50
        for format in ("text", "markdown"):
            self.assert_lossless(text, max_chars=21, overlap=5, format=format)

    def test_limits_and_large_overlap(self):
        text = "one\n\ntwo\nthree\n\n" * 9
        for size in range(1, 26):
            for overlap in (0, size // 2, size - 1):
                with self.subTest(size=size, overlap=overlap):
                    self.assert_lossless(text, max_chars=size, overlap=overlap)

    def test_paragraph_then_newline_preference(self):
        chunks = self.assert_lossless("a" * 11 + "\n\n" + "b" * 19,
                                     max_chars=20, overlap=3)
        self.assertEqual(chunks[0]["end"], 13)
        chunks = self.assert_lossless("a" * 12 + "\n" + "b" * 19,
                                     max_chars=20, overlap=3)
        self.assertEqual(chunks[0]["end"], 13)

    def test_markdown_heading_preference(self):
        text = "Intro text.\n# Heading\nbody content continues past the limit"
        chunks = self.assert_lossless(text, max_chars=22, overlap=0, format="markdown")
        self.assertEqual(chunks[0]["text"], "Intro text.\n")
        self.assertTrue(chunks[1]["text"].startswith("# Heading"))
        plain = chunk_text(text, max_chars=22, overlap=0)
        self.assertNotEqual(chunks[0]["end"], plain[0]["end"])

    def test_fenced_code_heading_not_preferred(self):
        text = "```python\n# Comment\nprint('hello')\n```\n# Real heading\nmore body\n"
        chunks = self.assert_lossless(text, max_chars=22, overlap=0, format="markdown")
        self.assertEqual(chunks[0]["text"], "```python\n# Comment\n")
        # Oversized fenced code is allowed to split, without changing its text.
        self.assert_lossless("~~~\n" + "x" * 40 + "\n~~~\n", max_chars=12,
                             overlap=2, format="markdown")

    def test_invalid_options(self):
        for value in (None, 1, [], {}):
            with self.assertRaises(ValueError):
                chunk_text(value)
        for size in (False, True, 0, -1, 1.5, "20", None):
            with self.assertRaises(ValueError):
                chunk_text("text", max_chars=size, overlap=0)
        for overlap in (False, True, -1, 20, 21, 1.5, "2", None):
            with self.assertRaises(ValueError):
                chunk_text("text", max_chars=20, overlap=overlap)
        for format in (None, "md", "html", [], {}):
            with self.assertRaises(ValueError):
                chunk_text("text", format=format)

    def test_input_and_output_resource_limits(self):
        with self.assertRaisesRegex(ValueError, "character chunking limit"):
            chunk_text("x" * 1_000_001)
        with self.assertRaisesRegex(ValueError, "ingestion limits"):
            chunk_text("x" * 10_001, max_chars=1, overlap=0)
        # A one-character stride must be bounded by total materialized output,
        # not merely by input length or the number of returned chunks.
        with self.assertRaisesRegex(ValueError, "ingestion limits"):
            chunk_text("x" * 20_000, max_chars=10_000, overlap=9_999)
        self.assertEqual(len(chunk_text("x" * 10_000, max_chars=1, overlap=0)), 10_000)
        self.assertEqual(chunk_text("中" * 1_000_000, max_chars=1_000_000, overlap=0)[0]["end"],
                         1_000_000)


if __name__ == "__main__":
    unittest.main()
