@echo off
setlocal EnableExtensions
set "PROJECT_ROOT=C:\EMC_Novidade_Visual_IA"
set "VENV_PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
cd /d "%PROJECT_ROOT%" || goto :error
if not exist "%VENV_PYTHON%" (
  echo ERRO: o ambiente virtual exclusivo nao foi encontrado.
  echo Caminho esperado: "%VENV_PYTHON%"
  echo Crie a .venv com: python -m venv "%PROJECT_ROOT%\.venv"
  goto :error
)
"%VENV_PYTHON%" -c "import sys; print('Interpretador:', sys.executable)"
if errorlevel 1 (
  echo ERRO: a .venv existe mas o interpretador nao executa.
  echo Isso acontece quando a .venv foi criada em outra maquina ou por outro usuario.
  echo Apague a pasta .venv e crie novamente com: python -m venv "%PROJECT_ROOT%\.venv"
  goto :error
)
"%VENV_PYTHON%" -m pip install --upgrade pip
"%VENV_PYTHON%" -m pip install -r "%PROJECT_ROOT%\requirements.txt"
if errorlevel 1 goto :error
echo.
echo Dependencias instaladas. Execute verificar_ambiente.bat para conferir.
exit /b 0

:error
echo.
echo A instalacao foi interrompida com erro.
pause
exit /b 1
