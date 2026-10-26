@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist .venv (
  echo Erstelle Python-Umgebung, das dauert beim ersten Mal ein paar Minuten...
  py -3 -m venv .venv || python -m venv .venv
  if errorlevel 1 (
    echo Python wurde nicht gefunden. Bitte Python von https://www.python.org installieren.
    pause
    exit /b 1
  )
)

call .venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
python -m app
pause
