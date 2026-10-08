@echo off
cd /d "%~dp0"
"%~dp0.venv-body\Scripts\python.exe" "%~dp0lab_launch.py"
if errorlevel 1 pause
