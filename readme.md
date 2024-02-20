# stalker-makefont

A collection of scripts to automate making bitmap fonts compatible with S.T.A.L.K.E.R. games. Currently only tested with S.T.A.L.K.E.R. Anomaly.

## Instructions

1. Install [NVTTE](https://developer.nvidia.com/nvidia-texture-tools-exporter), [BMFont](https://www.angelcode.com/products/bmfont/), [7-Zip](https://www.7-zip.org/), [Python](https://www.python.org/) and [xmltodict](https://pypi.org/project/xmltodict/) library.
2. Export fonts with BMFont in the folder with the scripts. Make sure to tick `Force offsets to zero` in export settings. Use the following syntax for naming:
   - `sans16` - smaller version of in-game font
   - `sans18` - bigger version of in-game font
   - `graff19` - smaller version of main menu font
   - `graff22` - bigger version of main menu font
3. Launch run.ps1 PowerShell script.

## Additional information

Stalker font maps should be compressed with DXT5 (BC3) and DDS format. That's why we need NVTTE to compress BMFont maps into the right format.

The ini file is a custom config format for the maps. It has mostly the same Unicode decimal numbers for each glyph, except some empty glyphs are replaced, for example glyph with a decimal 149 in the Stalker map is used for a bullet point (in Unicode it is decimal 8226).

#### todo:

- refactor and make scripts configurable via json
- fix to include bullet points in the map
- add bmfont to the script
