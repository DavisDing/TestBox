$ErrorActionPreference = "Stop"

# Always resolve paths from the repository, not from the caller's current
# directory. This keeps both local runs and GitHub Actions deterministic.
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

python -m pip install --upgrade ".[desktop,windows-build]"

$windowsDist = Join-Path $repoRoot "dist\windows"
if (Test-Path -LiteralPath $windowsDist) {
    Remove-Item -LiteralPath $windowsDist -Recurse -Force
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

function Invoke-CheckedPyInstaller {
    param([string[]] $Arguments)
    & python -m PyInstaller @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
}

# onedir avoids extracting the complete Python/Qt runtime on every launch.
# CLI and GUI remain separate so the CLI does not carry the desktop stack.
Invoke-CheckedPyInstaller @(
    "--noconfirm", "--clean", "--onedir", "--name", "TestBox",
    "--distpath", $windowsDist, "--workpath", (Join-Path $repoRoot "build\pyinstaller-cli"), "--specpath", (Join-Path $repoRoot "build\specs\cli"),
    "--collect-submodules", "testbox", "--collect-all", "openpyxl", "--collect-all", "docx", "--collect-all", "PIL",
    $pluginHiddenImportArgs, "--add-data", "plugins;plugins", "testbox/cli.py"
)

Invoke-CheckedPyInstaller @(
    "--noconfirm", "--clean", "--onedir", "--windowed", "--name", "TestBox-GUI",
    "--distpath", $windowsDist, "--workpath", (Join-Path $repoRoot "build\pyinstaller-gui"), "--specpath", (Join-Path $repoRoot "build\specs\gui"),
    "--collect-submodules", "testbox", "--collect-all", "PySide6", "--collect-all", "openpyxl", "--collect-all", "docx", "--collect-all", "PIL",
    $pluginHiddenImportArgs, "--add-data", "plugins;plugins", "testbox/gui.py"
)

# The updater is intentionally onefile: it runs only during an update and must
# be available as a small bootstrap outside the files it replaces.
Invoke-CheckedPyInstaller @(
    "--noconfirm", "--clean", "--onefile", "--name", "TestBox-Updater",
    "--distpath", (Join-Path $repoRoot "dist\updater"), "--workpath", (Join-Path $repoRoot "build\pyinstaller-updater"), "--specpath", (Join-Path $repoRoot "build\specs\updater"),
    "scripts/testbox_updater.py"
)
Copy-Item (Join-Path $repoRoot "dist\updater\TestBox-Updater.exe") (Join-Path $windowsDist "TestBox-Updater.exe")

$expectedExecutables = @(
    (Join-Path $windowsDist "TestBox\TestBox.exe"),
    (Join-Path $windowsDist "TestBox-GUI\TestBox-GUI.exe"),
    (Join-Path $windowsDist "TestBox-Updater.exe")
)
foreach ($executable in $expectedExecutables) {
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        Write-Host "--- Windows build output ---"
        Get-ChildItem -Path $windowsDist -Recurse -File | Select-Object -ExpandProperty FullName
        throw "Expected executable was not created: $executable"
    }
}

Write-Host "Windows onedir packages created under $windowsDist"
