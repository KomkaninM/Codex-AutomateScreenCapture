@echo off
rem Launch the PySide6 control panel through the existing dependency bootstrap.
call "%~dp0start_bot.bat" --gui
exit /b %ERRORLEVEL%
