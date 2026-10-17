@echo off
rem Double-click: start the patched VCMI with the Claude bridge (see vcmi_play.py).
python -X utf8 "%~dp0vcmi_play.py"
if errorlevel 1 pause
