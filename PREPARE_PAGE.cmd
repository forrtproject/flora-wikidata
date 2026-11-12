@echo off
setlocal
pushd "%~dp0"
title FLoRA - Prepare next page (read-only)
set "FLORA_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%FLORA_PYTHON%" goto fallback
"%FLORA_PYTHON%" -c "import requests" >nul 2>&1
if not errorlevel 1 goto run
:fallback
set "FLORA_PYTHON=%~dp0..\..\work\.venv\Scripts\python.exe"
if not exist "%FLORA_PYTHON%" goto missing
:run
set "FLORA_COUNT=%~1"
if "%FLORA_COUNT%"=="" set "FLORA_COUNT=25"
"%FLORA_PYTHON%" -X utf8 "%~dp0flora_pages.py" prepare --count %FLORA_COUNT%
goto done
:missing
echo Python environment missing. See CREATE_FIRST_TEN.md for setup.
:done
echo.
pause
popd
endlocal
