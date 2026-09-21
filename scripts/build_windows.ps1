$ErrorActionPreference = "Stop"

# Always resolve paths from the repository, not from the caller's current
# directory. This keeps both local runs and GitHub Actions deterministic.
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

python -m pip install --upgrade ".[desktop,evidence,windows-build]"

$appIcon = Join-Path $repoRoot "testbox\assets\logo.ico"
$windowsDist = Join-Path $repoRoot "dist\windows"
$cliDist = Join-Path $windowsDist "cli"
$guiDist = Join-Path $windowsDist "gui"
$pluginsSource = Join-Path $repoRoot "plugins"
if (-not (Test-Path -LiteralPath $pluginsSource -PathType Container)) {
    throw "Bundled plugins directory was not found: $pluginsSource"
}
if (Test-Path -LiteralPath $windowsDist) {
    Remove-Item -LiteralPath $windowsDist -Recurse -Force
}
New-Item -ItemType Directory -Path $cliDist, $guiDist -Force | Out-Null

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

# Evidence Tool imports these packages from plugin source at runtime. Collect
# their submodules without using --collect-all, which also copies unnecessary
# package metadata and tests.
$evidenceImportArgs = @(
    "--collect-submodules", "openpyxl",
    "--collect-submodules", "docx",
    "--collect-submodules", "PIL"
)

# Both channels carry the Core and official plugins, but only the GUI channel
# includes Qt. Keeping outputs in separate roots is required: update manifests
# delete removed managed files and therefore cannot safely target one shared
# installation directory.
$coreImportArgs = @(
    "--collect-submodules", "testbox.core",
    "--hidden-import", "testbox.sdk"
)

# onedir avoids extracting the complete Python runtime on every launch.
# The CLI deliberately excludes PySide6. Its `gui` subcommand remains usable
# from source installations but gives frozen CLI users a clear instruction to
# install the dedicated GUI package.
& python -m PyInstaller --noconfirm --clean --onedir --name TestBox --icon $appIcon `
    --distpath $cliDist --workpath (Join-Path $repoRoot "build\pyinstaller-cli") --specpath (Join-Path $repoRoot "build\specs\cli") `
    @coreImportArgs @evidenceImportArgs --exclude-module PySide6 --exclude-module testbox.gui `
    @pluginHiddenImportArgs --add-data "$pluginsSource;plugins" (Join-Path $repoRoot "testbox\cli.py")
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    throw "PyInstaller CLI build failed with exit code $exitCode"
}

# Import only the Qt modules used by TestBox and its interactive Evidence Tool.
# Do not use --collect-all PySide6: it pulls unused WebEngine, QML, 3D,
# Designer, PDF, multimedia and development resources into the installer.
$guiQtImportArgs = @(
    "--hidden-import", "PySide6.QtCore",
    "--hidden-import", "PySide6.QtGui",
    "--hidden-import", "PySide6.QtWidgets",
    "--hidden-import", "PySide6.QtSvg"
)
& python -m PyInstaller --noconfirm --clean --onedir --windowed --name TestBox-GUI --icon $appIcon `
    --distpath $guiDist --workpath (Join-Path $repoRoot "build\pyinstaller-gui") --specpath (Join-Path $repoRoot "build\specs\gui") `
    @coreImportArgs @evidenceImportArgs @guiQtImportArgs `
    @pluginHiddenImportArgs --add-data "$pluginsSource;plugins" (Join-Path $repoRoot "testbox\gui.py")
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    throw "PyInstaller GUI build failed with exit code $exitCode"
}

# The updater is intentionally onefile: it runs only during an update and must
# be available as a small bootstrap outside the files it replaces. Its filename
# selects the matching CLI or GUI release manifest at runtime.
& python -m PyInstaller --noconfirm --clean --onefile --name TestBox-CLI-Updater --icon $appIcon `
    --distpath $cliDist --workpath (Join-Path $repoRoot "build\pyinstaller-cli-updater") --specpath (Join-Path $repoRoot "build\specs\cli-updater") `
    (Join-Path $repoRoot "scripts\testbox_updater.py")
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    throw "PyInstaller CLI updater build failed with exit code $exitCode"
}

& python -m PyInstaller --noconfirm --clean --onefile --name TestBox-GUI-Updater --icon $appIcon `
    --distpath $guiDist --workpath (Join-Path $repoRoot "build\pyinstaller-gui-updater") --specpath (Join-Path $repoRoot "build\specs\gui-updater") `
    (Join-Path $repoRoot "scripts\testbox_updater.py")
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    throw "PyInstaller GUI updater build failed with exit code $exitCode"
}

$expectedExecutables = @(
    (Join-Path $cliDist "TestBox\TestBox.exe"),
    (Join-Path $cliDist "TestBox-CLI-Updater.exe"),
    (Join-Path $guiDist "TestBox-GUI\TestBox-GUI.exe"),
    (Join-Path $guiDist "TestBox-GUI-Updater.exe")
)
foreach ($executable in $expectedExecutables) {
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        Write-Host "--- Windows build output ---"
        Get-ChildItem -Path $windowsDist -Recurse -File | Select-Object -ExpandProperty FullName
        throw "Expected executable was not created: $executable"
    }
}

Write-Host "Separate CLI and GUI Windows packages created under $windowsDist"
