@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  python -m venv .venv
) else (
  py -3 -m venv .venv
)
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -e ".[windows,desktop]"
if errorlevel 1 goto failed
echo SimLoom AI desktop and Windows worker dependencies are ready.
echo Double-click "Start SimLoom AI.cmd" for the local workbench.
echo STEP solids are optional: .venv\Scripts\python.exe -m pip install -e ".[brep]"
pause
exit /b 0
:failed
echo Installation failed. Check Python 3.10+ and your network connection.
pause
exit /b 1
