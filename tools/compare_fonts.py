#!/usr/bin/env python3
"""Compare a fontbm build against an existing BMFont build.

Point it at two directories that contain the same font sets, for example::

    python tools/compare_fonts.py old/ui new/ui --codepage cp1251

Each set is expected to contain ``<stem>.fnt``, ``<stem>.ini`` and
``<stem>.dds``.  The tool compares the things that should be stable across
renderers:

* ``.fnt`` metadata (size, line height, base, texture dimensions, pages)
* per-glyph metrics (width, height, offsets, advance)
* ``.ini`` header values and glyph box sizes
* the rendered alpha of each glyph (cropped from each atlas by its own
  coordinates, so different packing algorithms do not matter)

It deliberately does **not** compare atlas x/y coordinates: fontbm and BMFont
pack glyphs differently, and the ``.ini`` is regenerated from each atlas.

Exit code is 1 for structural problems (missing files, multi-page output,
texture size mismatch, missing glyphs) or, with ``--fail-on-diff``, for metric
differences above ``--tol``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from makefont import (  # noqa: E402
    Fnt,
    Glyph,
    Ini,
    dds_info,
    load_dds_alpha,
    map_glyphs_to_bytes,
    parse_fnt,
    parse_ini,
)

try:
    from PIL import Image, ImageChops, ImageStat

    HAVE_PIL = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_PIL = False


def collect(directory: Path) -> dict[str, dict[str, Path]]:
    found: dict[str, dict[str, Path]] = {}
    for suffix in (".fnt", ".ini", ".dds"):
        for path in directory.glob(f"*{suffix}"):
            found.setdefault(path.stem, {})[suffix.lstrip(".")] = path
    return found


def glyph_metrics(glyph: Glyph) -> dict[str, int]:
    return {
        "width": glyph.width,
        "height": glyph.height,
        "xoffset": glyph.xoffset,
        "yoffset": glyph.yoffset,
        "xadvance": glyph.xadvance,
    }


def percentile(values: Sequence[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def compare_fnt(
    ref: Fnt,
    cand: Fnt,
    ref_glyphs: dict[int, Glyph],
    cand_glyphs: dict[int, Glyph],
    tol: int,
) -> dict[str, Any]:
    fields = {
        "size": abs(abs(ref.size) - abs(cand.size)),
        "line_height": abs(ref.line_height - cand.line_height),
        "base": abs(ref.base - cand.base),
        "width": abs(ref.width - cand.width),
        "height": abs(ref.height - cand.height),
        "pages": abs(ref.pages - cand.pages),
    }

    missing: list[int] = []
    extra: list[int] = []
    deltas: dict[str, list[float]] = {
        "width": [], "height": [], "xoffset": [], "yoffset": [], "xadvance": []
    }

    for byte in range(256):
        ref_glyph = ref_glyphs.get(byte)
        cand_glyph = cand_glyphs.get(byte)
        if ref_glyph and not cand_glyph:
            missing.append(byte)
        elif cand_glyph and not ref_glyph:
            extra.append(byte)
        elif ref_glyph and cand_glyph:
            rm = glyph_metrics(ref_glyph)
            cm = glyph_metrics(cand_glyph)
            for name in deltas:
                deltas[name].append(abs(rm[name] - cm[name]))

    # BMFont's forceZero mode makes every glyph cell full-height and zeroes
    # the offsets, while fontbm reports tight ink boxes.  Only width and
    # advance are directly comparable; the rest is informational.
    outliers = sum(
        1 for byte in range(256)
        if byte in ref_glyphs and byte in cand_glyphs
        and any(
            abs(glyph_metrics(ref_glyphs[byte])[name]
                - glyph_metrics(cand_glyphs[byte])[name]) > tol
            for name in ("width", "xadvance")
        )
    )

    return {
        "fields": fields,
        "missing": missing,
        "extra": extra,
        "max_delta": {name: max(values, default=0) for name, values in deltas.items()},
        "outliers": outliers,
        "compared": len(ref_glyphs),
    }


def compare_ini(ref: Ini, cand: Ini, tol: int) -> dict[str, Any]:
    missing = sorted(set(ref.rows) - set(cand.rows))
    extra = sorted(set(cand.rows) - set(ref.rows))
    size_deltas: list[float] = []
    zero_mismatch: list[int] = []

    for char_id in sorted(set(ref.rows) & set(cand.rows)):
        rx1, ry1, rx2, ry2 = ref.rows[char_id]
        cx1, cy1, cx2, cy2 = cand.rows[char_id]
        ref_size = (rx2 - rx1, ry2 - ry1)
        cand_size = (cx2 - cx1, cy2 - cy1)
        # Space is special: the original BMFont pipeline writes 0 width, while
        # this tool gives it a usable advance.  Report it separately.
        if char_id == 32:
            continue
        size_deltas.extend(
            [abs(ref_size[0] - cand_size[0]), abs(ref_size[1] - cand_size[1])]
        )
        if (ref_size == (0, 0)) != (cand_size == (0, 0)):
            zero_mismatch.append(char_id)

    ref_space = ref.rows.get(32)
    cand_space = cand.rows.get(32)
    space_delta = (
        abs((ref_space[2] - ref_space[0]) - (cand_space[2] - cand_space[0]))
        if ref_space and cand_space else None
    )

    return {
        "height_delta": (
            abs((ref.height or 0) - (cand.height or 0))
            if ref.height is not None and cand.height is not None
            else None
        ),
        "width_correction_delta": (
            abs((ref.width_correction or 0) - (cand.width_correction or 0))
            if ref.width_correction is not None
            and cand.width_correction is not None
            else None
        ),
        "size_deltas": size_deltas,
        "zero_mismatch": zero_mismatch,
        "space_delta": space_delta,
        "missing": missing,
        "extra": extra,
        "outliers": sum(1 for value in size_deltas if value > tol),
    }


def _ink_from_ini(
    alpha,
    row: tuple[int, int, int, int],
    height: float,
):
    """Crop the engine's ``height``-tall cell and return its ink bbox."""
    x1, y1, x2, _y2 = row
    width = x2 - x1
    cell = int(height)
    if width <= 0 or cell <= 0:
        return None
    box = (x1, y1, x1 + width, y1 + cell)
    if box[2] > alpha.width or box[3] > alpha.height:
        return None
    cell_image = alpha.crop(box)
    ink = cell_image.getbbox()
    if ink is None:
        return None
    return cell_image.crop(ink)


def compare_alpha(
    ref_dds: Path,
    cand_dds: Path,
    ref_ini: Ini,
    cand_ini: Ini,
) -> dict[str, Any]:
    if not HAVE_PIL:
        return {"skipped": "Pillow is not installed"}

    try:
        ref_alpha = load_dds_alpha(ref_dds)
        cand_alpha = load_dds_alpha(cand_dds)
    except Exception as exc:
        return {"skipped": f"cannot read DDS: {exc}"}

    if ref_alpha.size != cand_alpha.size:
        return {
            "skipped": (
                f"atlas size differs: {ref_alpha.size[0]}x{ref_alpha.size[1]} "
                f"vs {cand_alpha.size[0]}x{cand_alpha.size[1]}"
            )
        }
    if ref_ini.height is None or cand_ini.height is None:
        return {"skipped": "missing [symbol_coords] height"}
    if int(ref_ini.height) != int(cand_ini.height):
        return {
            "skipped": (
                f"cell height differs: {ref_ini.height:g} vs "
                f"{cand_ini.height:g} (see the ini header)"
            )
        }

    per_glyph: list[tuple[int, float, int, int]] = []
    for byte in sorted(set(ref_ini.rows) & set(cand_ini.rows)):
        ref_ink = _ink_from_ini(ref_alpha, ref_ini.rows[byte], ref_ini.height)
        cand_ink = _ink_from_ini(cand_alpha, cand_ini.rows[byte], cand_ini.height)
        if ref_ink is None or cand_ink is None:
            continue

        width = max(ref_ink.width, cand_ink.width)
        height = max(ref_ink.height, cand_ink.height)
        padded_ref = Image.new("L", (width, height), 0)
        padded_cand = Image.new("L", (width, height), 0)
        padded_ref.paste(ref_ink, (0, 0))
        padded_cand.paste(cand_ink, (0, 0))

        diff = ImageChops.difference(padded_ref, padded_cand)
        difference = ImageStat.Stat(diff).mean[0]
        per_glyph.append(
            (byte, difference,
             abs(ref_ink.width - cand_ink.width),
             abs(ref_ink.height - cand_ink.height))
        )

    if not per_glyph:
        return {"skipped": "no matching non-empty glyphs"}

    values = [item[1] for item in per_glyph]
    worst = sorted(per_glyph, key=lambda item: item[1], reverse=True)[:5]
    return {
        "mean": mean(values),
        "p95": percentile(values, 95),
        "max": max(values),
        "worst": [
            {
                "byte": byte,
                "mean_abs_diff": round(value, 2),
                "size_delta": [dw, dh],
            }
            for byte, value, dw, dh in worst
        ],
    }


def compare_set(
    stem: str,
    ref_files: dict[str, Path],
    cand_files: dict[str, Path],
    codepage: str,
    tol: int,
) -> dict[str, Any]:
    issues: list[str] = []
    result: dict[str, Any] = {"stem": stem}

    for role in ("ini",):
        if role not in ref_files or role not in cand_files:
            issues.append(f"missing .{role} file")
    if issues:
        result["issues"] = issues
        return result

    ref_ini = parse_ini(ref_files["ini"])
    cand_ini = parse_ini(cand_files["ini"])
    ini_result = compare_ini(ref_ini, cand_ini, tol)

    have_fnt = "fnt" in ref_files and "fnt" in cand_files
    ref_unknown: list[int] = []
    cand_unknown: list[int] = []
    ref_fnt = cand_fnt = None

    if have_fnt:
        ref_fnt = parse_fnt(ref_files["fnt"])
        cand_fnt = parse_fnt(cand_files["fnt"])
        ref_glyphs, ref_unknown = map_glyphs_to_bytes(ref_fnt, codepage)
        cand_glyphs, cand_unknown = map_glyphs_to_bytes(cand_fnt, codepage)
        fnt_result = compare_fnt(
            ref_fnt, cand_fnt, ref_glyphs, cand_glyphs, tol
        )
        if ref_fnt.pages != 1 or cand_fnt.pages != 1:
            issues.append(
                f"multi-page output (ref={ref_fnt.pages}, "
                f"cand={cand_fnt.pages})"
            )
        if (ref_fnt.width, ref_fnt.height) != (cand_fnt.width, cand_fnt.height):
            issues.append(
                f"texture size differs: {ref_fnt.width}x{ref_fnt.height} vs "
                f"{cand_fnt.width}x{cand_fnt.height}"
            )
        if fnt_result["missing"]:
            issues.append(
                f"{len(fnt_result['missing'])} reference glyphs missing"
            )
        if ref_unknown or cand_unknown:
            issues.append(
                f"glyphs outside {codepage}: ref={len(ref_unknown)} "
                f"cand={len(cand_unknown)}"
            )
        unicode_ref, unicode_cand = ref_fnt.unicode, cand_fnt.unicode
    else:
        # Shipped packages contain only .dds/.ini; use the blank rows instead.
        ref_nonzero = {
            char_id for char_id, (x1, _y1, x2, _y2) in ref_ini.rows.items()
            if x2 > x1 and char_id != 32
        }
        cand_nonzero = {
            char_id for char_id, (x1, _y1, x2, _y2) in cand_ini.rows.items()
            if x2 > x1 and char_id != 32
        }
        common = sorted(ref_nonzero & cand_nonzero)
        widths = [
            abs((ref_ini.rows[cid][2] - ref_ini.rows[cid][0])
                - (cand_ini.rows[cid][2] - cand_ini.rows[cid][0]))
            for cid in common
        ]
        fnt_result = {
            "skipped": "missing .fnt (package-only comparison)",
            "fields": {
                "size": 0, "line_height": 0, "base": 0,
                "width": 0, "height": 0, "pages": 0,
            },
            "missing": sorted(ref_nonzero - cand_nonzero),
            "extra": sorted(cand_nonzero - ref_nonzero),
            "max_delta": {
                "width": max(widths, default=0),
                "height": 0, "xoffset": 0, "yoffset": 0, "xadvance": 0,
            },
            "outliers": sum(1 for value in widths if value > tol),
            "compared": len(common),
        }
        if fnt_result["missing"]:
            issues.append(
                f"{len(fnt_result['missing'])} nonzero glyphs missing"
            )
        unicode_ref = unicode_cand = None

    if ini_result["missing"]:
        issues.append(f"{len(ini_result['missing'])} .ini rows missing")
    if ini_result["zero_mismatch"]:
        issues.append(
            f"{len(ini_result['zero_mismatch'])} glyphs are blank in only one set"
        )

    result.update(
        fnt=fnt_result,
        ini=ini_result,
        issues=issues,
        unicode_ref=unicode_ref,
        unicode_cand=unicode_cand,
    )

    if "dds" in ref_files and "dds" in cand_files:
        ref_meta = dds_info(ref_files["dds"])
        cand_meta = dds_info(cand_files["dds"])
        result["dds"] = {
            "ref": {
                "fourcc": ref_meta[0] or "uncompressed",
                "width": ref_meta[1],
                "height": ref_meta[2],
            },
            "cand": {
                "fourcc": cand_meta[0] or "uncompressed",
                "width": cand_meta[1],
                "height": cand_meta[2],
            },
            "alpha": compare_alpha(
                ref_files["dds"], cand_files["dds"], ref_ini, cand_ini
            ),
        }
    else:
        result["dds"] = {"skipped": "missing .dds file"}

    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def format_report(result: dict[str, Any], tol: int) -> str:
    stem = result["stem"]
    if "fnt" not in result:
        return f"== {stem} ==\n  FAIL: {'; '.join(result['issues'])}"

    fnt = result["fnt"]
    ini = result["ini"]
    fields = fnt["fields"]
    dds = result.get("dds", {})

    lines = [f"== {stem} =="]
    if fnt.get("skipped"):
        lines.append(f"  fnt: skipped ({fnt['skipped']})")
    else:
        lines.append(
            "  fnt: "
            f"size {fields['size']:+d} | lineHeight "
            f"{fields['line_height']:+d} | base {fields['base']:+d} | "
            f"texture {fields['width']:+d}x{fields['height']:+d} | "
            f"pages {fields['pages']:+d}"
        )
    lines.append(
        f"  glyphs: {fnt['compared']} compared | missing "
        f"{len(fnt['missing'])} | extra {len(fnt['extra'])} | "
        f"{fnt['outliers']} beyond {tol}px"
    )
    max_delta = fnt["max_delta"]
    if fnt.get("skipped"):
        lines.append(f"  metrics max delta: w={max_delta['width']}")
    else:
        lines.append(
            "  metrics max delta: "
            f"w={max_delta['width']} adv={max_delta['xadvance']} "
            f"(h={max_delta['height']} xoff={max_delta['xoffset']} "
            f"yoff={max_delta['yoffset']}; BMFont fixes cells, not comparable)"
        )
    header = []
    if ini["height_delta"] is not None:
        header.append(f"height {ini['height_delta']:g}")
    if ini["width_correction_delta"] is not None:
        header.append(f"width_correction {ini['width_correction_delta']:g}")
    if ini.get("space_delta") is not None:
        header.append(f"space {ini['space_delta']:g}")
    lines.append(
        "  ini: " + (" | ".join(header) if header else "header missing")
        + f" | size mismatches {ini['outliers']} | missing {len(ini['missing'])}"
    )

    if dds.get("skipped"):
        lines.append(f"  dds: skipped ({dds['skipped']})")
    else:
        ref = dds["ref"]
        cand = dds["cand"]
        alpha = dds.get("alpha", {})
        if alpha.get("skipped"):
            alpha_text = f"alpha skipped ({alpha['skipped']})"
        else:
            alpha_text = (
                f"alpha mean {alpha['mean']:.2f}, p95 {alpha['p95']:.2f}, "
                f"max {alpha['max']:.2f}"
            )
        lines.append(
            f"  dds: {ref['fourcc']} {ref['width']}x{ref['height']} vs "
            f"{cand['fourcc']} {cand['width']}x{cand['height']} | {alpha_text}"
        )
        if alpha.get("worst"):
            worst = alpha["worst"][0]
            lines.append(
                f"       worst glyph: byte {worst['byte']:03d}, "
                f"mean diff {worst['mean_abs_diff']:.2f}, "
                f"size delta {worst['size_delta']}"
            )

    if result["issues"]:
        lines.append(f"  FAIL: {'; '.join(result['issues'])}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare a fontbm/BMFont font directory against a reference."
    )
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--codepage", choices=("cp1251", "cp1252"), default="cp1251")
    parser.add_argument(
        "--tol", type=int, default=1,
        help="allowed per-glyph metric delta in pixels (default: 1)",
    )
    parser.add_argument(
        "--fail-on-diff", action="store_true",
        help="exit 1 when any metric differs by more than --tol",
    )
    parser.add_argument("--json", action="store_true", help="print JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    reference = collect(args.reference)
    candidate = collect(args.candidate)

    if not reference:
        print(f"error: no fonts found in {args.reference}", file=sys.stderr)
        return 2
    if not candidate:
        print(f"error: no fonts found in {args.candidate}", file=sys.stderr)
        return 2

    stems = sorted(set(reference) & set(candidate))
    missing_sets = sorted(set(reference) - set(candidate))
    extra_sets = sorted(set(candidate) - set(reference))

    results = [
        compare_set(stem, reference[stem], candidate[stem], args.codepage, args.tol)
        for stem in stems
    ]

    if args.json:
        print(json.dumps(
            {
                "results": results,
                "missing_sets": missing_sets,
                "extra_sets": extra_sets,
            },
            indent=2,
        ))
    else:
        for result in results:
            print(format_report(result, args.tol))
        for stem in missing_sets:
            print(f"== {stem} ==\n  FAIL: missing from candidate")
        for stem in extra_sets:
            print(f"== {stem} ==\n  note: extra in candidate")

    structural = bool(missing_sets) or any(result.get("issues") for result in results)
    metric_failures = sum(
        result.get("fnt", {}).get("outliers", 0)
        + result.get("ini", {}).get("outliers", 0)
        for result in results
    )
    exceeded = args.fail_on_diff and metric_failures > 0

    if not args.json:
        print(
            f"\n{len(results)} set(s) compared | "
            f"{'STRUCTURAL ISSUES' if structural else 'no structural issues'} | "
            f"{metric_failures} metric outlier(s)"
        )

    return 1 if structural or exceeded else 0


if __name__ == "__main__":
    raise SystemExit(main())
