@echo off
cd /d "%~dp0"
start "" "%~dp0.runtime\Scripts\pythonw.exe" -m seatwatch.app
