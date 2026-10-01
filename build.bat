@echo off
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name UmaFanCounter app.py
