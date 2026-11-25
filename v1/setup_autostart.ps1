# setup_autostart.ps1 - faz o QHOS voltar sozinho depois de reiniciar.
# RODE COMO ADMINISTRADOR, uma vez so.
#
# POR QUE ISTO EXISTE
# -------------------
# Em 13/08/2026 o Windows Update reiniciou a maquina as 08:41:03 (Evento 1074,
# MoUsoCoreWorker.exe / TrustedInstaller.exe). O servidor morreu no mesmo
# segundo e o sistema ficou 10h30 fora do ar, porque as tarefas agendadas
# disparavam AO FAZER LOGIN -- e ninguem logou ate as 19:12.
#
# Reboot de atualizacao e inevitavel. A correcao nao e evitar o reboot: e
# fazer o servidor voltar sem depender de alguem sentar na maquina.
#
# Duas tarefas, de proposito:
#   QHOS-Server  -> AO LIGAR o Windows, sem precisar de login (headless)
#   QHOS-RemoteMonitor -> idem, a vigia tambem nao pode depender de login
# A janela grafica (desktop_app.py) continua sendo aberta por voce, quando
# quiser -- ela precisa de sessao com desktop e nao faz sentido sem alguem ali.

$ErrorActionPreference = "Stop"
$root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = (Get-Command python).Source
$user   = "$env:USERDOMAIN\$env:USERNAME"

Write-Host ""
Write-Host "  raiz   : $root"
Write-Host "  python : $python"
Write-Host "  usuario: $user"
Write-Host ""

function Set-QhosTask {
    param([string]$Nome, [string]$Script, [int]$DelaySeg)

    $acao = New-ScheduledTaskAction -Execute $python `
                                    -Argument "`"$Script`"" `
                                    -WorkingDirectory $root

    # AtStartup: dispara com o Windows, ANTES de qualquer login.
    $gatilho = New-ScheduledTaskTrigger -AtStartup
    $gatilho.Delay = "PT$($DelaySeg)S"   # espera rede e disco assentarem

    # RunLevel Highest + usuario com senha salva = roda sem sessao aberta.
    $principal = New-ScheduledTaskPrincipal -UserId $user `
                                            -LogonType S4U `
                                            -RunLevel Highest

    $config = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -StartWhenAvailable `
                -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
                -ExecutionTimeLimit (New-TimeSpan -Days 0)   # sem limite

    if (Get-ScheduledTask -TaskName $Nome -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Nome -Confirm:$false
        Write-Host "   tarefa antiga '$Nome' removida"
    }
    Register-ScheduledTask -TaskName $Nome -Action $acao -Trigger $gatilho `
                           -Principal $principal -Settings $config | Out-Null
    Write-Host "   OK  $Nome  (ao ligar o Windows, +$DelaySeg s, sem precisar de login)"
}

# servidor primeiro; a vigia 60s depois, para nao acusar queda durante o boot
Set-QhosTask -Nome "QHOS-Server"          -Script (Join-Path $root "main.py")           -DelaySeg 90
Set-QhosTask -Nome "QHOS-RemoteMonitor"   -Script (Join-Path $root "remote_monitor.py") -DelaySeg 150

Write-Host ""
Write-Host "  Conferindo:"
Get-ScheduledTask -TaskName "QHOS-*" | Select-Object TaskName, State | Format-Table -AutoSize
Write-Host ""
Write-Host "  TESTE SEM REINICIAR:  schtasks /Run /TN QHOS-Server"
Write-Host "  VER SE PEGOU:         Get-ScheduledTaskInfo -TaskName QHOS-Server"
Write-Host ""
Write-Host "  RECOMENDADO ainda: Configuracoes > Windows Update > Horario de atividade,"
Write-Host "  para o reboot cair de madrugada e nao no meio do pregao."
Write-Host ""
