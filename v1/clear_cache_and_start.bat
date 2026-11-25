@echo off
echo Limpando __pycache__...
for /d /r "%~dp0" %%d in (__pycache__) do (
    if exist "%%d" (
        rd /s /q "%%d"
        echo Removido: %%d
    )
)
echo.
echo Cache limpo! Iniciando sistema...
echo.
cd /d "%~dp0"
python -B main.py
pause
