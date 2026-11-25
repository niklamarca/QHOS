@echo off
cd /d "%~dp0"
echo [START %date% %time%] instalando MetaTrader5 + pywebview... > install_log.txt
python -m pip install --upgrade MetaTrader5 pywebview >> install_log.txt 2>&1
echo [DONE exit=%errorlevel%] >> install_log.txt
exit
