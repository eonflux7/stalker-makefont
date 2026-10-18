# Fonts

Put the source TTF/OTF files here, or pass explicit paths with
`--font <family>=<path>`.

Default names:

| family | default file | used for |
|---|---|---|
| `arial` | `OpenSansCondensed-Regular.ttf` | `ui_font_arial_*` |
| `letter` | `OpenSansCondensed-Regular.ttf` | `ui_font_letter_*` |
| `graff` | `ElegantTypewriter.ttf` | `ui_font_graff_*` |

The names are only defaults; the important thing is that the file is the
font you actually want in-game.  Use a licensed copy.  Open Sans Condensed is
available under the Apache License 2.0; check the license of any other font
you use.

Example:

```bash
python3 makefont.py \
  --font arial=/path/to/OpenSansCondensed-Regular.ttf \
  --font letter=/path/to/OpenSansCondensed-Regular.ttf \
  --font graff=/path/to/ElegantTypewriter.ttf
```
