$7zipPath = "$env:ProgramFiles\7-Zip\7z.exe"
$nvttPath = "$env:ProgramFiles\NVIDIA Corporation\NVIDIA Texture Tools\nvtt_export.exe"
Set-Alias Start-SevenZip $7zipPath
Set-Alias Start-NVTT $nvttPath


python ./parse.py

$files = @(Get-ChildItem -Path .\*.dds)
foreach ($file in $files) {
    Start-NVTT $file -f 18 -o $file
}


New-Item -Path ".\gamedata\textures\ui" -ItemType Directory -Force
Get-ChildItem -Path "." -Include ("*.dds", "*.ini") -Recurse | Move-Item -Destination ".\gamedata\textures\ui\"

Start-SevenZip a opensans.zip .\gamedata\
