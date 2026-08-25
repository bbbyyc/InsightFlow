from app.parsers.base import BaseParser
from app.parsers.pdf_parser import PDFParser
from app.parsers.markdown_parser import MarkdownParser


PARSER_MAP: dict[str, BaseParser] = {
    "pdf": PDFParser(),
    "md": MarkdownParser(),
    "markdown": MarkdownParser(),
    "txt": MarkdownParser(),
}


def get_parser(file_type: str) -> BaseParser:
    parser = PARSER_MAP.get(file_type.lower())
    if not parser:
        raise ValueError(f"Unsupported file type: {file_type}")
    return parser
