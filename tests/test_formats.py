#!/usr/bin/env python3
"""Unit tests for the format, codepage and repack code."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from makefont import (  # noqa: E402
    FAMILIES,
    Glyph,
    Job,
    build_ini,
    byte_unicode_map,
    dds_info,
    fixed_cell_height,
    fontbm_chars,
    format_ranges,
    load_dds_alpha,
    map_glyphs_to_bytes,
    parse_fnt,
    parse_ini,
    repack_fixed_height,
)

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:  # pragma: no cover
    HAVE_PIL = False


SAMPLE_FNT = """<?xml version="1.0"?>
<font>
  <info face="Test" size="-20" bold="0" italic="0" charset="" unicode="1"
        stretchH="100" smooth="0" aa="1" padding="0,0,0,0" spacing="0,0"
        outline="0"/>
  <common lineHeight="23" base="19" scaleW="256" scaleH="256" pages="1"
          packed="0" alphaChnl="0" redChnl="4" greenChnl="4" blueChnl="4"/>
  <pages><page id="0" file="test.png"/></pages>
  <chars count="2">
    <char id="1040" x="1" y="2" width="10" height="14" xoffset="0"
          yoffset="5" xadvance="11" page="0" chnl="15"/>
    <char id="32" x="0" y="0" width="0" height="0" xoffset="0"
          yoffset="0" xadvance="6" page="0" chnl="15"/>
  </chars>
</font>
"""


class CharsetTests(unittest.TestCase):
    def test_cp1251_roundtrip(self):
        byte_to_uni, uni_to_byte = byte_unicode_map("cp1251")
        self.assertEqual(byte_to_uni[192], 0x0410)  # А
        self.assertEqual(byte_to_uni[255], 0x044F)  # я
        self.assertEqual(uni_to_byte[0x0410], 192)
        self.assertNotIn(0x98, byte_to_uni)  # undefined in cp1251

    def test_cp1252(self):
        byte_to_uni, _ = byte_unicode_map("cp1252")
        self.assertEqual(byte_to_uni[0x80], 0x20AC)  # €
        for undefined in (0x81, 0x8D, 0x8F, 0x90, 0x9D):
            self.assertNotIn(undefined, byte_to_uni)

    def test_fontbm_chars_are_unicode(self):
        chars = fontbm_chars("cp1251")
        self.assertIn("1040-1103", chars)
        self.assertIn("32-126", chars)
        self.assertNotIn("192", chars.split(","))  # not a Unicode codepoint

    def test_format_ranges(self):
        self.assertEqual(format_ranges([32, 33, 34, 40, 41]), "32-34,40-41")
        self.assertEqual(format_ranges([]), "")


class FntTests(unittest.TestCase):
    def test_parse_and_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.fnt"
            path.write_text(SAMPLE_FNT, encoding="utf-8")
            fnt = parse_fnt(path)
            self.assertEqual(fnt.size, -20)
            self.assertTrue(fnt.unicode)
            self.assertEqual(fnt.line_height, 23)
            self.assertEqual(fnt.pages, 1)
            self.assertEqual(fnt.page_files, ["test.png"])
            self.assertEqual(fnt.glyphs[1040].width, 10)

            glyphs, unknown = map_glyphs_to_bytes(fnt, "cp1251")
            self.assertEqual(unknown, [])
            self.assertEqual(glyphs[192].width, 10)
            self.assertNotIn(1040, glyphs)

    def test_fixed_cell_height(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.fnt"
            path.write_text(SAMPLE_FNT, encoding="utf-8")
            fnt = parse_fnt(path)
            glyphs, _ = map_glyphs_to_bytes(fnt, "cp1251")
            self.assertEqual(fixed_cell_height(fnt, glyphs, 20), 23)


class IniTests(unittest.TestCase):
    def test_build_ini(self):
        glyph = Glyph(id=65, x=1, y=2, width=10, height=12)
        text = build_ini({65: glyph}, height=20, width_correction=-1.0)
        self.assertIn("[width_correction]\nvalue = -1.0", text)
        self.assertIn("[symbol_coords]\nheight = 20", text)
        self.assertIn("000 = 0, 0, 0, 0", text)
        self.assertIn("065 = 1, 2, 11, 14", text)
        self.assertIn("255 = 0, 0, 0, 0", text)
        for line in text.splitlines():
            if line[:1].isdigit():
                self.assertRegex(line, r"^\d{3} = ")

    def test_parse_ini_roundtrip(self):
        glyph = Glyph(id=65, x=1, y=2, width=10, height=12)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.ini"
            path.write_text(
                build_ini({65: glyph}, height=20, width_correction=-1.0),
                encoding="ascii",
            )
            ini = parse_ini(path)
            self.assertEqual(ini.height, 20.0)
            self.assertEqual(ini.width_correction, -1.0)
            self.assertEqual(ini.rows[65], (1, 2, 11, 14))
            self.assertEqual(ini.rows[0], (0, 0, 0, 0))


class RepackTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_PIL, "Pillow is required")
    def test_repack_into_fixed_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
            source.paste((255, 255, 255, 255), (0, 0, 4, 4))
            source.paste((255, 255, 255, 255), (8, 0, 11, 3))
            png = tmp_path / "in.png"
            source.save(png)

            fnt_path = tmp_path / "test.fnt"
            fnt_path.write_text(SAMPLE_FNT, encoding="utf-8")
            fnt = parse_fnt(fnt_path)
            glyphs = {
                65: Glyph(id=65, x=0, y=0, width=4, height=4,
                          yoffset=1, xadvance=5),
                66: Glyph(id=66, x=8, y=0, width=3, height=3,
                          yoffset=2, xadvance=4),
                32: Glyph(id=32, x=0, y=0, width=0, height=0,
                          yoffset=0, xadvance=3),
            }
            out = tmp_path / "out.png"
            new_glyphs, cell, clipped = repack_fixed_height(
                fnt=fnt,
                glyphs=glyphs,
                source_png=png,
                output_png=out,
                texture_size=32,
                font_size=5,
                cell_height=5,
                spacing=1,
                space_width=None,
            )

            self.assertEqual(cell, 5)
            self.assertEqual(clipped, 0)
            self.assertEqual(new_glyphs[32].width, 3)
            self.assertEqual(new_glyphs[65].height, 5)
            self.assertEqual(new_glyphs[65].x, 4)
            self.assertEqual(new_glyphs[65].y, 0)
            self.assertEqual(new_glyphs[66].x, 9)
            self.assertEqual(new_glyphs[66].y, 0)
            self.assertTrue(out.is_file())

            atlas = Image.open(out).convert("RGBA")
            # A is shifted up by one (min yoffset) into its cell.
            self.assertEqual(atlas.getpixel((4, 0))[3], 255)
            self.assertEqual(atlas.getpixel((4, 4))[3], 0)

class DdsTests(unittest.TestCase):
    def test_8bit_alpha_dds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "font.dds"
            width = height = 2
            header = bytearray(128)
            header[0:4] = b"DDS "
            struct.pack_into("<I", header, 4, 124)
            struct.pack_into("<I", header, 8, 0x1007)
            struct.pack_into("<I", header, 12, height)
            struct.pack_into("<I", header, 16, width)
            struct.pack_into("<I", header, 20, width)
            struct.pack_into("<I", header, 76, 32)
            struct.pack_into("<I", header, 80, 0x2)  # DDPF_ALPHA
            struct.pack_into("<I", header, 88, 8)
            struct.pack_into("<I", header, 104, 0xFF)
            header[108:112] = struct.pack("<I", 0x1000)
            path.write_bytes(bytes(header) + bytes([0, 64, 128, 255]))

            fourcc, w, h, _ = dds_info(path)
            self.assertEqual((fourcc, w, h), ("", 2, 2))
            if HAVE_PIL:
                alpha = load_dds_alpha(path)
                self.assertEqual(alpha.size, (2, 2))
                self.assertEqual(alpha.getpixel((1, 1)), 255)


class JobTests(unittest.TestCase):
    def test_texture_tier_uses_base_size(self):
        job = Job(FAMILIES["graff"], "40_1024", 38)
        self.assertEqual(job.texture_size(4), 512)
        job = Job(FAMILIES["graff"], "40_1600", 58)
        self.assertEqual(job.texture_size(4), 1024)


if __name__ == "__main__":
    unittest.main()
