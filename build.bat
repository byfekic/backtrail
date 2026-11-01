@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py) else (set PY=python)
%PY% -m pip install -r requirements.txt
%PY% -m PyInstaller --clean --noconfirm Backtrail.spec
if errorlevel 1 (echo BUILD FAILED&pause&exit /b 1)
if not exist dist\Backtrail\config.json copy /Y config.example.json dist\Backtrail\config.json >nul
echo.
echo COMPLETE: dist\Backtrail\Backtrail.exe   (distribute the whole dist\Backtrail folder)
pause
