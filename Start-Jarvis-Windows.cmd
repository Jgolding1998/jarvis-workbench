@echo off
rem Starts the portable Jarvis bundle with its own bundled Python. No separate Python install is needed.
rem It does not install anything, change policies, or ask for administrator rights.
setlocal
cd /d "%~dp0"
if not exist "%~dp0runtime\python.exe" goto broken
if not exist "%~dp0app\jarvis\launcher.py" goto broken
if not exist "%~dp0app\web\setup.html" goto broken
"%~dp0runtime\python.exe" -I -m jarvis.launcher %*
set "JARVIS_EXIT=%ERRORLEVEL%"
if not "%JARVIS_EXIT%"=="0" if not defined JARVIS_NO_PAUSE pause
exit /b %JARVIS_EXIT%

:broken
echo.
echo This Jarvis folder is incomplete. Extract the whole Jarvis-Windows.zip again,
echo then double-click Start-Jarvis-Windows.cmd inside the extracted folder.
echo Running it from inside the ZIP preview does not work.
echo.
echo Nothing was installed or changed.
if not defined JARVIS_NO_PAUSE pause
exit /b 1
