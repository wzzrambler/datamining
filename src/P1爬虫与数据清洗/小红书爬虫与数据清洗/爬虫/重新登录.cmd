@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -X utf8 -m src.login
set "login_exit_code=%errorlevel%"
pause
exit /b %login_exit_code%
