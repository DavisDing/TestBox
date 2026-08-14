$ErrorActionPreference = "Stop"

python -m pip install --upgrade "pyinstaller>=6.0"
python -m PyInstaller --noconfirm --clean --onefile --name TestBox --collect-submodules testbox --add-data "plugins;plugins" testbox/cli.py
Write-Host "Windows package created: dist\TestBox.exe"
