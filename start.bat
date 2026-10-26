@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist .venv (
  echo Erstelle Python-Umgebung, das dauert beim ersten Mal ein paar Minuten...
  rem Ganz neue Python-Versionen haben oft noch keine fertigen Pakete, darum bewaehrte zuerst.
  py -3.13 -m venv .venv 2>nul || py -3.12 -m venv .venv 2>nul || py -3.14 -m venv .venv 2>nul || py -3 -m venv .venv 2>nul || python -m venv .venv
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
