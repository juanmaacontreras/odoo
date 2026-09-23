@echo off
REM Equipment intake - starts the local server and opens the browser.
setlocal EnableDelayedExpansion
pushd "%~dp0" || (echo Cannot open the folder "%~dp0". & pause & exit /b 1)

REM Find Python 3: py launcher, python on PATH, then the usual install folders.
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY if exist "%SystemRoot%\py.exe" set "PY="%SystemRoot%\py.exe" -3"
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PY="%%D\python.exe""
if not defined PY for /d %%D in ("%ProgramFiles%\Python3*") do if exist "%%D\python.exe" set "PY="%%D\python.exe""
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" -3"
if not defined PY (
  echo Python 3 was not found from this window.
  echo In Git Bash run:  type -a py python
  echo and send the output.
  pause
  exit /b 1
)
echo Using Python: !PY!
!PY! --version

!PY! -c "import xlrd" >nul 2>&1 || !PY! -m pip install --user -r requirements.txt
if not exist config.json (
  echo config.json not found. Copy config.example.json to config.json and fill in username and api_key.
  pause
  exit /b 1
)
!PY! server.py
pause
