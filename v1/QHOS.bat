@echo off
title QHOS LAUNCHER
cd /d "%~dp0"
echo ============================================
echo   QHOS TERMINAL - iniciando...
echo   (esta janela mostra erros do launcher)
echo   (o servidor abre em janela propria)
echo ============================================
echo.
python desktop_app.py
echo.
echo [QHOS] launcher encerrado. Pressione uma tecla para fechar.
pause >nul
