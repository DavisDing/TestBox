$ErrorActionPreference = "Stop"

python -m pip install --upgrade ".[desktop,windows-build]"

$windowsDist = "dist\windows"
if (Test-Path $windowsDist) {
    Remove-Item $windowsDist -Recurse -Force
}
New-Item -ItemType Directory -Path $windowsDist -Force | Out-Null

# Plugins are loaded from source files at runtime, so PyInstaller cannot see
# their imports during static analysis. Keep the standard-library imports used
# by the bundled plugins explicit; otherwise the frozen Host can fail after
# packaging even though the source-based test suite passes.
$pluginHiddenImports = @(
    "csv",
    "hashlib",
    "io",
    "random",
    "uuid",
    "zipfile",
    "xml.etree.ElementTree",
    "xml.sax.saxutils"
)
$pluginHiddenImportArgs = @()
foreach ($module in $pluginHiddenImports) {
    $pluginHiddenImportArgs += @("--hidden-import", $module)
}

# onedir avoids extracting the complete Python/Qt runtime on every launch.
# CLI and GUI remain separate so the CLI does not carry the desktop stack.
python -m PyInstaller --noconfirm --clean --onedir --name TestBox `
    --distpath $windowsDist --workpath "build\pyinstaller-cli" --specpath "build\specs\cli" `
    --collect-submodules testbox --collect-all openpyxl --collect-all docx --collect-all PIL `
    @pluginHiddenImportArgs --add-data "plugins;plugins" testbox/cli.py

python -m PyInstaller --noconfirm --clean --onedir --windowed --name TestBox-GUI `
    --distpath $windowsDist --workpath "build\pyinstaller-gui" --specpath "build\specs\gui" `
    --collect-submodules testbox --collect-all PySide6 --collect-all openpyxl --collect-all docx --collect-all PIL `
    @pluginHiddenImportArgs --add-data "plugins;plugins" testbox/gui.py

# The updater is intentionally onefile: it runs only during an update and must
# be available as a small bootstrap outside the files it replaces.
python -m PyInstaller --noconfirm --clean --onefile --name TestBox-Updater `
    --distpath "dist\updater" --workpath "build\pyinstaller-updater" --specpath "build\specs\updater" `
    scripts/testbox_updater.py
Copy-Item "dist\updater\TestBox-Updater.exe" "$windowsDist\TestBox-Updater.exe"

Write-Host "Windows onedir packages created under $windowsDist"
