@echo off
rem ---------------------------------------------------------------
rem  Gomoku server installer and launcher (Windows / cmd)
rem
rem    install.bat                          GUI mode
rem    install.bat --no-gui                 headless mode
rem    install.bat --no-gui --board-size 19 headless, 19 x 19
rem
rem  run.bat creates .venv, installs requirements.txt when needed,
rem  and then starts server.py. All arguments are forwarded unchanged.
rem ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

call "%~dp0run.bat" %*
exit /b %errorlevel%
