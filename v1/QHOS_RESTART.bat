@echo off
setlocal
title QHOS RESTART
cd /d "%~dp0"
echo ============================================
echo   QHOS - reiniciando o servidor (codigo novo)
echo ============================================
echo.
echo [1/4] encerrando TUDO (servidor da porta 8000 + tarefa agendada)...
rem A tarefa QHOS-Server sobe um servidor no boot do Windows. Se ela ja subiu
rem um e voce roda este .bat, o kill_server matava o que escutava a porta mas a
rem tarefa podia ter deixado outro processo segurando o banco -> DuckDB travado
rem -> sessao inteira em RAM sem persistir nada. Aconteceu em 14/08/2026.
rem Agora a tarefa e encerrada explicitamente ANTES do kill.
schtasks /End /TN "QHOS-Server" >nul 2>&1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0kill_server.ps1"

rem O kill_server ja detectava o banco travado, mas so IMPRIMIA o aviso e o .bat
rem seguia subindo assim mesmo -- exatamente o modo de falha silenciosa que ele
rem existe para evitar. Agora ele devolve codigo de saida e aqui a gente ABORTA.
if errorlevel 1 (
  echo.
  echo   ###############################################################
  echo   #  ABORTADO: o banco continua travado.                        #
  echo   #  Subir agora rodaria a sessao inteira em RAM, sem gravar    #
  echo   #  nada -- pior que nao subir. Feche as janelas do QHOS ou    #
  echo   #  reinicie o Windows e rode este .bat de novo.               #
  echo   ###############################################################
  echo.
  pause >nul
  exit /b 1
)

echo.
echo [2/4] conferindo a tarefa de autostart QHOS-Server...
rem  ATENCAO ao que /End faz: ele encerra a INSTANCIA em execucao e nunca
rem  desabilita a tarefa. A versao anterior chamava /Change /ENABLE aqui,
rem  dizendo "religando o autostart" -- religando algo que nunca foi desligado.
rem  Era no-op, e pior: reativava a tarefa mesmo se voce a tivesse desabilitado
rem  de proposito. Aqui so se CONSULTA o estado; nada e alterado sozinho.
schtasks /Query /TN "QHOS-Server" 2>nul
if errorlevel 1 (
  echo   tarefa QHOS-Server nao encontrada - autostart nao configurado.
  echo   ^(rode setup_autostart.ps1 se quiser o servidor subindo no boot^)
)

echo.
echo [3/4] subindo servidor + MT5 + janela do terminal...
rem  Esta linha BLOQUEIA ate voce fechar a janela do terminal: desktop_app.py
rem  abre o MetaTrader 5, sobe o main.py num console proprio (log ao vivo) e
rem  so devolve o controle quando webview.start() termina. Nao ha passo util
rem  depois dela -- por isso o antigo [3/4] nunca rodava na hora que o texto
rem  dava a entender.
python desktop_app.py

echo.
echo [4/4] janela do terminal fechada.
echo   O SERVIDOR CONTINUA RODANDO no console proprio dele.
echo   Para reabrir so a janela: QHOS.bat   ^| Para derrubar: kill_server.ps1
pause >nul
endlocal
