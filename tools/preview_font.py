#!/usr/bin/env python3
"""Render text with an X-Ray font the way the engine does.

This is a stand-in for launching S.T.A.L.K.E.R.  It follows the
``[symbol_coords]`` path in ``CGameFont::Initialize`` and
``dxFontRender::OnRender``:

* the glyph strip is exactly ``height`` pixels tall (``y2`` is ignored),
* the quad width is ``x2 - x``,
* the cursor advances by that width.

If the atlas has variable-height cells, this preview will show the same
clipping and bleeding the game would.

Example::

    python tools/preview_font.py dist/gamedata/textures/ui/ui_font_arial_14_800.ini \\
        --text "The quick brown fox\\nПривет, Сталкер!" --scale 4
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from makefont import load_dds_alpha, parse_ini  # noqa: E402


def parse_color(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        raise argparse.ArgumentTypeError("expected RRGGBB or #RRGGBB")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("font", type=Path, help="path to the .ini file (or its stem)")
    parser.add_argument(
        "--text", default="STALKER Anomaly 123\nПривет, Сталкер!",
        help="text to render (default shows Latin and Cyrillic)",
    )
    parser.add_argument("--text-file", type=Path, default=None)
    parser.add_argument("--codepage", choices=("cp1251", "cp1252"), default="cp1251")
    parser.add_argument("--out", type=Path, default=Path("preview.png"))
    parser.add_argument("--scale", type=int, default=4)
    parser.add_argument("--line-spacing", type=int, default=0)
    parser.add_argument("--margin", type=int, default=8)
    parser.add_argument("--background", type=parse_color, default=(16, 16, 16))
    parser.add_argument("--color", type=parse_color, default=(232, 232, 232))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        from PIL import Image
    except ImportError:
        print("error: Pillow is required (pip install Pillow)", file=sys.stderr)
        return 2

    ini_path = args.font
    if ini_path.suffix != ".ini":
        ini_path = ini_path.with_suffix(".ini")
    if not ini_path.is_file():
        print(f"error: {ini_path} not found", file=sys.stderr)
        return 2

    ini = parse_ini(ini_path)
    alpha = load_dds_alpha(ini_path.with_suffix(".dds"))

    if ini.height is None:
        print("error: missing [symbol_coords] height", file=sys.stderr)
        return 2
    cell_height = int(round(ini.height))

    if args.text_file:
        text = args.text_file.read_text(encoding="utf-8")
    else:
        text = args.text
    lines = text.splitlines() or [""]

    def glyph_width(byte: int) -> int:
        row = ini.rows.get(byte)
        if row is None:
            return 0
        return max(0, row[2] - row[0])

    encoded_lines = [line.encode(args.codepage, errors="replace") for line in lines]
    max_width = max(
        (sum(glyph_width(byte) for byte in line) for line in encoded_lines),
        default=0,
    )
    line_step = cell_height + args.line_spacing
    mask = Image.new("L", (max(1, max_width), max(1, line_step * len(lines))), 0)

    for row_index, encoded in enumerate(encoded_lines):
        x = 0
        y = row_index * line_step
        for byte in encoded:
            row = ini.rows.get(byte)
            if row is None:
                continue
            x1, y1, x2, _y2 = row
            width = x2 - x1
            if width <= 0:
                continue
            box = (x1, y1, x1 + width, y1 + cell_height)
            if box[2] > alpha.width or box[3] > alpha.height:
                print(
                    f"warning: glyph {byte} at {box} is outside the atlas",
                    file=sys.stderr,
                )
                x += width
                continue
            crop = alpha.crop(box)
            mask.paste(crop, (x, y))
            x += width

    background = Image.new("RGB", mask.size, args.background)
    foreground = Image.new("RGB", mask.size, args.color)
    rendered = Image.composite(foreground, background, mask)

    margin = max(0, args.margin)
    canvas = Image.new(
        "RGB",
        (rendered.width + margin * 2, rendered.height + margin * 2),
        args.background,
    )
    canvas.paste(rendered, (margin, margin))

    if args.scale != 1:
        canvas = canvas.resize(
            (canvas.width * args.scale, canvas.height * args.scale),
            Image.NEAREST,
        )
    canvas.save(args.out)

    print(
        f"{ini_path.name}: atlas {alpha.width}x{alpha.height}, "
        f"cell height {cell_height}, {len(ini.rows)} rows"
    )
    print(f"wrote {args.out} ({canvas.width}x{canvas.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
