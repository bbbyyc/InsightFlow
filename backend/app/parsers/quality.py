"""Conservative checks for known text extraction failures, not semantic quality."""
import re


class DocumentQualityError(ValueError):
    """A permanent input failure: retrying identical bytes cannot fix extraction."""


_CID = re.compile(r"\(cid:\d+\)", re.IGNORECASE)


def validate_extracted_text(text: str, location: str = "文档") -> None:
    if not text.strip():
        raise DocumentQualityError(
            f"{location}未提取到可检索文本；可能为扫描件，请先进行 OCR 或上传可复制文字的版本。"
        )
    # Count one bad glyph per CID marker, rather than its expanded ASCII length.
    normalized = _CID.sub("\ufffd", text)
    visible = [char for char in normalized if not char.isspace()]
    invalid = normalized.count("\ufffd")
    if invalid >= 3 and invalid / max(1, len(visible)) >= 0.05:
        raise DocumentQualityError(
            f"{location}文本解析质量不合格：发现 {invalid} 个无法映射的字符"
            f"（占非空白字符 {invalid / len(visible):.1%}）。"
            "请重新导出包含正确字体映射的 PDF，或先进行 OCR 后重新上传。"
        )
