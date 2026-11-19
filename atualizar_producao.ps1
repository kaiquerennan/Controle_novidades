# Atualiza a maquina de producao com o que foi enviado ao GitHub.
#
# Roda a cada 1 minuto pela tarefa "EMC Atualizacao automatica" (criada pelo
# instalar_tarefa_emc.ps1). Sem commit novo nao faz nada e nao escreve no log.
# Com commit novo: para o app, baixa o codigo, reinstala o que mudou de
# dependencia e sobe o app de novo. Tudo fica registrado em logs\atualizacao.log.
#
# Para rodar na mao:  powershell -ExecutionPolicy Bypass -File atualizar_producao.ps1

param(
    [string]$Branch = 'main',
    [string]$TarefaApp = 'EMC Central de Novidades',
    [int]$PortaApp = 8510,
    [int]$PortaWhatsApp = 8511
)

$ErrorActionPreference = 'Stop'
$pasta = Split-Path -Parent $MyInvocation.MyCommand.Path
$tarefaApp = $TarefaApp
$portaApp = $PortaApp
$portaWhatsApp = $PortaWhatsApp
$log = Join-Path $pasta 'logs\atualizacao.log'

function Write-Log([string]$mensagem) {
    New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
    $linha = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $mensagem
    Add-Content -LiteralPath $log -Value $linha -Encoding UTF8
}

# O git escreve progresso no stderr; no PowerShell 5.1 isso viraria erro e
# derrubaria o script. O que vale e o codigo de saida.
function Invoke-Git {
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $saida = & git -C $pasta @args 2>&1 | ForEach-Object { "$_" }
        $codigo = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $anterior
    }
    # TrimEnd, nao Trim: o status --porcelain comeca com espaco (" M arquivo").
    [pscustomobject]@{ Ok = ($codigo -eq 0); Saida = (@($saida) -join "`n").TrimEnd() }
}

function Stop-PortOwner([int]$porta) {
    $conexoes = Get-NetTCPConnection -LocalPort $porta -State Listen -ErrorAction SilentlyContinue
    foreach ($id in @($conexoes | Select-Object -ExpandProperty OwningProcess -Unique)) {
        if ($id -gt 0) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
    }
}

function Test-PortListening([int]$porta) {
    [bool](Get-NetTCPConnection -LocalPort $porta -State Listen -ErrorAction SilentlyContinue)
}

function Wait-Port([int]$porta, [bool]$aberta, [int]$segundos) {
    $limite = (Get-Date).AddSeconds($segundos)
    while ((Get-Date) -lt $limite) {
        if ((Test-PortListening $porta) -eq $aberta) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Stop-App {
    Stop-ScheduledTask -TaskName $tarefaApp -ErrorAction SilentlyContinue
    # Parar a tarefa encerra o cmd, mas o python pode ficar vivo segurando a porta.
    Stop-PortOwner $portaApp
    if (-not (Wait-Port $portaApp $false 30)) {
        Write-Log "AVISO: a porta $portaApp continuou ocupada depois de parar o app."
    }
}

function Start-App {
    try {
        Start-ScheduledTask -TaskName $tarefaApp
    } catch {
        Write-Log "ERRO: nao consegui iniciar a tarefa '$tarefaApp': $($_.Exception.Message)"
        return
    }
    if (Wait-Port $portaApp $true 90) {
        Write-Log 'App no ar.'
    } else {
        Write-Log "ERRO: o app nao abriu a porta $portaApp em 90 s. Veja logs\aplicativo.log."
    }
}

# 1. Ha algo novo?
$busca = Invoke-Git fetch --quiet origin $Branch
if (-not $busca.Ok) {
    Write-Log "ERRO: git fetch falhou (sem rede?): $($busca.Saida)"
    exit 1
}
$atual = (Invoke-Git rev-parse HEAD).Saida
$novo = (Invoke-Git rev-parse "origin/$Branch").Saida
if ($atual -eq $novo) { exit 0 }

if (-not (Invoke-Git merge-base --is-ancestor HEAD "origin/$Branch").Ok) {
    Write-Log "ERRO: esta maquina tem commits que nao estao no GitHub; nada foi atualizado. Resolva com git status."
    exit 1
}

# 2. Alteracao local em codigo e sinal de que alguem mexeu na producao: nao
#    passa por cima. Ja o que o .gitignore manda ignorar (log, .pyc, estado de
#    tela...) muda sozinho com o app rodando: e guardado e devolvido.
$locais = @((Invoke-Git status --porcelain --untracked-files=no).Saida -split "`n" |
    Where-Object { $_ } | ForEach-Object { $_.Substring(3).Trim('"') })
$execucao = @($locais | Where-Object { (Invoke-Git check-ignore --no-index -q -- $_).Ok })
$codigoLocal = @($locais | Where-Object { $_ -notin $execucao })
if ($codigoLocal.Count -gt 0) {
    Write-Log ("ERRO: arquivos alterados nesta maquina: {0}. Nada foi atualizado." -f ($codigoLocal -join ', '))
    exit 1
}

$mudados = @((Invoke-Git diff --name-only $atual $novo).Saida -split "`n" | Where-Object { $_ })
Write-Log ("Atualizando {0} -> {1} ({2} arquivo(s)): {3}" -f `
    $atual.Substring(0, 7), $novo.Substring(0, 7), $mudados.Count, ($mudados -join ', '))

# 3. Para o app (e o WhatsApp, se o codigo dele mudou) antes de mexer nos arquivos.
Stop-App
$whatsappMudou = @($mudados | Where-Object { $_ -like 'whatsapp_gateway/*' }).Count -gt 0
if ($whatsappMudou) {
    Stop-PortOwner $portaWhatsApp
    Wait-Port $portaWhatsApp $false 15 | Out-Null
}

# A copia mantem o caminho relativo: ha varios __init__.cpython-*.pyc com o
# mesmo nome em pastas diferentes.
$guarda = Join-Path $env:TEMP ('emc_atualizacao_' + [guid]::NewGuid().ToString('N'))
foreach ($arquivo in $execucao) {
    $copia = Join-Path $guarda $arquivo
    New-Item -ItemType Directory -Force -Path (Split-Path $copia) | Out-Null
    Copy-Item -LiteralPath (Join-Path $pasta $arquivo) -Destination $copia -Force
    Invoke-Git checkout -- $arquivo | Out-Null
}

# 4. Baixa o codigo e devolve os arquivos de execucao como estavam.
$merge = Invoke-Git merge --ff-only "origin/$Branch"
foreach ($arquivo in $execucao) {
    $destino = Join-Path $pasta $arquivo
    New-Item -ItemType Directory -Force -Path (Split-Path $destino) | Out-Null
    Copy-Item -LiteralPath (Join-Path $guarda $arquivo) -Destination $destino -Force
}
if (Test-Path $guarda) { Remove-Item -LiteralPath $guarda -Recurse -Force -ErrorAction SilentlyContinue }
if (-not $merge.Ok) {
    Write-Log "ERRO: git merge falhou; o app volta com o codigo antigo: $($merge.Saida)"
    Start-App
    exit 1
}

# 5. Dependencias, so quando mudaram. Uma falha aqui fica no log mas nunca
#    impede o passo 6: o app tem de voltar mesmo que a instalacao de errado.
function Invoke-Install([string]$descricao, [string]$diretorio, [scriptblock]$comando) {
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Push-Location $diretorio
    try {
        $saida = & $comando 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) { Write-Log "$descricao atualizadas." }
        else { Write-Log "ERRO: $descricao falharam: $($saida.Trim())" }
    } catch {
        Write-Log "ERRO: $descricao falharam: $($_.Exception.Message)"
    } finally {
        Pop-Location
        $ErrorActionPreference = $anterior
    }
}
if ($mudados -contains 'requirements.txt') {
    Invoke-Install 'Dependencias do Python' $pasta {
        & (Join-Path $pasta '.venv\Scripts\python.exe') -m pip install `
            --disable-pip-version-check -q -r requirements.txt
    }
}
if (@($mudados | Where-Object { $_ -in 'whatsapp_gateway/package.json', 'whatsapp_gateway/package-lock.json' }).Count -gt 0) {
    Invoke-Install 'Dependencias do WhatsApp' (Join-Path $pasta 'whatsapp_gateway') {
        & npm.cmd install --no-fund --no-audit
    }
}

# 6. Sobe o app de novo; ele mesmo reconecta o WhatsApp se foi parado.
Start-App
exit 0
