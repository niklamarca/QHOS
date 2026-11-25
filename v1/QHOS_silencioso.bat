@echo off
title QHOS (silencioso)
cd /d "%~dp0"
set QHOS_HIDE=1
start "" pythonw desktop_app.py
exit
