@echo off
rem extscope-container launcher for Windows. Installed to <prefix>\bin\extscope-container.cmd by install.ps1.
setlocal
set "HERE=%~dp0"
if exist "%HERE%..\lib\extscope-container\src\extscope_container" (set "SRC=%HERE%..\lib\extscope-container\src") else (set "SRC=%HERE%..\extscope-container\src")
if defined PYTHONPATH (set "PYTHONPATH=%SRC%;%PYTHONPATH%") else (set "PYTHONPATH=%SRC%")
where py >nul 2>nul && (py -3 -m extscope_container %* & exit /b %ERRORLEVEL%)
where python >nul 2>nul && (python -m extscope_container %* & exit /b %ERRORLEVEL%)
echo Error: Python ^>= 3.11 not found in PATH. 1>&2
exit /b 1
