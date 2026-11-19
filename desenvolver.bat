@echo off
rem Sobe o app para DESENVOLVIMENTO: recarrega sozinho a cada arquivo .py salvo
rem e nao envia nada automaticamente (agendamentos e WhatsApp automatico
rem desligados), para nao duplicar as mensagens da maquina de producao.
rem Na producao use executar.bat (ou a tarefa do Windows).
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo ERRO: .venv nao encontrada. Crie com: python -m venv .venv
  pause
  exit /b 1
)
set "EMC_MODO_DESENVOLVIMENTO=1"
echo Modo desenvolvimento: http://localhost:8510  ^(Ctrl+C para parar^)
".venv\Scripts\python.exe" app.py
