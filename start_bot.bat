@echo off
setlocal
title BMS Automation LINE Bot Launcher
pushd "%~dp0"
if errorlevel 1 goto folder_error

where py >nul 2>&1
if errorlevel 1 goto use_python
py -3.14 -c "import sys" >nul 2>&1
if not errorlevel 1 goto use_314
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 goto use_312
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 goto use_default

:use_python
where python >nul 2>&1
if errorlevel 1 goto missing_python
python "%~dp0launcher.py" %*
set "BOT_EXIT=%ERRORLEVEL%"
goto finished

:use_314
py -3.14 "%~dp0launcher.py" %*
set "BOT_EXIT=%ERRORLEVEL%"
goto finished

:use_312
py -3.12 "%~dp0launcher.py" %*
set "BOT_EXIT=%ERRORLEVEL%"
goto finished

:use_default
py -3 "%~dp0launcher.py" %*
set "BOT_EXIT=%ERRORLEVEL%"
goto finished

:missing_python
echo Python was not found. Install standard Python 3.14 for Windows x64.
echo Select "Add Python to PATH", then double-click this file again.
echo Download: https://www.python.org/downloads/windows/
set "BOT_EXIT=1"
goto finished

:folder_error
echo Cannot open the program folder. Extract the complete ZIP before launching.
pause
exit /b 1

:finished
if not "%BOT_EXIT%"=="0" echo Launcher stopped. Read the message above for the next step.
popd
pause
exit /b %BOT_EXIT%
