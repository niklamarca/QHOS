# Registra a tarefa diaria QHOS-Limpar-Brutos-ES (19:30). Roda do repositorio; o alvo e o .ps1 instalado.
# Uso: powershell -NoProfile -ExecutionPolicy Bypass -File registrar_tarefa_limpar_es.ps1 [-Hora 19:30] [-Mostrar] [-Remover]
#   -Mostrar: imprime o que seria registrado e nao registra nada.
#   -Remover: apaga SOMENTE a tarefa QHOS-Limpar-Brutos-ES.
# Mesmo molde das tarefas do heartbeat: sua conta, sessao interativa, janela oculta, uma instancia por vez, recupera
# execucao perdida. 19:30 fica depois do fechamento da coleta multiativo (por volta de 17:20) nos dois horarios.
param([string]$Hora = '19:30', [switch]$Mostrar, [switch]$Remover)
$ErrorActionPreference = 'Stop'
$nome = 'QHOS-Limpar-Brutos-ES'
$alvo = 'C:\Users\usuario\Downloads\Rithmic\avisos_discord\limpar_brutos_es_diario.ps1'
if ($Remover) {
    if (Get-ScheduledTask -TaskName $nome -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $nome -Confirm:$false; "tarefa $nome removida" }
    else { "tarefa $nome nao existe" }
    return
}
if (-not (Test-Path $alvo)) { throw "instale antes: $alvo" }
if (Get-ScheduledTask -TaskName $nome -ErrorAction SilentlyContinue) { throw "a tarefa $nome ja existe; use -Remover antes de registrar de novo" }
$acao = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $alvo)
$gatilho = New-ScheduledTaskTrigger -Daily -At $Hora
$config = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
if ($Mostrar) {
    "tarefa $nome, todo dia as $Hora"
    "acao: powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$alvo`""
    "conta: $($principal.UserId), interativa; limite 3 h; uma instancia por vez; recupera execucao perdida"
    return
}
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $config -Principal $principal `
    -Description 'QHOS V2: tira de capturas/ os brutos do ES com 7 dias ou mais, so depois de reler o .tar.xz do dia no DataSet e conferir uma segunda copia local; nunca altera o DataSet.' | Out-Null
"tarefa $nome registrada para as $Hora"
