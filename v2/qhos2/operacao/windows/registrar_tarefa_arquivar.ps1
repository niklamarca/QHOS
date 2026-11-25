# Registra a tarefa diaria QHOS-Arquivar-Capturas. NAO foi executado: rode so depois de instalar os dois arquivos.
# Uso: powershell -NoProfile -ExecutionPolicy Bypass -File registrar_tarefa_arquivar.ps1 [-Hora 19:30] [-Mostrar] [-Remover]
#   -Mostrar: imprime o que seria registrado e nao registra nada.
#   -Remover: apaga SOMENTE a tarefa QHOS-Arquivar-Capturas.
# Mesmo molde das tarefas do heartbeat: sua conta, sessao interativa, janela oculta, uma instancia por vez,
# recupera execucao perdida (StartWhenAvailable). Limite de 3 h porque o primeiro lote pode ser grande.
# 19:30 fica depois do fim da captura nos dois horarios (17:15 ate 30/10, 18:15 depois). A rodada e idempotente:
# se o PC estiver desligado, a proxima pega tudo o que ficou para tras.
param([string]$Hora = '19:30', [switch]$Mostrar, [switch]$Remover)
$ErrorActionPreference = 'Stop'
$nome = 'QHOS-Arquivar-Capturas'
$alvo = 'C:\Users\usuario\Downloads\Rithmic\avisos_discord\arquivar_capturas_diario.ps1'
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
    -Description 'QHOS V2: le e confere toda captura fechada e insere no DataSet permanente em AAAA-MM-DD_<id> (xz, conferido pelo hash do manifesto); nao apaga nada do DataSet nem os brutos; pregoes de 9 a 20/11 ficam selados ate 21/11.' | Out-Null
"tarefa $nome registrada para as $Hora"
