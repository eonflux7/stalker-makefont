#!/usr/bin/env python3
"""Build X-Ray / S.T.A.L.K.E.R. bitmap font packages.

Two renderers are supported:

* ``fontbm`` (default) - cross-platform FreeType based CLI.  It writes a
  BMFont compatible ``.fnt`` and PNG pages; this script converts the PNG to
  DDS (BC3/DXT5 by default when a compressor is available, otherwise
  uncompressed A8R8G8B8 via Pillow).
* ``bmfont`` - the original Windows tool.  It writes DDS itself, but needs
  Windows (or Wine) and the fonts installed in the system.

The generated ``.ini`` files are keyed by *engine character byte* (0-255),
not by Unicode codepoint.  When a Unicode renderer such as fontbm is used,
pass ``--codepage cp1251`` (the default, for Russian STALKER builds) or
``--codepage cp1252``; the mapping is applied automatically.

Examples
--------
    # Linux/macOS, fonts in ./fonts/
    python3 makefont.py --backend fontbm --font-dir fonts

    # one family only, keep the intermediate files
    python3 makefont.py --only arial --keep-work

    # original Windows tool
    python3 makefont.py --backend bmfont

    # custom DDS compressor
    python3 makefont.py --dds-command 'compressonatorcli -fd BC3 {in} {out}'
"""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = SCRIPT_DIR / "template_conf.bmfc"


# ---------------------------------------------------------------------------
# Font set definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Family:
    key: str
    display_name: str          # name used by BMFont (installed font)
    default_font_file: str     # used by fontbm
    prefix: str


FAMILIES: dict[str, Family] = {
    "arial": Family(
        key="arial",
        display_name="Open Sans Condensed",
        default_font_file="OpenSansCondensed-Regular.ttf",
        prefix="ui_font_arial_",
    ),
    "graff": Family(
        key="graff",
        display_name="ELEGANT TYPEWRITER",
        default_font_file="ElegantTypewriter.ttf",
        prefix="ui_font_graff_",
    ),
    "letter": Family(
        key="letter",
        display_name="Open Sans Condensed",
        default_font_file="OpenSansCondensed-Regular.ttf",
        prefix="ui_font_letter_",
    ),
}

# label -> base size.  The base size is the original script's `instance[1]`;
# BMFont was configured with base + 4 points/pixels.
LAYOUT: dict[str, list[tuple[str, int]]] = {
    "arial": [
        ("14_800", 16), ("14_1024", 16), ("14_1600", 16),
        ("21_800", 16), ("21_1024", 16), ("21_1600", 23),
    ],
    "graff": [
        ("19_800", 16), ("19_1024", 16), ("19_1600", 26),
        ("22_800", 16), ("22_1024", 16), ("22_1600", 26),
        ("32_800", 23), ("32_1024", 30), ("32_1600", 43),
        ("40_800", 31), ("40_1024", 38), ("40_1600", 58),
        ("50_800", 57), ("50_1024", 49), ("50_1600", 74),
    ],
    "letter": [
        ("16_800", 16), ("16_1024", 16), ("16_1600", 22),
        ("18_800", 16), ("18_1024", 16), ("18_1600", 23),
        ("25_800", 22), ("25_1024", 26), ("25_1600", 38),
    ],
}


@dataclass(frozen=True)
class Job:
    family: Family
    label: str
    base_size: int
    suffix: str = ""

    @property
    def stem(self) -> str:
        return f"{self.family.prefix}{self.label}{self.suffix}"

    def font_size(self, offset: int) -> int:
        return self.base_size + offset

    def texture_size(self, _offset: int = 0) -> int:
        # The original script picks the tier from the base size (not base+4).
        size = self.base_size
        if size <= 20:
            return 256
        if size <= 40:
            return 512
        return 1024


def all_jobs() -> list[Job]:
    jobs: list[Job] = []
    for key, family in FAMILIES.items():
        for label, base_size in LAYOUT[key]:
            jobs.append(Job(family=family, label=label, base_size=base_size))
    return jobs


# ---------------------------------------------------------------------------
# Character set / codepage handling
# ---------------------------------------------------------------------------

# The exact set from the original template_conf.bmfc (a Windows ANSI set).
DEFAULT_CHARSET_BYTES: tuple[int, ...] = (
    tuple(range(32, 127))
    + (128,)
    + tuple(range(130, 141))
    + (142,)
    + tuple(range(145, 157))
    + tuple(range(158, 256))
)


def format_ranges(values: Iterable[int]) -> str:
    """Compress a set of integers into fontbm/BMFont's ``a-b,c`` syntax."""
    numbers = sorted(set(values))
    if not numbers:
        return ""
    parts: list[str] = []
    start = prev = numbers[0]
    for value in numbers[1:]:
        if value == prev + 1:
            prev = value
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = value
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(parts)


def byte_unicode_map(codepage: str) -> tuple[dict[int, int], dict[int, int]]:
    """Return (byte -> Unicode, Unicode -> byte) for the engine charset.

    Undefined bytes in the codepage are omitted.  For CP1251 that is 0x98;
    for CP1252 it is 0x81, 0x8D, 0x8F, 0x90 and 0x9D.
    """
    byte_to_uni: dict[int, int] = {}
    uni_to_byte: dict[int, int] = {}
    for byte in DEFAULT_CHARSET_BYTES:
        try:
            char = bytes([byte]).decode(codepage)
        except UnicodeDecodeError:
            continue
        codepoint = ord(char)
        byte_to_uni[byte] = codepoint
        uni_to_byte.setdefault(codepoint, byte)
    return byte_to_uni, uni_to_byte


def fontbm_chars(codepage: str) -> str:
    """The ``--chars`` argument for fontbm (Unicode codepoints)."""
    _, uni_to_byte = byte_unicode_map(codepage)
    return format_ranges(uni_to_byte.keys())


# ---------------------------------------------------------------------------
# FNT parsing
# ---------------------------------------------------------------------------

@dataclass
class Glyph:
    id: int
    x: int
    y: int
    width: int
    height: int
    xoffset: int = 0
    yoffset: int = 0
    xadvance: int = 0
    page: int = 0


@dataclass
class Fnt:
    font_name: str
    size: int
    unicode: bool
    line_height: int
    base: int
    width: int
    height: int
    pages: int
    page_files: list[str]
    glyphs: dict[int, Glyph]


def _attr_int(element: ET.Element | None, name: str, default: int = 0) -> int:
    if element is None:
        return default
    value = element.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return int(float(value))


def parse_fnt(path: Path) -> Fnt:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise RuntimeError(f"{path}: not valid XML ({exc})") from exc
    if root.tag != "font":
        raise RuntimeError(f"{path}: unexpected root element <{root.tag}>")

    info = root.find("info")
    common = root.find("common")
    if info is None or common is None:
        raise RuntimeError(f"{path}: missing <info> or <common>")

    pages_node = root.find("pages")
    page_files = [
        page.get("file", "")
        for page in (pages_node.findall("page") if pages_node is not None else [])
    ]

    glyphs: dict[int, Glyph] = {}
    chars_node = root.find("chars")
    if chars_node is not None:
        for node in chars_node.findall("char"):
            glyph = Glyph(
                id=_attr_int(node, "id"),
                x=_attr_int(node, "x"),
                y=_attr_int(node, "y"),
                width=_attr_int(node, "width"),
                height=_attr_int(node, "height"),
                xoffset=_attr_int(node, "xoffset"),
                yoffset=_attr_int(node, "yoffset"),
                xadvance=_attr_int(node, "xadvance"),
                page=_attr_int(node, "page"),
            )
            glyphs[glyph.id] = glyph

    return Fnt(
        font_name=info.get("face", ""),
        size=_attr_int(info, "size"),
        unicode=info.get("unicode", "0") == "1",
        line_height=_attr_int(common, "lineHeight"),
        base=_attr_int(common, "base"),
        width=_attr_int(common, "scaleW"),
        height=_attr_int(common, "scaleH"),
        pages=_attr_int(common, "pages", 1),
        page_files=page_files,
        glyphs=glyphs,
    )


def map_glyphs_to_bytes(fnt: Fnt, codepage: str) -> tuple[dict[int, Glyph], list[int]]:
    """Map a Unicode .fnt back to engine bytes.

    Returns (glyphs by byte, unmapped Unicode codepoints).
    BMFont ANSI output already has byte ids and is passed through.
    """
    if not fnt.unicode:
        return {gid: glyph for gid, glyph in fnt.glyphs.items() if 0 <= gid < 256}, []

    _, uni_to_byte = byte_unicode_map(codepage)
    mapped: dict[int, Glyph] = {}
    unknown: list[int] = []
    for codepoint, glyph in fnt.glyphs.items():
        byte = uni_to_byte.get(codepoint)
        if byte is None:
            unknown.append(codepoint)
            continue
        mapped[byte] = replace(glyph, id=byte)
    return mapped, unknown


# ---------------------------------------------------------------------------
# INI generation
# ---------------------------------------------------------------------------

def _fmt_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def build_ini(
    glyphs: dict[int, Glyph],
    height: float,
    width_correction: float = -1.0,
    count: int = 256,
) -> str:
    lines = [
        "[width_correction]",
        # Keep the float form used by the original script (-1.0, not -1).
        f"value = {width_correction}",
        "",
        "[symbol_coords]",
        f"height = {_fmt_number(height)}",
    ]
    for char_id in range(count):
        glyph = glyphs.get(char_id)
        if glyph is None:
            x = y = width = glyph_height = 0
        else:
            x, y, width, glyph_height = (
                glyph.x, glyph.y, glyph.width, glyph.height
            )
        lines.append(
            f"{char_id:03d} = {x}, {y}, {x + width}, {y + glyph_height}"
        )
    return "\n".join(lines) + "\n"


@dataclass
class Ini:
    height: float | None
    width_correction: float | None
    rows: dict[int, tuple[int, int, int, int]]


_INI_ROW_RE = re.compile(
    r"^(\d{3})\s*=\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*$"
)


def parse_ini(path: Path) -> Ini:
    """Parse the X-Ray ``[symbol_coords]`` format."""
    height: float | None = None
    width_correction: float | None = None
    rows: dict[int, tuple[int, int, int, int]] = {}
    section = ""

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line
            continue
        if section == "[width_correction]" and line.startswith("value"):
            width_correction = float(line.split("=", 1)[1].strip())
            continue
        if section == "[symbol_coords]":
            if line.startswith("height"):
                height = float(line.split("=", 1)[1].strip())
                continue
            match = _INI_ROW_RE.match(line)
            if match:
                rows[int(match.group(1))] = (
                    int(match.group(2)),
                    int(match.group(3)),
                    int(match.group(4)),
                    int(match.group(5)),
                )
    return Ini(height, width_correction, rows)


# ---------------------------------------------------------------------------
# Rendering backends
# ---------------------------------------------------------------------------

@dataclass
class Rendered:
    fnt: Path
    texture: Path
    dds: Path | None = None


def _command(value: str | Sequence[str]) -> list[str]:
    parts = shlex.split(value) if isinstance(value, str) else list(value)
    if parts and ("/" in parts[0] or "\\" in parts[0]):
        parts[0] = str(Path(parts[0]).expanduser().resolve())
    return parts


def _run(cmd: Sequence[str], cwd: Path | None = None, verbose: bool = False) -> None:
    printable = " ".join(shlex.quote(str(part)) for part in cmd)
    if verbose:
        print(f"+ {printable}")
    proc = subprocess.run(
        [str(part) for part in cmd],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"command failed with exit code {proc.returncode}:\n  {printable}"
            + (f"\n{detail}" if detail else "")
        )


def render_fontbm(
    job: Job,
    font_path: Path,
    font_size: int,
    texture_size: int,
    chars: str,
    work_dir: Path,
    fontbm: str,
    no_antialias: bool,
    verbose: bool,
) -> Rendered:
    stem = work_dir / job.stem
    fnt = stem.with_suffix(".fnt")
    png = stem.with_suffix(".png")

    cmd = _command(fontbm) + [
        "--font-file", str(font_path.resolve()),
        "--font-size", str(font_size),
        "--chars", chars,
        "--texture-size", f"{texture_size}x{texture_size}",
        "--max-texture-count", "1",
        "--texture-name-suffix", "none",
        "--padding-up", "0", "--padding-right", "0",
        "--padding-down", "0", "--padding-left", "0",
        "--spacing-horiz", "1", "--spacing-vert", "1",
        "--align-horiz", "1", "--align-vert", "1",
        "--data-format", "xml",
        "--kerning-pairs", "disabled",
        "--output", str(stem),
    ]
    if no_antialias:
        cmd.append("--monochrome")

    _run(cmd, cwd=work_dir, verbose=verbose)
    if not fnt.is_file():
        raise RuntimeError(f"fontbm did not produce {fnt.name}")
    if not png.is_file():
        pages = sorted(work_dir.glob(f"{job.stem}*.png"))
        if len(pages) != 1:
            raise RuntimeError(
                f"expected one texture page for {job.stem}, found "
                f"{[p.name for p in pages] or 'none'}"
            )
        png = pages[0]
    return Rendered(fnt=fnt, texture=png)


def _fontbm_line_height(
    font_path: Path,
    font_size: int,
    work_dir: Path,
    fontbm: str,
    verbose: bool,
) -> int:
    """Render a one-glyph probe and return fontbm's line height."""
    stem = work_dir / f".probe-{font_path.stem}-{font_size}"
    cmd = _command(fontbm) + [
        "--font-file", str(font_path.resolve()),
        "--font-size", str(font_size),
        "--chars", "65",
        "--texture-size", "256x256",
        "--max-texture-count", "1",
        "--texture-name-suffix", "none",
        "--data-format", "xml",
        "--output", str(stem),
    ]
    _run(cmd, cwd=work_dir, verbose=verbose)
    fnt_path = stem.with_suffix(".fnt")
    try:
        return parse_fnt(fnt_path).line_height
    finally:
        for path in (fnt_path, stem.with_suffix(".png")):
            path.unlink(missing_ok=True)


def calibrate_fontbm_size(
    font_path: Path,
    target_cell: int,
    work_dir: Path,
    fontbm: str,
    verbose: bool,
) -> tuple[int, int]:
    """Pick a fontbm size whose line height matches the BMFont cell height.

    BMFont's positive ``lfHeight`` is a *cell* height; fontbm's ``--font-size``
    follows the font's line metrics, which are usually 20-35% taller.  Probe
    and scale so the glyphs keep BMFont's visual size inside the original
    ``[symbol_coords] height``.
    """
    line = _fontbm_line_height(font_path, target_cell, work_dir, fontbm, verbose)
    size = target_cell
    if line <= 0:
        return size, line
    best = (abs(line - target_cell), line > target_cell, size, line)

    for _ in range(3):
        if line == target_cell:
            break
        candidate = max(1, round(size * target_cell / line))
        if candidate == size:
            break
        size = candidate
        line = _fontbm_line_height(font_path, size, work_dir, fontbm, verbose)
        score = (abs(line - target_cell), line > target_cell, size, line)
        if score < best:
            best = score
        if line <= target_cell:
            break

    _, _, best_size, best_line = best
    if verbose:
        print(
            f"  calibration: fontbm {best_size}px -> lineHeight "
            f"{best_line} (target {target_cell})"
        )
    return best_size, best_line


def render_bmfont(
    job: Job,
    font_size: int,
    texture_size: int,
    work_dir: Path,
    template_path: Path,
    bmfont: str,
    windows_charset: int | None,
    font_path: Path | None,
    verbose: bool,
) -> Rendered:
    template = template_path.read_text(encoding="utf-8")
    config = (
        template
        .replace("$fontname", job.family.display_name)
        .replace("$fontsize", str(font_size))
        .replace("$w", str(texture_size))
        .replace("$h", str(texture_size))
    )
    if windows_charset is not None:
        config = config.replace("charSet=0", f"charSet={windows_charset}")
    if font_path is not None and font_path.is_file():
        config = config.replace("fontFile=", f"fontFile={font_path.resolve()}")

    bmfc = work_dir / f"{job.stem}.bmfc"
    bmfc.write_text(config, encoding="utf-8")
    fnt = work_dir / f"{job.stem}.fnt"

    _run(
        _command(bmfont) + ["-c", bmfc.name, "-o", fnt.name],
        cwd=work_dir,
        verbose=verbose,
    )
    if not fnt.is_file():
        raise RuntimeError(f"BMFont did not produce {fnt.name}")

    dds_files = sorted(work_dir.glob(f"{job.stem}*.dds"))
    if len(dds_files) != 1:
        raise RuntimeError(
            f"expected one .dds for {job.stem}, found "
            f"{[p.name for p in dds_files] or 'none'}; multi-page fonts are "
            "not supported (lower the font size or raise the texture size)"
        )
    dds = work_dir / f"{job.stem}.dds"
    if dds_files[0] != dds:
        dds_files[0].replace(dds)
    return Rendered(fnt=fnt, texture=dds, dds=dds)


# ---------------------------------------------------------------------------
# DDS conversion / validation
# ---------------------------------------------------------------------------

def dds_info(path: Path) -> tuple[str, int, int, bool]:
    """Return (fourCC, width, height, has_alpha_flag) from a DDS header."""
    header = path.read_bytes()[:128]
    if len(header) < 128 or header[:4] != b"DDS ":
        raise RuntimeError(f"{path}: not a DDS file")
    height = int.from_bytes(header[12:16], "little")
    width = int.from_bytes(header[16:20], "little")
    pixel_flags = int.from_bytes(header[80:84], "little")
    fourcc = header[84:88].decode("ascii", "replace").strip("\x00")
    # DDPF_ALPHAPIXELS (0x1) or the legacy DDPF_ALPHA (0x2).
    return fourcc, width, height, bool(pixel_flags & 0x3)


def load_dds_alpha(path: Path):
    """Load a DDS atlas and return its alpha channel as a Pillow ``L`` image.

    Supports compressed DXT1/3/5 through Pillow, uncompressed 32-bit RGBA
    through Pillow, and the 8-bit alpha-only DDS used by some X-Ray fonts.
    """
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError("Pillow is required to read font atlases") from exc

    fourcc, width, height, _has_alpha = dds_info(path)
    raw = path.read_bytes()
    if not fourcc:
        bpp = int.from_bytes(raw[88:92], "little")
        if bpp == 8:
            pixels = raw[128:128 + width * height]
            if len(pixels) != width * height:
                raise RuntimeError(f"{path}: truncated 8-bit DDS")
            # X-Ray's 8-bit font textures store coverage directly.
            return Image.frombytes("L", (width, height), pixels)

    try:
        image = Image.open(path).convert("RGBA")
    except Exception as exc:
        raise RuntimeError(f"{path}: unsupported DDS format ({exc})") from exc
    return image.getchannel("A")


def detect_dds_tool(explicit: str) -> str:
    if explicit != "auto":
        return explicit
    for candidate in ("compressonatorcli", "CompressonatorCLI", "nvcompress"):
        if shutil.which(candidate):
            return candidate
    try:
        import PIL  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "no DDS compressor found. Install compressonatorcli or nvcompress, "
            "install Pillow for uncompressed DDS output, or pass --dds-command."
        ) from exc
    return "pillow"


def write_alpha8_dds(png: Path, dds: Path) -> None:
    """Write the 8-bit alpha-only DDS used by some X-Ray fonts.

    This is the format GAMMA's optional font addon uses: header flags
    ``DDPF_ALPHA``, 8 bits per pixel, alpha mask 0xFF, raw coverage data.
    """
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required for --alpha8-dds") from exc

    alpha = Image.open(png).convert("RGBA").getchannel("A")
    width, height = alpha.size
    header = bytearray(128)
    header[0:4] = b"DDS "
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<I", header, 8, 0x00081007)  # caps|h|w|pf|linearsize
    struct.pack_into("<I", header, 12, height)
    struct.pack_into("<I", header, 16, width)
    struct.pack_into("<I", header, 20, width * height)
    struct.pack_into("<I", header, 76, 32)       # pixel format size
    struct.pack_into("<I", header, 80, 0x2)      # DDPF_ALPHA
    struct.pack_into("<I", header, 88, 8)        # bits per pixel
    struct.pack_into("<I", header, 104, 0xFF)    # alpha mask
    struct.pack_into("<I", header, 108, 0x1000)  # DDSCAPS_TEXTURE
    dds.write_bytes(bytes(header) + alpha.tobytes())


def convert_to_dds(
    png: Path,
    dds: Path,
    tool: str,
    custom_command: str | None,
    verbose: bool,
    alpha8: bool = False,
) -> None:
    if alpha8:
        write_alpha8_dds(png, dds)
    elif custom_command:
        cmd = [
            part.format(input=str(png), output=str(dds))
            for part in shlex.split(custom_command)
        ]
        _run(cmd, verbose=verbose)
    elif tool in ("compressonator", "compressonatorcli", "CompressonatorCLI"):
        exe = shutil.which("compressonatorcli") or shutil.which("CompressonatorCLI")
        if not exe:
            raise RuntimeError("compressonatorcli not found in PATH")
        _run([exe, "-fd", "BC3", str(png), str(dds)], verbose=verbose)
    elif tool == "nvcompress":
        exe = shutil.which("nvcompress")
        if not exe:
            raise RuntimeError("nvcompress not found in PATH")
        _run([exe, "-bc3", "-nomips", str(png), str(dds)], verbose=verbose)
    elif tool == "pillow":
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("Pillow is required for --dds-tool pillow") from exc
        Image.open(png).convert("RGBA").save(dds)
    else:
        raise RuntimeError(f"unknown DDS tool: {tool}")

    if not dds.is_file():
        raise RuntimeError(f"DDS conversion did not produce {dds}")
    fourcc, _, _, has_alpha = dds_info(dds)
    if fourcc == "DX10":
        print(
            f"warning: {dds.name} uses a DX10 header; X-Ray expects legacy "
            "DXT5. Try another compressor.",
            file=sys.stderr,
        )
    elif not fourcc and not has_alpha:
        print(
            f"warning: {dds.name} has no alpha channel; the font will not "
            "render.",
            file=sys.stderr,
        )


def fixed_cell_height(fnt: Fnt, glyphs: dict[int, Glyph], font_size: int) -> int:
    """Smallest cell height that fits every glyph without clipping."""
    active = [
        glyph for glyph in glyphs.values()
        if glyph.width > 0 and glyph.height > 0
    ]
    if not active:
        return font_size
    min_top = min(glyph.yoffset for glyph in active)
    max_bottom = max(glyph.yoffset + glyph.height for glyph in active)
    return max(font_size, fnt.line_height, max_bottom - min_top)


def repack_fixed_height(
    fnt: Fnt,
    glyphs: dict[int, Glyph],
    source_png: Path,
    output_png: Path,
    texture_size: int,
    font_size: int,
    cell_height: int | None = None,
    spacing: int = 1,
    space_width: int | None = None,
) -> tuple[dict[int, Glyph], int, int]:
    """Rebuild an atlas with fixed-height cells.

    X-Ray samples a ``height``-tall strip at each glyph's ``y`` and ignores
    the fourth coordinate, so every cell must be exactly that tall.  BMFont
    gets this via ``forceZero=1``; fontbm does not, hence this step.

    Returns (glyphs with new coordinates, cell height, clipped glyph count).
    """
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Pillow is required for the fontbm fixed-height repack"
        ) from exc

    active = [
        glyph for glyph in glyphs.values()
        if glyph.width > 0 and glyph.height > 0
    ]
    if not active:
        raise RuntimeError("font has no non-empty glyphs")

    min_top = min(glyph.yoffset for glyph in active)
    max_bottom = max(glyph.yoffset + glyph.height for glyph in active)
    # Normalise the ink to the top of the cell (works for negative bearings too).
    shift = -min_top

    if cell_height is None:
        cell_height = max(font_size, fnt.line_height, max_bottom - min_top)
    cell = cell_height

    source = Image.open(source_png).convert("RGBA")
    atlas = Image.new("RGBA", (texture_size, texture_size), (255, 255, 255, 0))
    new_glyphs: dict[int, Glyph] = {}
    x = y = 0
    clipped = 0

    for byte in sorted(glyphs):
        glyph = glyphs[byte]
        is_space = byte == 32
        if is_space and glyph.width <= 0:
            width = space_width
            if width is None:
                width = glyph.xadvance or max(1, font_size // 4)
            width = max(1, width)
            if x + width > texture_size:
                x = 0
                y += cell + spacing
            if y + cell > texture_size:
                raise RuntimeError(
                    f"fixed-height packing for {source_png.stem} needs more "
                    f"than {texture_size}x{texture_size}; pass a larger "
                    "--texture-size"
                )
            # Blank cell: the engine advances by the cell width.
            new_glyphs[byte] = replace(
                glyph, x=x, y=y, width=width, height=cell,
                xoffset=0, yoffset=0,
            )
            x += width + spacing
            continue

        width = glyph.width
        if width <= 0 or glyph.height <= 0:
            new_glyphs[byte] = replace(
                glyph, x=0, y=0, width=0, height=0, xoffset=0, yoffset=0
            )
            continue
        if width > texture_size:
            raise RuntimeError(
                f"glyph {byte} is {width}px wide, wider than the "
                f"{texture_size}px texture"
            )

        if x + width > texture_size:
            x = 0
            y += cell + spacing
        if y + cell > texture_size:
            raise RuntimeError(
                f"fixed-height packing for {source_png.stem} needs more than "
                f"{texture_size}x{texture_size}; pass a larger --texture-size"
            )

        top = glyph.yoffset + shift
        source_top = 0
        crop_height = glyph.height
        if top < 0:
            source_top = -top
            crop_height -= source_top
            top = 0
        if top + crop_height > cell:
            crop_height = cell - top
        if crop_height < glyph.height:
            clipped += 1
        if crop_height <= 0:
            new_glyphs[byte] = replace(
                glyph, x=x, y=y, width=glyph.width, height=cell,
                xoffset=0, yoffset=0,
            )
            x += glyph.width + spacing
            continue

        box = (
            glyph.x,
            glyph.y + source_top,
            glyph.x + glyph.width,
            glyph.y + source_top + crop_height,
        )
        if box[2] > source.width or box[3] > source.height:
            raise RuntimeError(
                f"{source_png}: glyph {byte} box {box} is outside the atlas"
            )
        alpha = source.crop(box).getchannel("A")
        glyph_image = Image.new("RGBA", alpha.size, (255, 255, 255, 0))
        glyph_image.putalpha(alpha)
        atlas.paste(glyph_image, (x, y + top), glyph_image)

        new_glyphs[byte] = replace(
            glyph,
            x=x,
            y=y,
            width=glyph.width,
            height=cell,
            xoffset=0,
            yoffset=0,
        )
        x += glyph.width + spacing

    atlas.save(output_png)
    return new_glyphs, cell, clipped


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------

def package_outputs(
    rendered: Sequence[tuple[Path, Path]],
    out_dir: Path,
    zip_name: str,
) -> Path:
    ui_dir = out_dir / "gamedata" / "textures" / "ui"
    ui_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    for dds, ini in rendered:
        for source in (dds, ini):
            target = ui_dir / source.name
            shutil.copy2(source, target)
            copied.append(target)

    archive = out_dir / zip_name
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(copied):
            zf.write(path, f"gamedata/textures/ui/{path.name}")
    return archive


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build X-Ray/STALKER bitmap font packages.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples\n--------", 1)[-1],
    )
    parser.add_argument("--backend", choices=("fontbm", "bmfont"), default="fontbm")
    parser.add_argument(
        "--fontbm", default="fontbm",
        help="fontbm command or path (default: fontbm)",
    )
    parser.add_argument(
        "--bmfont", default="bmfont64.exe",
        help='BMFont command or path, e.g. "wine bmfont64.exe"',
    )
    parser.add_argument(
        "--font", action="append", default=[], metavar="FAMILY=PATH",
        help="override a font file, e.g. --font arial=/path/OpenSans.ttf",
    )
    parser.add_argument("--font-dir", default="fonts")
    parser.add_argument(
        "--codepage", choices=("cp1251", "cp1252"), default="cp1251",
        help="engine codepage used for the .ini byte ids (default: cp1251)",
    )
    parser.add_argument(
        "--size-offset", type=int, default=4,
        help="added to the base size before rendering (default: 4)",
    )
    parser.add_argument(
        "--height-offset", type=int, default=0,
        help="added to [symbol_coords] height (default: 0)",
    )
    parser.add_argument(
        "--width-correction", type=float, default=-1.0,
        help="[width_correction] value (default: -1.0)",
    )
    parser.add_argument(
        "--texture-size", type=int, default=None,
        help="force one texture size for all jobs (for experiments)",
    )
    parser.add_argument(
        "--cell-height", type=int, default=None,
        help="requested fixed cell height for the fontbm repack; it is "
             "expanded if the glyph ink needs more (default: base size)",
    )
    parser.add_argument(
        "--pack-spacing", type=int, default=1,
        help="pixels between repacked cells (default: 1)",
    )
    parser.add_argument(
        "--space-width", type=int, default=None,
        help="width of the blank space cell (default: fontbm's advance)",
    )
    parser.add_argument(
        "--no-repack", action="store_true",
        help="debug: keep fontbm's variable-height atlas (not X-Ray safe)",
    )
    parser.add_argument(
        "--no-calibrate", action="store_true",
        help="use the BMFont cell size as fontbm's font size (usually too big)",
    )
    parser.add_argument(
        "--only", action="append", default=[], metavar="FAMILY",
        help="limit to a family (arial, graff, letter); repeatable",
    )
    parser.add_argument(
        "--suffix", default="",
        help="append to output names, e.g. _west for localization font_prefix",
    )
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--out", default="dist")
    parser.add_argument("--zip-name", default="alternative_fonts.zip")
    parser.add_argument(
        "--dds-tool", default="auto",
        choices=("auto", "compressonator", "nvcompress", "pillow"),
    )
    parser.add_argument(
        "--dds-command", default=None,
        help="custom compressor command with {input} and {output} placeholders",
    )
    parser.add_argument(
        "--alpha8-dds", action="store_true",
        help="write uncompressed 8-bit alpha DDS (the format some X-Ray "
             "font addons use); no compressor needed",
    )
    parser.add_argument("--no-antialias", action="store_true")
    parser.add_argument(
        "--windows-charset", type=int, default=None,
        help="BMFont charSet override, e.g. 204 for Russian (cp1251)",
    )
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--list", action="store_true", help="list jobs and exit")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def selected_jobs(only: Sequence[str]) -> list[Job]:
    wanted: set[str] = set()
    for value in only:
        wanted.update(part.strip() for part in value.split(",") if part.strip())
    unknown = wanted - set(FAMILIES)
    if unknown:
        raise SystemExit(f"unknown family: {', '.join(sorted(unknown))}")
    return [job for job in all_jobs() if not wanted or job.family.key in wanted]


def resolve_fonts(args: argparse.Namespace, jobs: Sequence[Job]) -> dict[str, Path]:
    overrides: dict[str, Path] = {}
    for item in args.font:
        if "=" not in item:
            raise SystemExit(f"--font expects FAMILY=PATH, got {item!r}")
        key, path = item.split("=", 1)
        if key not in FAMILIES:
            raise SystemExit(f"unknown family in --font: {key}")
        overrides[key] = Path(path).expanduser()

    fonts: dict[str, Path] = {}
    for key in {job.family.key for job in jobs}:
        family = FAMILIES[key]
        path = overrides.get(key) or Path(args.font_dir) / family.default_font_file
        if args.backend == "fontbm" or key in overrides:
            if not path.is_file():
                raise SystemExit(
                    f"font file for {key!r} not found: {path}\n"
                    f"Put {family.default_font_file} in {args.font_dir}/ or pass "
                    f"--font {key}=/path/to/font.ttf"
                )
        fonts[key] = path
    return fonts


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.backend == "bmfont" and args.alpha8_dds:
        raise SystemExit(
            "--alpha8-dds is only supported with --backend fontbm; BMFont "
            "writes its own DDS"
        )

    jobs = [
        replace(job, suffix=args.suffix) for job in selected_jobs(args.only)
    ]
    if args.list:
        for job in jobs:
            print(
                f"{job.stem:32s} base={job.base_size:2d} "
                f"font={job.font_size(args.size_offset):2d} "
                f"texture={job.texture_size(args.size_offset)}"
            )
        return 0
    if not jobs:
        raise SystemExit("no jobs selected")

    fonts = resolve_fonts(args, jobs)

    # BMFont's charSet=0 means "the machine's ANSI codepage"; for a CP1251
    # package force Cyrillic so the build does not depend on the Windows locale.
    windows_charset = args.windows_charset
    if windows_charset is None and args.codepage == "cp1251":
        windows_charset = 204  # RUSSIAN_CHARSET

    if (
        args.backend == "fontbm"
        and not args.dry_run
        and not args.dds_command
        and not args.alpha8_dds
    ):
        dds_tool = detect_dds_tool(args.dds_tool)
    else:
        dds_tool = args.dds_tool

    work_dir_arg = args.work_dir
    if work_dir_arg:
        work_dir = Path(work_dir_arg)
        work_dir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    elif args.keep_work:
        work_dir = Path("build")
        work_dir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="makefont-"))
        cleanup = True

    chars = fontbm_chars(args.codepage)
    _, uni_to_byte = byte_unicode_map(args.codepage)
    rendered: list[tuple[Path, Path]] = []
    calibration: dict[tuple[str, int], tuple[int, int]] = {}

    try:
        for index, job in enumerate(jobs, 1):
            target_cell = args.cell_height or (
                job.font_size(args.size_offset) + args.height_offset
            )
            if target_cell < 1:
                raise SystemExit(
                    f"{job.stem}: invalid cell height {target_cell}"
                )
            render_size = target_cell
            texture = args.texture_size or job.texture_size(args.size_offset)
            prefix = f"[{index}/{len(jobs)}] {job.stem}"

            if args.dry_run:
                print(
                    f"{prefix}: cell={target_cell} texture={texture} "
                    f"font={fonts.get(job.family.key, '-')}"
                )
                continue

            print(f"{prefix}: cell={target_cell} texture={texture}")
            if args.backend == "fontbm":
                if not args.no_repack and not args.no_calibrate:
                    key = (job.family.key, target_cell)
                    if key not in calibration:
                        calibration[key] = calibrate_fontbm_size(
                            font_path=fonts[job.family.key],
                            target_cell=target_cell,
                            work_dir=work_dir,
                            fontbm=args.fontbm,
                            verbose=args.verbose,
                        )
                    render_size, measured = calibration[key]
                    if measured != target_cell:
                        print(
                            f"  fontbm size {render_size}px "
                            f"(lineHeight {measured}, target {target_cell})"
                        )
                result = render_fontbm(
                    job=job,
                    font_path=fonts[job.family.key],
                    font_size=render_size,
                    texture_size=texture,
                    chars=chars,
                    work_dir=work_dir,
                    fontbm=args.fontbm,
                    no_antialias=args.no_antialias,
                    verbose=args.verbose,
                )
            else:
                result = render_bmfont(
                    job=job,
                    font_size=target_cell,
                    texture_size=texture,
                    work_dir=work_dir,
                    template_path=Path(args.template),
                    bmfont=args.bmfont,
                    windows_charset=windows_charset,
                    font_path=fonts.get(job.family.key),
                    verbose=args.verbose,
                )

            fnt = parse_fnt(result.fnt)
            if fnt.pages != 1:
                raise RuntimeError(
                    f"{job.stem}: generated {fnt.pages} pages; the X-Ray .ini "
                    "format only supports one page"
                )
            if (fnt.width, fnt.height) != (texture, texture):
                raise RuntimeError(
                    f"{job.stem}: texture is {fnt.width}x{fnt.height}, "
                    f"expected {texture}x{texture}"
                )

            glyphs, unknown = map_glyphs_to_bytes(fnt, args.codepage)
            if unknown:
                print(
                    f"  warning: {len(unknown)} glyphs outside {args.codepage} "
                    "(ignored)",
                    file=sys.stderr,
                )
            if fnt.unicode:
                expected = set(uni_to_byte)
                missing = sorted(expected - set(fnt.glyphs))
            else:
                expected = set(uni_to_byte.values())
                missing = sorted(expected - set(fnt.glyphs))
            if missing:
                print(
                    f"  warning: font is missing {len(missing)} requested "
                    "characters (they will be blank)",
                    file=sys.stderr,
                )

            height = target_cell
            if args.backend == "fontbm":
                if not args.no_repack:
                    natural = fixed_cell_height(fnt, glyphs, render_size)
                    cell = target_cell
                    if natural > cell:
                        print(
                            f"  warning: glyph ink needs {natural}px, cell is "
                            f"{cell}px; expanding the cell",
                            file=sys.stderr,
                        )
                        cell = natural
                    packed_png = work_dir / f"{job.stem}_packed.png"
                    glyphs, cell, clipped = repack_fixed_height(
                        fnt=fnt,
                        glyphs=glyphs,
                        source_png=result.texture,
                        output_png=packed_png,
                        texture_size=texture,
                        font_size=render_size,
                        cell_height=cell,
                        spacing=args.pack_spacing,
                        space_width=args.space_width,
                    )
                    height = cell
                    result.texture = packed_png
                    if clipped:
                        print(
                            f"  warning: clipped {clipped} glyph(s); increase "
                            "--cell-height",
                            file=sys.stderr,
                        )
                    if cell != target_cell:
                        print(
                            f"  repacked into {cell}px cells "
                            f"(target {target_cell})"
                        )
                else:
                    print(
                        "  warning: --no-repack keeps fontbm's variable-height "
                        "cells; X-Ray will sample them incorrectly",
                        file=sys.stderr,
                    )

                dds = work_dir / f"{job.stem}.dds"
                convert_to_dds(
                    result.texture, dds, dds_tool, args.dds_command,
                    args.verbose, alpha8=args.alpha8_dds,
                )
                result.dds = dds
            else:
                heights = {
                    glyph.height for glyph in glyphs.values()
                    if glyph.width > 0 and glyph.height > 0
                }
                if len(heights) > 1:
                    print(
                        "  warning: glyph cell heights vary; set forceZero=1 "
                        "in template_conf.bmfc",
                        file=sys.stderr,
                    )
                if abs(fnt.size) != target_cell:
                    print(
                        f"  note: BMFont size is {abs(fnt.size)}, expected "
                        f"{target_cell}",
                        file=sys.stderr,
                    )

            if result.dds is None:
                raise RuntimeError(f"{job.stem}: no DDS texture produced")

            ini = work_dir / f"{job.stem}.ini"
            ini.write_text(
                build_ini(glyphs, height, args.width_correction),
                encoding="ascii",
            )

            fourcc, width, dheight, _ = dds_info(result.dds)
            print(
                f"  {result.dds.name}: {width}x{dheight} "
                f"{fourcc or 'uncompressed'}"
            )
            rendered.append((result.dds, ini))

        if args.dry_run:
            return 0

        archive = package_outputs(rendered, Path(args.out), args.zip_name)
        print(f"\nGenerated {len(rendered)} font(s)")
        print(f"  textures: {Path(args.out) / 'gamedata/textures/ui'}")
        print(f"  archive:  {archive}")
        return 0
    finally:
        if cleanup:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
