@echo off
REM doc-agent: install missing packages (first run only), then open the renamer in your browser.
cd /d "%~dp0"
".venv\Scripts\python.exe" -c "import sys" 2>nul
if errorlevel 1 (
  echo Creating virtual environment...
  py -3.12 -m venv .venv || python -m venv .venv
)
".venv\Scripts\python.exe" -c "import rapidocr, rapidfuzz, fastapi, multipart, pymupdf, pydantic" 2>nul || (
  echo Installing packages - first run only, about 2 minutes...
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -W ignore -m docagent.web %*
pause
