@echo off
cd /d "%~dp0"
python backtrail.py
if errorlevel 1 pause
