# stalker-makefont

Build X-Ray / S.T.A.L.K.E.R. bitmap font packages (`.dds` + `.ini`) from
TTF/OTF files.  Targets the format used by Anomaly, GAMMA and Demonized's
modded exes (verified against their engine source and shipped font assets).

The default renderer is [fontbm](https://github.com/vladimirgamalyan/fontbm),
a cross-platform FreeType based BMFont replacement.  The original Windows
BMFont is still supported as a backend.

## Why this is not just "run fontbm"

X-Ray's `[symbol_coords]` format has an assumption that is easy to miss.  In
`GameFont.cpp` / `dxFontRender.cpp`:

```cpp
fHeight = ini->r_float("symbol_coords", "height");
TCMap[i].set(v.x, v.y, v[2] - v[0]);      // width = x2 - x
...
fTCHeight = fHeight / textureHeight;       // vertical span = height
v->set(..., tv + fTCHeight);               // y2 is never used
```

Every glyph is sampled as a **`height`-pixel-tall strip** starting at `y`.
`y2` is ignored.  BMFont gets this right because `forceZero=1` equalises all
cell heights.  fontbm packs tight, variable-height glyphs, so feeding its
atlas directly to X-Ray produces clipped and bleeding text.

This tool re-packs fontbm output into fixed-height cells, calibrates fontbm's
font size so the resulting line height matches BMFont's cell height, and then
writes the `.ini` from the new layout.

## Install

* Python 3.10+
* [fontbm](https://github.com/vladimirgamalyan/fontbm/releases) (Linux,
  macOS, Windows; MIT).  Put it anywhere and use `--fontbm PATH`, or install
  it on `PATH`.
* Pillow (needed for the repack, preview and compare tools):

```bash
pip install -r requirements.txt
```

* Put the source fonts in `fonts/` (see `fonts/README.md`) or pass them with
  `--font FAMILY=PATH`.

A DDS compressor is optional.  With no compressor the default is an
uncompressed RGBA DDS; `--alpha8-dds` writes the 8-bit alpha format used by
some X-Ray font addons.

## Usage

```bash
# all 30 original font variants
python3 makefont.py --font-dir fonts --out dist

# one family, custom font path
python3 makefont.py --only arial \
  --font arial=/path/to/OpenSansCondensed-Regular.ttf

# 8-bit alpha DDS, no compressor needed
python3 makefont.py --alpha8-dds

# BC3/DXT5 via Compressonator or nvcompress (if installed)
python3 makefont.py --dds-tool compressonator

# localization suffix (localization.ltx: font_prefix = _west)
python3 makefont.py --suffix _west

# original Windows tool (or via Wine)
python3 makefont.py --backend bmfont --bmfont "wine bmfont64.exe"
```

Output:

```
dist/
  alternative_fonts.zip
  gamedata/textures/ui/
    ui_font_arial_14_800.dds
    ui_font_arial_14_800.ini
    ...
```

Useful options:

| option | meaning |
|---|---|
| `--only arial,graff` | limit the families |
| `--codepage cp1251` | engine byte mapping (default cp1251; use cp1252 for Western) |
| `--size-offset N` | added to the base size (default 4, as in the original script) |
| `--cell-height N` | requested cell height (expanded if ink needs more) |
| `--space-width N` | width of the blank space cell (default: fontbm advance) |
| `--suffix STR` | appended to every output stem |
| `--keep-work` | keep the generated `.fnt`/PNG intermediates in `build/` |
| `--no-repack` | debug only: keep fontbm's variable-height atlas |
| `--no-calibrate` | debug only: use the cell size directly as fontbm size |
| `--list` / `--dry-run` | inspect the job table without rendering |

## Validating without the game

Two small tools are included, because the engine's fixed-height rule is easy
to get wrong and an Anomaly install is not required:

### Preview

`tools/preview_font.py` renders text exactly as `dxFontRender` does: it crops
a `height`-tall strip at each glyph and advances by `x2 - x`.

```bash
python3 tools/preview_font.py \
  dist/gamedata/textures/ui/ui_font_arial_14_800.ini \
  --text $'The quick brown fox\nПривет, Сталкер!' --scale 4
```

If cells are variable-height, this shows the same clipping/bleeding the game
would.  It also reads the 8-bit alpha DDS used by some font addons.

### Compare

`tools/compare_fonts.py` compares a new build against an existing BMFont
build.  It is layout independent: it compares `.fnt` metadata, glyph widths
and advances, `.ini` header/cell sizes, and the alpha of each glyph cropped
by its own coordinates.

```bash
python3 tools/compare_fonts.py old/ui dist/gamedata/textures/ui
```

Shipped packages contain only `.dds`/`.ini`, so `.fnt` is optional.  Add
`--fail-on-diff` to make metric differences above `--tol` fail the command.

## Format notes

* `.ini` keys are **engine bytes** (`000`–`255`), not Unicode codepoints.
  With a Unicode renderer the codepage mapping is applied automatically:
  byte 192 (А in CP1251) is generated from U+0410.
* `[width_correction]` is still written (as `-1.0`) for compatibility, but
  modern X-Ray/OpenXRay/modded-exes code has it commented out.  It has no
  effect there.
* `[symbol_coords] height` is the cell height and is used both for the
  vertical texture span and for text scale (`WidthScale()`).
* `y2` in each `.ini` row is ignored by the renderer; every cell must be
  exactly `height` tall.
* DDS: BC3/DXT5 is the traditional format.  Uncompressed A8R8G8B8 and 8-bit
  alpha DDS also load in the engines tested here; `--alpha8-dds` writes the
  latter (same as GAMMA's optional font addon).
* With `--backend bmfont` and CP1251, the tool sets `charSet=204`
  (Russian charset) so the result does not depend on the Windows locale.
  Override with `--windows-charset`.
* Anomaly/GAMMA pick atlases by screen height (`texture800`, `texture1024`,
  `texture1600`, `texture2160`) and can append `font_prefix` (`_west`,
  `_cent`, ...) to the filename.  Use `--suffix` to match your
  `localization.ltx`.

## Legacy script

`let_him_cook.py` and `template_conf.bmfc` are the original implementation.
They are kept for reference and for the `--backend bmfont` path.  The new
entry point is `makefont.py`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests cover the CP1251/CP1252 mapping, `.fnt`/`.ini` parsing, the
fixed-height repack, the 8-bit DDS reader and the texture-size tiers.
