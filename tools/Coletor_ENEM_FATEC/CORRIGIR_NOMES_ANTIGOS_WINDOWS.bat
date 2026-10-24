@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON=%~dp0..\..\.venv\Scripts\python.exe"
echo ATENCAO: primeiro crie um backup da pasta resultados.
echo Este processo renomeia downloads catalogados e atualiza o CSV, sem alterar o conteudo dos PDFs.
pause
if exist "%PYTHON%" (
  "%PYTHON%" -m pip install -r "%~dp0requirements_organizador.txt"
  if errorlevel 1 goto erro
  "%PYTHON%" "%~dp0organizar_resultados.py"
) else (
  py -3 -m pip install -r "%~dp0requirements_organizador.txt"
  if errorlevel 1 goto erro
  py -3 "%~dp0organizar_resultados.py"
)
if errorlevel 1 goto erro
echo.
echo Concluido. Confira resultados\materiais\ENEM\2002 e o relatorio de renomeacoes.
pause
exit /b 0
:erro
echo Houve um erro. Confira a mensagem acima.
pause
exit /b 1
