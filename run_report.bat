@echo off
rem M7 Terminal daily report runner.
rem
rem   run_report.bat              interactive; prints to the console and waits
rem   run_report.bat --scheduled  Task Scheduler; logs to logs\ and exits
rem
rem The scheduler only sees the exit code: 0 ok, 2 Toss API error, 3 other.
setlocal

cd /d "%~dp0"

rem Redirected stdout follows the console code page (cp949 on Korean Windows),
rem so one Korean stock name garbles the whole log. Pin it to UTF-8.
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

if /i "%~1"=="--scheduled" goto :scheduled

python main.py
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" echo [!] FAILED with exit code %EXITCODE%.
pause
exit /b %EXITCODE%

:scheduled
if not exist "%~dp0logs" mkdir "%~dp0logs"

rem %DATE% is locale dependent and unusable in a file name. Pin the format.
set "NOW="
for /f "delims=" %%i in ('powershell -NoProfile -Command Get-Date -Format yyyy-MM-dd_HH-mm-ss') do set "NOW=%%i"
if not defined NOW set "NOW=unknown"
set "LOGFILE=%~dp0logs\report_%NOW:~0,10%.log"

echo.>> "%LOGFILE%"
echo ===== run %NOW% =====>> "%LOGFILE%"
python main.py >> "%LOGFILE%" 2>&1
set "EXITCODE=%ERRORLEVEL%"
echo ----- exit %EXITCODE% ----->> "%LOGFILE%"
exit /b %EXITCODE%
