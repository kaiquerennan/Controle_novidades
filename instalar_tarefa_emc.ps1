# Cria as tarefas do Agendador do Windows da maquina de producao:
#   - "EMC Central de Novidades": sobe o app sempre que a maquina liga, sem
#     precisar de ninguem logado e sem janela de cmd;
#   - "EMC Atualizacao automatica": a cada 1 minuto confere o GitHub e, havendo
#     commit novo, atualiza e reinicia o app (atualizar_producao.ps1).
# Rode em um PowerShell aberto como Administrador. Rodar de novo substitui as duas.

$ErrorActionPreference = 'Stop'
$nome = 'EMC Central de Novidades'
$nomeAtualizacao = 'EMC Atualizacao automatica'
$pasta = 'C:\EMC_Novidade_Visual_IA'

foreach ($arquivo in 'executar.bat', 'atualizar_producao.ps1') {
    if (-not (Test-Path "$pasta\$arquivo")) { throw "Nao encontrei $pasta\$arquivo" }
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

$acaoAtualizacao = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$pasta\atualizar_producao.ps1`"" `
    -WorkingDirectory $pasta

# A cada 1 minuto, para sempre (sem RepetitionDuration o Windows repete sem fim).
# Sem commit novo a rodada so faz um git fetch e termina em poucos segundos.
$gatilhoAtualizacao = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes 1)

# Uma atualizacao com pip/npm leva alguns minutos; 30 e folga. Se uma rodada
# ainda estiver em curso, a seguinte e ignorada.
$configAtualizacao = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew

$usuario = "$env:USERDOMAIN\$env:USERNAME"
$cred = Get-Credential -UserName $usuario -Message "Senha do Windows de $usuario (para rodar sem ninguem logado)"
$senha = $cred.GetNetworkCredential().Password

Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $config `
    -User $cred.UserName -Password $senha -RunLevel Highest -Force | Out-Null

Register-ScheduledTask -TaskName $nomeAtualizacao -Action $acaoAtualizacao `
    -Trigger $gatilhoAtualizacao -Settings $configAtualizacao `
    -User $cred.UserName -Password $senha -RunLevel Highest -Force | Out-Null

Write-Host ''
Write-Host "Tarefas '$nome' e '$nomeAtualizacao' criadas para o usuario $usuario."
Write-Host 'Para iniciar o app agora sem reiniciar:  Start-ScheduledTask -TaskName "EMC Central de Novidades"'
Write-Host 'Para parar o app:                         Stop-ScheduledTask -TaskName "EMC Central de Novidades"'
Write-Host 'Para pausar a atualizacao automatica:     Disable-ScheduledTask -TaskName "EMC Atualizacao automatica"'
Write-Host "Historico das atualizacoes:               $pasta\logs\atualizacao.log"
