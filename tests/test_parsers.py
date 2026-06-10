import io
import unittest
from pathlib import Path

from pypdf import PdfWriter

from app.parsers import ocr_available, parse_document_with_metadata


ROOT = Path(__file__).resolve().parents[1]


class ParserTests(unittest.TestCase):
    def test_ocr_availability_returns_boolean(self):
        self.assertIsInstance(ocr_available(), bool)

    def test_scanned_resume_uses_ocr(self):
        path = ROOT / "examples" / "小黄_深度实战版_.pdf"
        if not path.exists():
            self.skipTest("OCR example PDF is not included in this checkout")
        if not ocr_available():
            self.skipTest("OCR command line dependencies are not installed")

        result = parse_document_with_metadata(path.name, path.read_bytes())

        self.assertTrue(result.used_ocr)
        self.assertGreater(len(result.text), 1000)
        self.assertIn("跨境电商", result.text)

    def test_blank_pdf_does_not_abort_analysis(self):
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        stream = io.BytesIO()
        writer.write(stream)

        result = parse_document_with_metadata("空白简历.pdf", stream.getvalue())

        self.assertIn("空白简历", result.text)
        self.assertIn("继续分析", result.warning)


if __name__ == "__main__":
    unittest.main()
