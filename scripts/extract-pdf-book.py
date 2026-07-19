#!/usr/bin/env python3
"""Extract a PDF book to markdown with inline co-located JPEG images (PyMuPDF)."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF


@dataclass
class LayoutBlock:
    kind: str  # "text" | "image"
    y0: float
    x0: float
    content: str


@dataclass
class ExtractStats:
    pages_processed: int = 0
    images_saved: int = 0
    images_skipped_small: int = 0
    images_deduped: int = 0
    bbox_fallback_pages: set[int] = field(default_factory=set)
    unique_hashes: set[str] = field(default_factory=set)


def parse_pages(spec: str | None, page_count: int) -> list[int]:
    if spec is None:
        return list(range(page_count))

    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))

    selected = sorted(p for p in pages if 0 <= p < page_count)
    invalid = sorted(p for p in pages if p < 0 or p >= page_count)
    if invalid:
        print(f"WARNING: ignoring out-of-range pages: {invalid}", file=sys.stderr)
    return selected


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def extract_text_blocks(page: fitz.Page) -> list[LayoutBlock]:
    blocks: list[LayoutBlock] = []
    data = page.get_text("dict")

    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue

        bbox = block.get("bbox", (0.0, 0.0, 0.0, 0.0))
        parts: list[str] = []
        for line in block.get("lines", []):
            line_parts: list[str] = []
            for span in line.get("spans", []):
                text = span.get("text", "")
                if text:
                    line_parts.append(text)
            if line_parts:
                parts.append("".join(line_parts))

        text = "\n".join(parts).strip()
        if text:
            blocks.append(LayoutBlock("text", float(bbox[1]), float(bbox[0]), text))

    return blocks


def save_pixmap_jpeg(pix: fitz.Pixmap, out_path: Path, quality: int = 85) -> None:
    if pix.n - pix.alpha > 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    elif pix.alpha:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    pix.save(str(out_path), jpg_quality=quality)


def extract_image_blocks(
    page: fitz.Page,
    doc: fitz.Document,
    page_index: int,
    output_dir: Path,
    min_width: int,
    min_height: int,
    stats: ExtractStats,
) -> list[LayoutBlock]:
    blocks: list[LayoutBlock] = []
    hash_to_filename: dict[str, str] = {}
    picture_index = 0

    for img_info in page.get_images(full=True):
        xref = int(img_info[0])
        try:
            base_image = doc.extract_image(xref)
        except Exception as exc:
            print(
                f"WARNING: page {page_index}: failed to extract xref {xref}: {exc}",
                file=sys.stderr,
            )
            continue

        width = int(base_image["width"])
        height = int(base_image["height"])
        if width < min_width or height < min_height:
            stats.images_skipped_small += 1
            continue

        image_bytes = base_image["image"]
        digest = hashlib.sha256(image_bytes).hexdigest()

        if digest in hash_to_filename:
            filename = hash_to_filename[digest]
            stats.images_deduped += 1
            continue

        filename = f"_page_{page_index}_Picture_{picture_index}.jpeg"
        out_path = output_dir / filename
        try:
            pix = fitz.Pixmap(doc, xref)
            save_pixmap_jpeg(pix, out_path)
        except Exception as exc:
            print(
                f"WARNING: page {page_index}: failed to save xref {xref}: {exc}",
                file=sys.stderr,
            )
            continue

        hash_to_filename[digest] = filename
        stats.unique_hashes.add(digest)
        stats.images_saved += 1
        picture_index += 1

        y0 = 1_000_000.0
        x0 = 0.0
        used_bbox = False
        try:
            rects = page.get_image_rects(xref)
            if rects:
                rect = rects[0]
                y0 = float(rect.y0)
                x0 = float(rect.x0)
                used_bbox = True
        except Exception:
            used_bbox = False

        if not used_bbox:
            stats.bbox_fallback_pages.add(page_index)

        blocks.append(LayoutBlock("image", y0, x0, f"![]({filename})"))

    return blocks


def render_page_markdown(blocks: list[LayoutBlock]) -> str:
    ordered = sorted(blocks, key=lambda block: (block.y0, block.x0))
    lines: list[str] = []
    for block in ordered:
        lines.append(block.content)
        lines.append("")
    return "\n".join(lines).strip()


def extract_book(
    pdf_path: Path,
    output_dir: Path,
    book_name: str,
    pages: list[int],
    min_width: int = 80,
    min_height: int = 80,
) -> tuple[Path, ExtractStats, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = ExtractStats()

    doc = fitz.open(pdf_path)
    md_parts: list[str] = []

    try:
        for page_index in pages:
            page = doc[page_index]
            stats.pages_processed += 1

            text_blocks = extract_text_blocks(page)
            image_blocks = extract_image_blocks(
                page,
                doc,
                page_index,
                output_dir,
                min_width,
                min_height,
                stats,
            )

            page_md = render_page_markdown(text_blocks + image_blocks)
            if page_md:
                md_parts.append(page_md)
    finally:
        doc.close()

    md_path = output_dir / f"{book_name}.md"
    md_content = "\n\n".join(part for part in md_parts if part).strip()
    if md_content:
        md_content += "\n"
    md_path.write_text(md_content, encoding="utf-8")
    return md_path, stats, md_content


def pdftotext_page(pdf_path: Path, page_index: int) -> str:
    page_num = page_index + 1
    try:
        result = subprocess.run(
            [
                "pdftotext",
                "-layout",
                "-f",
                str(page_num),
                "-l",
                str(page_num),
                str(pdf_path),
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return ""

    if result.returncode != 0:
        print(
            f"WARNING: pdftotext failed for page {page_index}: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return ""
    return result.stdout


def pymupdf_page_text(pdf_path: Path, page_index: int) -> str:
    doc = fitz.open(pdf_path)
    try:
        return doc[page_index].get_text("text")
    finally:
        doc.close()


def run_metrics(
    pdf_path: Path,
    md_content: str,
    pages: list[int],
    stats: ExtractStats,
) -> dict[str, object]:
    pymupdf_chars = 0
    pdftotext_chars = 0
    page_ratios: dict[int, float] = {}

    for page_index in pages:
        pymupdf_text = pymupdf_page_text(pdf_path, page_index)
        pdftotext_text = pdftotext_page(pdf_path, page_index)
        p_chars = len(normalize_text(pymupdf_text))
        d_chars = len(normalize_text(pdftotext_text))
        pymupdf_chars += p_chars
        pdftotext_chars += d_chars
        if d_chars > 0:
            page_ratios[page_index] = p_chars / d_chars

    overall_ratio = pymupdf_chars / pdftotext_chars if pdftotext_chars else 1.0
    bbox_fallback_pct = (
        100.0 * len(stats.bbox_fallback_pages) / len(pages) if pages else 0.0
    )

    spot_words = ["Gangrel", "Masquerade", "Protean"]
    spot_hits = {word: word in md_content for word in spot_words}

    page1_inline = False
    if 1 in pages:
        # Page 1 may have no images above min size; also accept interleaved layout
        # on any selected page that contains both images and body text.
        page1_inline = bool(
            re.search(r"!\[\]\(_page_1_[^)]+\)", md_content)
            and re.search(r"!\[\]\(_page_1_[^)]+\)[\s\S]+[^\n!]", md_content)
        )
    inline_confirmed = bool(
        re.search(
            r"!\[\]\(_page_\d+_[^)]+\)[\s\S]{10,}[^\n!]",
            md_content,
        )
        and re.search(
            r"[^\n!][\s\S]{10,}!\[\]\(_page_\d+_[^)]+\)",
            md_content,
        )
    )

    return {
        "pymupdf_chars": pymupdf_chars,
        "pdftotext_chars": pdftotext_chars,
        "overall_ratio": overall_ratio,
        "page_ratios": page_ratios,
        "bbox_fallback_pages": sorted(stats.bbox_fallback_pages),
        "bbox_fallback_pct": bbox_fallback_pct,
        "spot_hits": spot_hits,
        "page1_inline": page1_inline,
        "inline_confirmed": inline_confirmed,
    }


def print_stats(stats: ExtractStats, metrics: dict[str, object] | None = None) -> None:
    print("--- extract-pdf-book summary ---")
    print(f"Pages processed: {stats.pages_processed}")
    print(f"Images saved: {stats.images_saved}")
    print(f"Images skipped (< min size): {stats.images_skipped_small}")
    print(f"Images deduped (same SHA256): {stats.images_deduped}")
    print(f"Unique image hashes: {len(stats.unique_hashes)}")
    print(f"BBox fallback pages: {sorted(stats.bbox_fallback_pages)}")

    if metrics:
        print("--- dry-run metrics ---")
        print(
            "PyMuPDF chars vs pdftotext: "
            f"{metrics['pymupdf_chars']} / {metrics['pdftotext_chars']} "
            f"({metrics['overall_ratio']:.1%})"
        )
        print(f"Per-page ratios: {metrics['page_ratios']}")
        print(f"BBox fallback: {metrics['bbox_fallback_pct']:.1f}% of test pages")
        print(f"Spot-check words: {metrics['spot_hits']}")
        print(f"Page 1 inline layout: {metrics['page1_inline']}")
        print(f"Inline layout (any page): {metrics['inline_confirmed']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract PDF to markdown with inline images (_page_N_Picture_M.jpeg)."
    )
    parser.add_argument("--pdf", required=True, type=Path, help="Path to source PDF")
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output directory for markdown and images",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Book basename (creates <name>.md)",
    )
    parser.add_argument(
        "--pages",
        help="Optional page selection, e.g. '0-2' or '0,1,2,5,35'",
    )
    parser.add_argument("--min-width", type=int, default=80)
    parser.add_argument("--min-height", type=int, default=80)
    parser.add_argument(
        "--metrics",
        action="store_true",
        help="Compare extracted text with pdftotext on selected pages",
    )
    args = parser.parse_args()

    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    doc = fitz.open(pdf_path)
    page_count = doc.page_count
    doc.close()

    pages = parse_pages(args.pages, page_count)
    if not pages:
        print("ERROR: no pages selected", file=sys.stderr)
        return 1

    md_path, stats, md_content = extract_book(
        pdf_path=pdf_path,
        output_dir=args.output.resolve(),
        book_name=args.name,
        pages=pages,
        min_width=args.min_width,
        min_height=args.min_height,
    )

    metrics = None
    if args.metrics:
        metrics = run_metrics(pdf_path, md_content, pages, stats)

    print(f"Wrote markdown: {md_path}")
    print(f"PyMuPDF version: {fitz.VersionBind}")
    print_stats(stats, metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
