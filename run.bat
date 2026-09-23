@echo off
REM Equipment intake - starts the local server and opens the browser.
cd /d "%~dp0"
set "PY=python"
where py >/dev/null 2>/dev/null && set "PY=py -3"
%PY% -c "import xlrd" 2>/dev/null || %PY% -m pip install --user -r requirements.txt
if not exist config.json (
  echo config.json not found. Copy config.example.json to config.json and fill in username and api_key.
  pause
  exit /b 1
)
%PY% server.py
pause
