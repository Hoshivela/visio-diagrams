@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -Action Install %*
set "setup_result=%errorlevel%"
if "%~1"=="" pause
exit /b %setup_result%
