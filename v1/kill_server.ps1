# kill_server.ps1 - encerra o QHOS de verdade e so devolve o controle
# quando o BANCO estiver livre.
#
# POR QUE FOI REESCRITO (2026-08-13)
# ----------------------------------
# A versao anterior matava apenas quem ESCUTAVA a porta 8000 e esperava 3s
# fixos, checando a PORTA. Um processo que travou (ou que morreu deixando o
# handle preso pelo Windows) para de escutar mas CONTINUA segurando
# quant_hpc.db. O boot seguinte encontrava o banco travado e caia para
# :memory:, descartando em silencio tudo que aprendia. Medido nos logs:
# 20 de 102 boots rodaram sem persistencia nenhuma.
#
# Alem disso o fallback via netstat usava $pid, que e variavel AUTOMATICA e
# somente-leitura no PowerShell -- aquele ramo nunca executou.

$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$db   = Join-Path $root "data_lake\quant_hpc.db"
$killed = 0

function Kill-Pid([int]$targetPid, [string]$motivo) {
    $p = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
    if ($p) {
        Write-Host ("   matando PID " + $p.Id + " (" + $p.ProcessName + ") - " + $motivo)
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        return 1
    }
    return 0
}

# ---- 1) quem escuta a porta 8000 -------------------------------------
foreach ($c in (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) {
    $killed += Kill-Pid $c.OwningProcess "porta 8000"
}

# ---- 2) fallback netstat (agora com variavel valida) ------------------
if ($killed -eq 0) {
    foreach ($l in (netstat -ano | Select-String ":8000\s+.*LISTENING")) {
        $procId = ($l -split '\s+')[-1]
        if ($procId -match '^\d+$') { $killed += Kill-Pid ([int]$procId) "netstat" }
    }
}

# ---- 3) O PONTO NOVO: qualquer python deste projeto, escutando ou nao --
# E este que segurava o banco e sobrevivia ao kill anterior.
$alvos = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -and ($_.CommandLine -like "*main.py*" -or $_.CommandLine -like "*desktop_app*") -and $_.CommandLine -like "*trader*" }
foreach ($a in $alvos) {
    $killed += Kill-Pid ([int]$a.ProcessId) "python do QHOS (segurava o banco)"
}

if ($killed -eq 0) { Write-Host "   nenhum processo do QHOS encontrado" }

# ---- 4) So libera quando o BANCO abrir (nao a porta) -------------------
# A porta livre nao prova nada: o handle do arquivo pode continuar preso.
$livre = $false
for ($i = 1; $i -le 20; $i++) {
    if (-not (Test-Path $db)) { $livre = $true; break }
    try {
        $fs = [IO.File]::Open($db, 'Open', 'ReadWrite', 'None')
        $fs.Close(); $fs.Dispose()
        $livre = $true
        break
    } catch {
        if ($i -eq 1) { Write-Host "   aguardando o Windows liberar o banco..." }
        Start-Sleep -Milliseconds 750
    }
}

if ($livre) {
    Write-Host "   banco LIVRE - pode subir (persistencia garantida)"
} else {
    Write-Host ""
    Write-Host "   ###############################################################"
    Write-Host "   #  ATENCAO: o banco AINDA esta travado apos 15s.              #"
    Write-Host "   #  Se subir agora, a sessao roda em RAM e NAO GUARDA NADA.    #"
    Write-Host "   #  Feche todas as janelas do QHOS, ou reinicie o Windows.     #"
    Write-Host "   ###############################################################"
    Write-Host ""
}

$still = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($still) { Write-Host "   AVISO: porta 8000 ainda ocupada" } else { Write-Host "   porta 8000 livre - OK" }

# Codigo de saida para quem chama decidir. Imprimir o aviso e devolver 0 fazia
# o QHOS_RESTART.bat subir por cima do banco travado -- justamente o cenario
# que este script existe para impedir. 1 = nao suba.
if ($livre) { exit 0 } else { exit 1 }
