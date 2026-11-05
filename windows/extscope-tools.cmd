@echo off
rem extscope-tools launcher for Windows. Installed to <prefix>\bin\extscope-tools.cmd by install.ps1.
setlocal
set "HERE=%~dp0"
if exist "%HERE%..\lib\extscope-tools\src\extscope_tools" (set "SRC=%HERE%..\lib\extscope-tools\src") else (set "SRC=%HERE%..\extscope-tools\src")
if defined PYTHONPATH (set "PYTHONPATH=%SRC%;%PYTHONPATH%") else (set "PYTHONPATH=%SRC%")
where py >nul 2>nul && (py -3 -m extscope_tools %* & exit /b %ERRORLEVEL%)
where python >nul 2>nul && (python -m extscope_tools %* & exit /b %ERRORLEVEL%)
echo Error: Python ^>= 3.11 not found in PATH. 1>&2
exit /b 1
