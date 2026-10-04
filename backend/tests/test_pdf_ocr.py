import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw

from app.parsers.pdf_parser import PDFParser
from app.parsers.ocr import recognize_page
from app.parsers.quality import DocumentQualityError


def page(text, images=None):
    return SimpleNamespace(extract_text=lambda: text, images=images or [])


def opened(pages):
    pdf = MagicMock()
    pdf.__enter__.return_value.pages = pages
    return patch("app.parsers.pdf_parser.pdfplumber.open", return_value=pdf)


class PDFOCRTests(unittest.TestCase):
    def test_short_valid_text_with_decorative_image_stays_native(self):
        with (opened([page("谢 谢！", [1])]), patch("app.parsers.pdf_parser.recognize_page") as ocr):
            result = PDFParser().parse("source.pdf")
        ocr.assert_not_called()
        self.assertEqual(result.report["pages"][0]["method"], "native")

    def test_mixed_pages_only_ocr_bad_or_scanned_pages_with_provenance(self):
        with (opened([page("正常原文"), page(None), page("(cid:1)"*20), page("", [1])]),
              patch("app.parsers.pdf_parser.recognize_page", side_effect=[
                  {"text": "CREATE TABLE 学生表", "method": "ocr", "confidence": 90},
                  {"text": "扫描图像内容", "method": "ocr", "confidence": 85},
              ]) as ocr):
            parser = PDFParser()
            text = parser.parse("source.pdf")
        self.assertEqual([call.args[1] for call in ocr.call_args_list], [3, 4])
        self.assertEqual([p["method"] for p in text.report["pages"]], ["native", "blank", "ocr", "ocr"])
        self.assertEqual([c.page_number for c in parser.chunk(text)], [1, 3, 4])
        self.assertEqual(text.report["ocr_pages"], 2)

    def test_ocr_bad_result_rejected_and_report_attached(self):
        with (opened([page("(cid:1)"*20)]),
              patch("app.parsers.pdf_parser.recognize_page", return_value={"text": "(cid:2)"*30, "method": "ocr"})):
            with self.assertRaises(DocumentQualityError) as caught:
                PDFParser().parse("source.pdf")
        self.assertEqual(caught.exception.report["status"], "failed")
        self.assertEqual(caught.exception.report["pages"][0]["page_number"], 1)

    def test_page_limit_fails_before_next_ocr(self):
        with (opened([page("", [1]), page("", [1])]),
              patch("app.parsers.pdf_parser.settings.pdf_ocr_max_pages", 1),
              patch("app.parsers.pdf_parser.recognize_page", return_value={"text": "正常文字", "method": "ocr"}) as ocr):
            with self.assertRaisesRegex(DocumentQualityError, "上限"):
                PDFParser().parse("source.pdf")
        self.assertEqual(ocr.call_count, 1)

    def test_total_budget_fails_before_ocr(self):
        with (opened([page("", [1])]),
              patch("app.parsers.pdf_parser.time.monotonic", side_effect=[0, 0, 241, 242]),
              patch("app.parsers.pdf_parser.recognize_page") as ocr):
            with self.assertRaisesRegex(DocumentQualityError, "上限"):
                PDFParser().parse("source.pdf")
        ocr.assert_not_called()


class LocalOCRTests(unittest.TestCase):
    def commands(self, confidence=95, blank=False):
        def run(command, **kwargs):
            self.assertGreater(kwargs["timeout"], 0)
            self.assertEqual(kwargs["env"]["OMP_THREAD_LIMIT"], "1")
            if command[0] == "pdftoppm":
                image = Image.new("RGB", (200, 200), "white")
                if not blank:
                    ImageDraw.Draw(image).rectangle((30, 30, 100, 60), fill="black")
                image.save(command[-1] + ".png")
                return SimpleNamespace(stdout="")
            return SimpleNamespace(stdout="level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tconf\ttext\n"
                                   f"5\t1\t1\t1\t1\t1\t{confidence}\tSELECT\n"
                                   f"5\t1\t1\t1\t1\t2\t{confidence}\t学\n"
                                   f"5\t1\t1\t1\t1\t3\t{confidence}\t生\n")
        return patch("app.parsers.ocr.subprocess.run", side_effect=run)

    def recognize(self):
        return recognize_page("test.pdf", 1, timeout=30, languages="chi_sim+eng", min_confidence=50)

    def test_tsv_text_and_confidence(self):
        with self.commands():
            result = self.recognize()
        self.assertEqual(result["text"], "SELECT 学生")
        self.assertEqual(result["confidence"], 95)

    def test_low_confidence_is_not_success(self):
        with self.commands(confidence=20), self.assertRaisesRegex(DocumentQualityError, "置信度"):
            self.recognize()

    def test_blank_raster_skips_tesseract(self):
        with self.commands(blank=True) as run:
            self.assertEqual(self.recognize()["method"], "blank")
        self.assertEqual(run.call_count, 1)

    def test_timeout_and_missing_binary_are_actionable(self):
        for error in [subprocess.TimeoutExpired("pdftoppm", 30), FileNotFoundError()]:
            with patch("app.parsers.ocr.subprocess.run", side_effect=error), self.assertRaises(DocumentQualityError):
                self.recognize()
