#!/usr/bin/env python3
"""Validate that every ![](path) in a book markdown file exists on disk."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

IMAGE_LINK_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def find_broken_image_links(book_dir: Path, md_path: Path) -> list[tuple[str, Path]]:
    """Return list of (link, expected_path) for missing image files."""
    if not md_path.is_file():
        raise FileNotFoundError(f"Markdown file not found: {md_path}")

    text = md_path.read_text(encoding="utf-8")
    broken: list[tuple[str, Path]] = []

    for match in IMAGE_LINK_RE.finditer(text):
        link = match.group(1).strip()
        if not link or link.startswith(("http://", "https://", "data:")):
            continue

        # Paths in book markdown are relative to the md file directory.
        target = (md_path.parent / link).resolve()
        if not target.is_file():
            broken.append((link, target))

    return broken


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that all markdown image links point to existing files."
    )
    parser.add_argument(
        "--book-dir",
        required=True,
        type=Path,
        help="Book directory (used as base when --md is relative)",
    )
    parser.add_argument(
        "--md",
        required=True,
        help="Markdown filename or path relative to --book-dir",
    )
    args = parser.parse_args()

    book_dir = args.book_dir.resolve()
    md_path = Path(args.md)
    if not md_path.is_absolute():
        md_path = book_dir / md_path

    try:
        broken = find_broken_image_links(book_dir, md_path)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    links = IMAGE_LINK_RE.findall(md_path.read_text(encoding="utf-8"))
    local_links = [
        link.strip()
        for link in links
        if link.strip() and not link.strip().startswith(("http://", "https://", "data:"))
    ]

    print(f"Book dir: {book_dir}")
    print(f"Markdown: {md_path}")
    print(f"Local image links checked: {len(local_links)}")

    if broken:
        print(f"Broken links: {len(broken)}", file=sys.stderr)
        for link, expected in broken:
            print(f"  MISSING: {link} -> {expected}", file=sys.stderr)
        return 1

    print("All image links OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
