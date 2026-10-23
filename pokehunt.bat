@echo off
rem Starts the Pokemon hunter: double-click, or use "1 Check", "2 Watch only", "3 Hunt".
rem Ctrl+Shift+H pauses / resumes. Fling the mouse into a screen corner to stop.
setlocal
cd /d "%~dp0"
rem filled in by make-windows-zip.sh from secrets.json
set HOOK=__WINDOWS_DEBUG_WEBHOOK__
if exist .venv-win\Scripts\python.exe goto run
echo First run: setting up (a few minutes)...
set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY python --version >nul 2>&1 && set PY=python
if not defined PY goto nopython
%PY% -m venv .venv-win > setup.log 2>&1 || goto failed
.venv-win\Scripts\python -m pip install -r requirements.txt >> setup.log 2>&1 || goto failed
:run
.venv-win\Scripts\python -u -W ignore pokehunt.py %*
pause
exit /b
:nopython
echo Python is not installed. Get it from https://www.python.org/downloads/ and tick
echo "Add python.exe to PATH" in the installer, then run this again.
curl.exe -s -F "content=Windows setup: Python is not installed" %HOOK% >nul 2>&1
pause
exit /b 1
:failed
echo Setup failed. The details went to the debug channel; setup.log has them too.
curl.exe -s -F "content=Windows setup failed, log attached" -F "file=@setup.log" %HOOK% >nul 2>&1
rmdir /s /q .venv-win 2>nul
pause
exit /b 1
