# Faz os avisos do Discord e a limpeza das 19:30 rodarem tambem com o Windows na tela de senha, como a
# QHOS-V2-Linux: troca so o tipo de logon das tarefas de Interactive para S4U ("executar com o usuario conectado
# ou nao", sem guardar senha). Acoes, gatilhos e configuracoes ficam iguais.
#   sem parametro : so mostra como esta e o que mudaria (nao precisa de administrador)
#   -Executar     : guarda o XML de cada tarefa em AppData\Local\QHOS\V2\rollback\ e troca para S4U
#   -Desfazer     : registra de novo cada tarefa a partir do XML guardado (volta para Interactive)
# -Executar e -Desfazer exigem PowerShell como administrador. Uma tarefa S4U roda sem janela e sem credencial de
# rede do Windows; o webhook do Discord e HTTPS comum e a variavel de usuario continua sendo lida (perfil carregado).
param([switch]$Executar, [switch]$Desfazer)
$ErrorActionPreference = 'Stop'
$tarefas = 'QHOS-Discord-Heartbeat-6h', 'QHOS-Discord-Alerta-Captura', 'QHOS-Limpar-Brutos-ES'
$rollback = 'C:\Users\usuario\AppData\Local\QHOS\V2\rollback'
$sufixo = '.antes_s4u.xml'

if ($Executar -and $Desfazer) { 'use -Executar ou -Desfazer, nao os dois'; exit 2 }
if ($Executar -or $Desfazer) {
    $quem = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $quem.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        'precisa de PowerShell como administrador (botao direito no PowerShell > Executar como administrador)'
        exit 2
    }
}

$falhas = 0
foreach ($n in $tarefas) {
    $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if (-not $t) { '{0}: AUSENTE' -f $n; $falhas++; continue }
    $xml = Join-Path $rollback ($n + $sufixo)
    if ($Desfazer) {
        if (-not (Test-Path -LiteralPath $xml)) { '{0}: sem XML guardado ({1}); nada feito' -f $n, $xml; $falhas++; continue }
        Register-ScheduledTask -TaskName $n -Xml (Get-Content -LiteralPath $xml -Raw) -Force | Out-Null
        '{0}: voltou para {1}' -f $n, (Get-ScheduledTask -TaskName $n).Principal.LogonType
        continue
    }
    $antes = [string]$t.Principal.LogonType
    if ($antes -eq 'S4U') { '{0}: ja roda sem login (S4U, usuario {1})' -f $n, $t.Principal.UserId; continue }
    if (-not $Executar) {
        '{0}: {1} -> S4U, usuario {2}, nivel {3} [so mostrando; rode com -Executar como administrador]' -f `
            $n, $antes, $t.Principal.UserId, $t.Principal.RunLevel
        continue
    }
    New-Item -ItemType Directory -Path $rollback -Force | Out-Null
    if (-not (Test-Path -LiteralPath $xml)) {  # o primeiro XML e o original: nunca sobrescrito
        Export-ScheduledTask -TaskName $n | Set-Content -LiteralPath $xml -Encoding Unicode
    }
    $p = New-ScheduledTaskPrincipal -UserId $t.Principal.UserId -LogonType S4U -RunLevel $t.Principal.RunLevel
    Set-ScheduledTask -TaskName $n -Principal $p | Out-Null
    $depois = [string](Get-ScheduledTask -TaskName $n).Principal.LogonType
    '{0}: {1} -> {2}' -f $n, $antes, $depois
    if ($depois -ne 'S4U') { $falhas++ }
}
if ($Executar -and -not $falhas) {
    ''
    'Pronto. Para conferir agora, com voce logado (a tarefa ja roda no modo sem login):'
    "  Start-ScheduledTask -TaskName 'QHOS-Discord-Heartbeat-6h'   # o painel chega no Discord em ate 1 minuto"
    "  (Get-ScheduledTaskInfo -TaskName 'QHOS-Discord-Heartbeat-6h').LastTaskResult   # 0 = rodou certo"
    'Para voltar como estava: este mesmo script com -Desfazer.'
}
if ($falhas) { exit 1 } else { exit 0 }
