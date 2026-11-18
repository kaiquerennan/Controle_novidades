@echo off
setlocal EnableExtensions
set "PROJECT_ROOT=C:\EMC_Novidade_Visual_IA"
set "GATEWAY_DIR=%PROJECT_ROOT%\whatsapp_gateway"
cd /d "%GATEWAY_DIR%" || goto :error
where node >nul 2>&1
if errorlevel 1 (
  echo ERRO: Node.js nao foi encontrado.
  echo O envio pelo WhatsApp usa a biblioteca Baileys, que roda em Node.js.
  echo Instale o Node.js LTS e execute este arquivo novamente.
  goto :error
)
node --version
call npm --version
echo.
echo Instalando as dependencias do servico de WhatsApp...
call npm install --no-fund --no-audit
if errorlevel 1 goto :error
echo.
echo Dependencias instaladas.
echo Abra a aplicacao, acesse WhatsApp no menu lateral e leia o QR para vincular a conta.
exit /b 0

:error
echo.
echo A instalacao foi interrompida com erro.
pause
exit /b 1
