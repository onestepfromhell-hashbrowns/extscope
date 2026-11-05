@echo off
rem ExtScope launcher for Windows. Installed to <prefix>\bin\extscope.cmd by install.ps1.
rem Library is expected at ..\lib\extscope\src (installed) or ..\..\src (in-tree).
setlocal
set "HERE=%~dp0"
if exist "%HERE%..\lib\extscope\src\extscope" (
    set "EXTSCOPE_SRC=%HERE%..\lib\extscope\src"
) else (
    set "EXTSCOPE_SRC=%HERE%..\src"
)
if defined PYTHONPATH (set "PYTHONPATH=%EXTSCOPE_SRC%;%PYTHONPATH%") else (set "PYTHONPATH=%EXTSCOPE_SRC%")
where py >nul 2>nul && (py -3 -m extscope %* & exit /b %ERRORLEVEL%)
where python >nul 2>nul && (python -m extscope %* & exit /b %ERRORLEVEL%)
echo Error: Python ^>= 3.11 not found in PATH. 1>&2
exit /b 1
