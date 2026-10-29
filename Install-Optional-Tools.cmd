@echo off
setlocal
cd /d "%~dp0"
set "JARVIS_PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "JARVIS_PY=py -3"
if not defined JARVIS_PY (
  where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "JARVIS_PY=python"
)
if not defined JARVIS_PY goto nopython
%JARVIS_PY% -I -c "import os,sys; sys.path.append(os.path.abspath('.')); from jarvis.install_tools import main; raise SystemExit(main())"
set "JARVIS_EXIT=%ERRORLEVEL%"
if not defined JARVIS_NO_PAUSE pause
exit /b %JARVIS_EXIT%

:nopython
echo.
echo Jarvis optional tools need Python 3.11 or newer.
echo Download the official installer from https://www.python.org/downloads/
echo In the installer, tick "Add python.exe to PATH", then open this file again.
echo Nothing was installed or changed.
if not defined JARVIS_NO_PAUSE pause
exit /b 2
