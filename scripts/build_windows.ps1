$ErrorActionPreference = "Stop"

python -m pip install --upgrade ".[desktop,windows-build]"

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

python -m PyInstaller --noconfirm --clean --onefile --name TestBox --collect-submodules testbox --collect-all openpyxl --collect-all docx --collect-all PIL @pluginHiddenImportArgs --add-data "plugins;plugins" testbox/cli.py
python -m PyInstaller --noconfirm --clean --onefile --windowed --name TestBox-GUI --collect-submodules testbox --collect-all PySide6 --collect-all openpyxl --collect-all docx --collect-all PIL @pluginHiddenImportArgs --add-data "plugins;plugins" testbox/gui.py
Write-Host "Windows packages created: dist\TestBox.exe and dist\TestBox-GUI.exe"
