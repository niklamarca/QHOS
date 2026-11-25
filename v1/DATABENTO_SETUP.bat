@echo off
cd /d "%~dp0"
title Quant HPC OS - Databento Setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_databento.ps1"
if errorlevel 1 (
  echo.
  echo CONFIGURACAO NAO CONCLUIDA. Leia o erro acima.
) else (
  echo.
  echo CONFIGURACAO CONCLUIDA COM SUCESSO.
)
pause
