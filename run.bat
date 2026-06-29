@echo off
REM ============================================================
REM  KAVACH allocation - one-click runner (Windows)
REM  Double-click this file, OR drag your filled chart .xlsx onto it.
REM  It installs what is needed, runs the allocation (computing the
REM  station/loco slots) and writes the output workbook + CSV.
REM  All bundled data is synthetic / illustrative.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python"

set "PY="
for %%V in (3.12 3.11 3.13) do (
  if not defined PY ( py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V" )
)
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY ( where py >nul 2>nul && set "PY=py" )
if not defined PY (
  echo Python was not found. Install Python 3.12 from https://www.python.org/downloads/
  echo and TICK "Add Python to PATH", then run this again.
  pause & exit /b 1
)

set "CHART=%~1"
if "%CHART%"=="" set "CHART=KAVACH_input_template.xlsx"
for %%F in ("%CHART%") do set "BASE=%%~nF"
if not exist "output" mkdir "output"
set "OUT=output\%BASE%_compliant.xlsx"

echo ============================================================
echo  KAVACH allocation - one-click run
echo  Python : %PY%
echo  Chart  : %CHART%
echo  Output : %OUT%
echo ============================================================
echo.
echo [1/2] Installing required packages (openpyxl, ortools)...
%PY% -m pip install -r frequency-timeslot-analysis\requirements.txt

echo.
echo [2/2] Running allocation (computes station + loco slots)...
echo.
%PY% frequency-timeslot-analysis\run_allocation.py "%CHART%" "%OUT%" --rf-range 15 --no-boundary
if errorlevel 1 (
  echo.
  echo The run reported a problem above. If it mentions OR-Tools or a crash,
  echo install the Microsoft Visual C++ Redistributable (x64) and retry.
  pause & exit /b 1
)

echo.
echo ============================================================
echo  Done. Outputs are in the "output" folder:
echo    %OUT%
echo    output\%BASE%_compliant.csv
echo  (the table above shows station slots, loco slots and the plan)
echo ============================================================
start "" "output"
echo.
pause
endlocal
