@echo off
setlocal
pushd "%~dp0"
title FLoRA - Read-only Wikidata check
echo.
echo FLoRA: checking the first ten CSV rows
echo Account name for public information: Hb19988
echo This test does NOT log in, create items, or change Wikidata.
echo.
set "FLORA_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%FLORA_PYTHON%" goto fallback
"%FLORA_PYTHON%" -c "import requests" >nul 2>&1
if not errorlevel 1 goto run
:fallback
set "FLORA_PYTHON=%~dp0..\..\work\.venv\Scripts\python.exe"
if not exist "%FLORA_PYTHON%" goto missing
:run
"%FLORA_PYTHON%" -X utf8 "%~dp0check_first_ten.py" --csv "C:\Users\hamid\Documents\GitHub\flora-validation\output\flora.csv" --out "%~dp0online-check.json" --username Hb19988
if errorlevel 1 goto failed
echo.
echo FINISHED. Open online-check.json.md in VS Code for the results.
echo No Wikidata edits were made.
goto done
:missing
echo Python environment was not found. Ask for help setting it up.
goto done
:failed
echo.
echo The check stopped. Copy the error above into the chat.
echo This read-only test cannot publish edits.
:done
echo.
pause
popd
endlocal
