@echo off
title Quant HPC OS — Instalacao de Dependencias
echo.
echo ============================================================
echo   QUANT HPC OS v1.0.0 — Instalacao de Dependencias
echo ============================================================
echo.

cd /d "%~dp0"

:: Verificar Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Python nao encontrado! Instale o Python 3.10+ e tente novamente.
    pause
    exit /b 1
)

python --version
echo.

:: Atualizar pip
echo [1/3] Atualizando pip...
python -m pip install --upgrade pip
echo.

:: Instalar dependencias principais
echo [2/3] Instalando dependencias do requirements.txt...
pip install -r requirements.txt
echo.

:: Instalar PyTorch com CUDA (RTX 5090 = CUDA 12.x)
echo [3/3] Instalando PyTorch com suporte CUDA 12...
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
echo.

echo ============================================================
echo   Instalacao concluida!
echo   Proximo passo: configure o arquivo .env com sua ANTHROPIC_API_KEY
echo   Depois execute: python main.py
echo ============================================================
echo.
pause
