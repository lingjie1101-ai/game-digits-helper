$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    python -m pip install -r build-requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    python -m PyInstaller --noconfirm --clean --windowed --onedir --hidden-import pynput.mouse._win32 --hidden-import pynput.keyboard._win32 --name '游戏数字随手贴 V3.5' app.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    Copy-Item -LiteralPath third_party -Destination 'dist/游戏数字随手贴 V3.5' -Recurse -Force
    Write-Output 'Build complete: dist/游戏数字随手贴 V3.5/游戏数字随手贴 V3.5.exe'
} finally {
    Pop-Location
}
