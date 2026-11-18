# Cria a tarefa do Agendador do Windows que sobe o EMC Central de Novidades
# sempre que a maquina liga, sem precisar de ninguem logado e sem janela de cmd.
# Rode em um PowerShell aberto como Administrador.

$ErrorActionPreference = 'Stop'
$nome = 'EMC Central de Novidades'
$pasta = 'C:\EMC_Novidade_Visual_IA'

if (-not (Test-Path "$pasta\executar.bat")) {
    throw "Nao encontrei $pasta\executar.bat"
}

# "< nul" faz o "pause" do executar.bat terminar sozinho em caso de erro,
# em vez de deixar a tarefa presa esperando uma tecla que ninguem vai apertar.
$acao = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument "/c `"$pasta\executar.bat`" < nul" -WorkingDirectory $pasta

# Sobe assim que o Windows inicia. Se a rede ainda nao estiver pronta e o app
# falhar, a tarefa tenta de novo sozinha (RestartInterval abaixo).
$gatilho = New-ScheduledTaskTrigger -AtStartup

# Sem limite de tempo (o padrao do Windows mata a tarefa depois de 3 dias)
# e tenta de novo a cada 5 minutos se o app cair.
$config = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 5) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew

$usuario = "$env:USERDOMAIN\$env:USERNAME"
$cred = Get-Credential -UserName $usuario -Message "Senha do Windows de $usuario (para rodar sem ninguem logado)"

Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $config `
    -User $cred.UserName -Password $cred.GetNetworkCredential().Password `
    -RunLevel Highest -Force | Out-Null

Write-Host ''
Write-Host "Tarefa '$nome' criada para o usuario $usuario."
Write-Host 'Para iniciar agora sem reiniciar:  Start-ScheduledTask -TaskName "EMC Central de Novidades"'
Write-Host 'Para parar:                         Stop-ScheduledTask -TaskName "EMC Central de Novidades"'
Write-Host 'Para remover:                       Unregister-ScheduledTask -TaskName "EMC Central de Novidades"'
