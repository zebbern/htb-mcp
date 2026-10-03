@echo off
rem HTB Agent Toolkit launcher for Windows - runs htb.py with the available Python.
rem Usage: htb <command> [options]   (e.g. htb doctor)
where python >nul 2>nul
if %errorlevel%==0 (
    python "%~dp0htb.py" %*
    exit /b %errorlevel%
)
py "%~dp0htb.py" %*
exit /b %errorlevel%
