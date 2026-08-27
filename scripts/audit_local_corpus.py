"""Audit local PDF candidates without modifying source files."""

from __future__ import annotations

import argparse
from pathlib import Path

import pdfplumber


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()

    print("file\tpages\ttext_chars")
    for path in sorted(args.root.glob("*.pdf")):
        try:
            with pdfplumber.open(path) as pdf:
                text_chars = sum(len(page.extract_text() or "") for page in pdf.pages)
                print(f"{path.name}\t{len(pdf.pages)}\t{text_chars}")
        except Exception as exc:
            print(f"{path.name}\tERROR\t{type(exc).__name__}")


if __name__ == "__main__":
    main()
