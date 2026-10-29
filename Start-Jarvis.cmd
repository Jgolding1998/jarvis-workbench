@echo off
rem Opens the Jarvis setup wizard in safe demo mode. Needs Python 3.11 or newer.
rem It does not install anything, change policies, or ask for administrator rights.
setlocal
cd /d "%~dp0"
set "JARVIS_PY="
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "JARVIS_PY=.venv\Scripts\python.exe"
)
if not defined JARVIS_PY (
where py >nul 2>nul && py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "JARVIS_PY=py -3"
)
if not defined JARVIS_PY (
  where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "JARVIS_PY=python"
)
if not defined JARVIS_PY goto nopython
%JARVIS_PY% -m jarvis.launcher %*
set "JARVIS_EXIT=%ERRORLEVEL%"
if not "%JARVIS_EXIT%"=="0" if not defined JARVIS_NO_PAUSE pause
exit /b %JARVIS_EXIT%

:nopython
echo.
echo Jarvis needs Python 3.11 or newer, and it was not found on this computer.
echo.
echo 1. Download the official installer: https://www.python.org/downloads/
echo 2. In the installer, tick "Add python.exe to PATH".
echo 3. Double-click Start-Jarvis.cmd again.
echo.
echo Nothing was installed or changed.
if not defined JARVIS_NO_PAUSE pause
exit /b 1
