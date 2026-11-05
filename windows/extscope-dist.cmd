@echo off
rem extscope-dist launcher for Windows. Installed to <prefix>\bin\extscope-dist.cmd by install.ps1.
setlocal
set "HERE=%~dp0"
if exist "%HERE%..\lib\extscope-dist\src\extscope_dist" (set "DSRC=%HERE%..\lib\extscope-dist\src") else (set "DSRC=%HERE%..\extscope-dist\src")
set "MSRC=%HERE%..\lib\extscope-modules\src"
if not exist "%MSRC%" set "MSRC=%HERE%..\extscope-modules\src"
if defined PYTHONPATH (set "PYTHONPATH=%DSRC%;%MSRC%;%PYTHONPATH%") else (set "PYTHONPATH=%DSRC%;%MSRC%")
where py >nul 2>nul && (py -3 -m extscope_dist %* & exit /b %ERRORLEVEL%)
where python >nul 2>nul && (python -m extscope_dist %* & exit /b %ERRORLEVEL%)
echo Error: Python ^>= 3.11 not found in PATH. 1>&2
exit /b 1
