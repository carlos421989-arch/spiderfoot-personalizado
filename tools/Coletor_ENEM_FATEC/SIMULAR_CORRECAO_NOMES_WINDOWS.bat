@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON=%~dp0..\..\.venv\Scripts\python.exe"
echo SIMULACAO: nenhum arquivo sera renomeado ou movido.
if exist "%PYTHON%" (
  "%PYTHON%" -m pip install -r "%~dp0requirements_organizador.txt"
  if errorlevel 1 goto erro
  "%PYTHON%" "%~dp0organizar_resultados.py" --simular
) else (
  py -3 -m pip install -r "%~dp0requirements_organizador.txt"
  if errorlevel 1 goto erro
  py -3 "%~dp0organizar_resultados.py" --simular
)
if errorlevel 1 goto erro
pause
exit /b 0
:erro
echo A simulacao encontrou um erro. Confira a mensagem acima.
pause
exit /b 1
