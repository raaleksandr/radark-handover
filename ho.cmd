@echo off
rem Launcher for ho.py on Windows: prefers the py launcher, falls back to python.
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%~dp0ho.py" %*
) else (
  python "%~dp0ho.py" %*
)
exit /b %ERRORLEVEL%
