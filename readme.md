# stalker-makefont

A collection of scripts to automate making bitmap fonts compatible with S.T.A.L.K.E.R. games. Currently only tested with S.T.A.L.K.E.R. Anomaly.

## Instructions
Install [NVTTE](https://developer.nvidia.com/nvidia-texture-tools-exporter) and [BMFont](https://www.angelcode.com/products/bmfont/).

Export fonts with BMFont in the folder with the scripts. Make sure to tick `Force offsets to zero` in export settings. Use the following syntax for naming:
- `sans16` - Smaller version of in-game font
- `sans18` - Bigger version of in-game font
- `graff19` - Smaller version of main menu font
- `graff22` - Bigger version of main menu font

## Trivia
Stalker font maps should be compressed with DXT5 (BC3) and DDS format. That's why we need NVTTE to compress BMFont maps into the right format.

The ini file is a custom config format for the maps. It has mostly the same Unicode decimal numbers for each glyph, except some empty glyphs are used in the maps, for examply glyph with a decimal 149 in the S.T.A.L.K.E.R. map is for a bullet point (in Unicode it is decimal 8226).

## Todo
- Refactor and make scripts configurable via json
- Fix to include bullet points in the map
