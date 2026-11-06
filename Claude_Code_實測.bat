@echo off
setlocal
cd /d "%~dp0"

rem One-click launcher: Claude Code -> local PII proxy -> CGU upstream.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\run_claude_demo.ps1" %*

if errorlevel 1 (
    echo.
    echo [ERROR] Keep the error above. Never include .env or the API key in screenshots.
    pause
    exit /b 1
)
