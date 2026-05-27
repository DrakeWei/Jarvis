#!/usr/bin/env python3

from pypdf import PdfReader


def read_pdf(path: str) -> int:
    reader = PdfReader(path)
    return len(reader.pages)


if __name__ == "__main__":
    raise SystemExit("pdf tool placeholder")
