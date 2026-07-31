@echo off
setlocal EnableExtensions
set "PROJECT_ROOT=C:\EMC_Novidade_Visual_IA"
set "VENV_PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
cd /d "%PROJECT_ROOT%" || goto :error
if /I not "%CD%"=="%PROJECT_ROOT%" (
  echo ERRO: diretorio atual inesperado: %CD%
  goto :error
)
if not exist "%VENV_PYTHON%" (
  echo ERRO: o ambiente virtual exclusivo nao foi encontrado.
  echo Caminho esperado: "%VENV_PYTHON%"
  echo Crie a .venv do projeto antes de instalar as dependencias.
  goto :error
)
if /I not "%VENV_PYTHON%"=="C:\EMC_Novidade_Visual_IA\.venv\Scripts\python.exe" (
  echo ERRO: caminho do Python virtual rejeitado.
  goto :error
)
"%VENV_PYTHON%" -c "import sys, nicegui, PIL, google.genai, openai; from openai import OpenAI; assert sys.executable.lower() == r'%VENV_PYTHON%'.lower(); assert openai.__version__ == '2.48.0'" >nul 2>&1
if errorlevel 1 (
  echo ERRO: ambiente invalido ou dependencias incorretas na .venv.
  echo A aplicacao exige OpenAI SDK 2.48.0 e o Python exclusivo do projeto.
  echo Execute "%PROJECT_ROOT%\instalar_dependencias.bat" e tente novamente.
  goto :error
)
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8510 -State Listen -ErrorAction SilentlyContinue) { exit 1 }"
if errorlevel 1 (
  echo ERRO: a porta 8510 esta ocupada. Nenhum processo foi encerrado.
  goto :error
)
"%VENV_PYTHON%" "%PROJECT_ROOT%\app.py"
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo A execucao foi interrompida com erro.
pause
exit /b 1