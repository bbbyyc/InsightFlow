"""Bounded, local page rendering and OCR. No document data leaves this machine."""
import csv
import io
import os
import re
from pathlib import Path
import subprocess
import tempfile
import time

from PIL import Image

from app.parsers.quality import DocumentQualityError, validate_extracted_text


def recognize_page(file_path: str, page_number: int, *, timeout: float,
                   languages: str, min_confidence: float) -> dict:
    started = time.monotonic()

    def run(command):
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise DocumentQualityError(f"PDF 第 {page_number} 页 OCR 超时（上限 {timeout:.0f} 秒）")
        try:
            return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=remaining, check=True,
                                  env={**os.environ, "OMP_THREAD_LIMIT": "1"}).stdout
        except FileNotFoundError as exc:
            raise DocumentQualityError("OCR 依赖不可用，请安装 Poppler、Tesseract 和中英文语言包后重试。") from exc
        except subprocess.TimeoutExpired as exc:
            raise DocumentQualityError(f"PDF 第 {page_number} 页 OCR 超时（上限 {timeout:.0f} 秒）") from exc
        except subprocess.CalledProcessError as exc:
            raise DocumentQualityError(f"PDF 第 {page_number} 页 OCR 执行失败：{exc.stderr[-300:]}") from exc

    with tempfile.TemporaryDirectory(prefix="insightflow-ocr-") as directory:
        prefix = str(Path(directory) / "page")
        # Bound rendered pixels to 3000x3000 regardless of PDF physical dimensions.
        run(["pdftoppm", "-f", str(page_number), "-l", str(page_number), "-r", "200",
             "-scale-to", "3000", "-singlefile", "-png", str(Path(file_path).resolve()), prefix])
        with Image.open(prefix + ".png") as image:
            histogram = image.convert("L").histogram()
            ink_fraction = sum(histogram[:220]) / (image.width * image.height)
        if ink_fraction < 0.0001:
            return {"text": "", "method": "blank", "confidence": None,
                    "elapsed_ms": round((time.monotonic()-started)*1000)}
        tsv = run(["tesseract", prefix + ".png", "stdout", "-l", languages,
                   "--psm", "3", "tsv"])
    lines = {}
    scores = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        word = row["text"].strip()
        confidence = float(row["conf"])
        key = (row["block_num"], row["par_num"], row["line_num"])
        lines.setdefault(key, []).append(word)
        scores.append((max(0, confidence), len(word)))
    text = "\n".join(" ".join(words) for words in lines.values())
    # Tesseract emits Chinese characters as separate words. Preserve English/SQL
    # spacing but remove artificial gaps inside Chinese phrases for retrieval.
    text = re.sub(r"(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])", "", text)
    validate_extracted_text(text, f"PDF 第 {page_number} 页 OCR 结果")
    confidence = sum(score * weight for score, weight in scores) / max(1, sum(w for _, w in scores))
    if sum(c.isalnum() for c in text) < 3 or confidence < min_confidence:
        raise DocumentQualityError(
            f"PDF 第 {page_number} 页 OCR 结果质量不足（置信度 {confidence:.1f}，阈值 {min_confidence:g}）；请提供更清晰的文件。")
    return {"text": text, "method": "ocr", "confidence": round(confidence, 2),
            "elapsed_ms": round((time.monotonic()-started)*1000)}
