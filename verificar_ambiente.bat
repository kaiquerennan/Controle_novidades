@echo off
setlocal EnableExtensions
set "PROJECT_ROOT=C:\EMC_Novidade_Visual_IA"
set "VENV_PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
echo Diretorio atual: %CD%
echo Python previsto: %VENV_PYTHON%
if exist "%VENV_PYTHON%" (
  echo .venv: existente
  "%VENV_PYTHON%" -c "import sys; print('Interpretador:', sys.executable)"
  "%VENV_PYTHON%" -c "import openai; from openai import OpenAI; print('OpenAI:', openai.__version__); print('Modulo:', openai.__file__)"
  if errorlevel 1 echo OpenAI SDK: AUSENTE OU INCOMPATIVEL
  "%VENV_PYTHON%" -c "import pyodbc; print('pyodbc:', pyodbc.version); print('Drivers ODBC SQL Server:', [d for d in pyodbc.drivers() if 'SQL Server' in d])"
  if errorlevel 1 echo pyodbc: AUSENTE
) else (
  echo .venv: nao existente
  echo Versao: indisponivel ate a criacao da .venv
)
echo Porta configurada: 8510
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8510 -State Listen -ErrorAction SilentlyContinue) { Write-Host 'Situacao da porta 8510: OCUPADA' } else { Write-Host 'Situacao da porta 8510: LIVRE' }"
echo Isolamento: nenhuma pasta da automacao sera utilizada.
echo Este script nao instala nem altera arquivos.
exit /b 0
