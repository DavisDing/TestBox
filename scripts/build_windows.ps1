$ErrorActionPreference = "Stop"

python -m pip install --upgrade ".[desktop,windows-build]"
python -m PyInstaller --noconfirm --clean --onefile --name TestBox --collect-submodules testbox --collect-all openpyxl --collect-all docx --collect-all PIL --add-data "plugins;plugins" testbox/cli.py
python -m PyInstaller --noconfirm --clean --onefile --windowed --name TestBox-GUI --collect-submodules testbox --collect-all PySide6 --collect-all openpyxl --collect-all docx --collect-all PIL --add-data "plugins;plugins" testbox/gui.py
Write-Host "Windows packages created: dist\TestBox.exe and dist\TestBox-GUI.exe"
