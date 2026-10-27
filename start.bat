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

rem Pakete nur installieren, wenn sich requirements.txt geaendert hat (spart Zeit und Internet beim Start)
set "REQ_HASH="
for /f "delims=" %%h in ('certutil -hashfile requirements.txt SHA256 ^| findstr /v ":"') do if not defined REQ_HASH set "REQ_HASH=%%h"
set "OLD_HASH="
if exist .venv\requirements.sha256 set /p OLD_HASH=<.venv\requirements.sha256
if not "%REQ_HASH%"=="%OLD_HASH%" (
  echo Installiere Pakete...
  python -m pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt && (echo %REQ_HASH%)>.venv\requirements.sha256
)

python -m app %*
if "%~1"=="" pause
