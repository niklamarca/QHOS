$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvPath = Join-Path $ProjectRoot ".env"
$TempPath = Join-Path $ProjectRoot ".env.databento.tmp"
$BackupPath = Join-Path $ProjectRoot ".env.databento.rollback"
$HadEnv = Test-Path -LiteralPath $EnvPath

Write-Host "Quant HPC OS - Configuracao Databento" -ForegroundColor Cyan
Write-Host "A chave nao sera exibida na tela."
$SecureKey = Read-Host "Cole sua DATABENTO_API_KEY" -AsSecureString
$Bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
try {
    $ApiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Bstr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Bstr)
}

if (-not $ApiKey.StartsWith("db-") -or $ApiKey.Length -lt 16) {
    throw "A chave nao tem o formato Databento esperado (db-...)."
}

$Lines = @()
if (Test-Path -LiteralPath $EnvPath) {
    $Lines = @(Get-Content -LiteralPath $EnvPath -Encoding UTF8)
}
$Wanted = [ordered]@{
    "DATABENTO_API_KEY" = $ApiKey
    "EVENT_PLANE_ENABLED" = "true"
    "DATABENTO_BOOTSTRAP_SHADOW" = "true"
    "DATABENTO_SHADOW_DATASET" = "EQUS.MINI"
    "DATABENTO_SHADOW_SCHEMA" = "ohlcv-1m"
    "DATABENTO_SHADOW_SYMBOL" = "SPY"
    "DATABENTO_SHADOW_USE_QHOS_UNIVERSE" = "true"
    "DATABENTO_SHADOW_LIMIT" = "50000"
    "DATABENTO_BOOTSTRAP_MAX_USD" = "0.25"
    "DATABENTO_DAILY_SPEND_CAP_USD" = "1.00"
    "DATABENTO_SHADOW_POLL_MINUTES" = "60"
}

$Filtered = foreach ($Line in $Lines) {
    $Name = if ($Line -match "^\s*([^#=\s]+)\s*=") { $Matches[1].ToUpperInvariant() } else { "" }
    if (-not $Wanted.Contains($Name)) { $Line }
}
$Output = @($Filtered)
$Output += ""
$Output += "# Databento Event Plane - gerado por setup_databento.ps1"
foreach ($Entry in $Wanted.GetEnumerator()) {
    $Output += "$($Entry.Key)=$($Entry.Value)"
}

try {
    if ($HadEnv) {
        Copy-Item -LiteralPath $EnvPath -Destination $BackupPath -Force
    }
    $Output | Set-Content -LiteralPath $TempPath -Encoding UTF8
    Move-Item -LiteralPath $TempPath -Destination $EnvPath -Force

    # Remove heranca e limita o arquivo ao usuario atual, SYSTEM e
    # administradores. A chave continua local, mas nao fica legivel por outros
    # usuarios comuns da maquina.
    $CurrentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $EnvPath /inheritance:r /grant:r "*$CurrentSid`:(R,W)" "*S-1-5-18`:(F)" "*S-1-5-32-544`:(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Nao foi possivel restringir as permissoes do .env."
    }
}
catch {
    if ($HadEnv -and (Test-Path -LiteralPath $BackupPath)) {
        Move-Item -LiteralPath $BackupPath -Destination $EnvPath -Force
    }
    elseif (-not $HadEnv -and (Test-Path -LiteralPath $EnvPath)) {
        Remove-Item -LiteralPath $EnvPath -Force
    }
    throw
}
finally {
    if (Test-Path -LiteralPath $TempPath) {
        Remove-Item -LiteralPath $TempPath -Force
    }
}
$ApiKey = $null

Write-Host "Chave salva. Executando autenticacao e probe shadow limitado..." -ForegroundColor Yellow
Push-Location $ProjectRoot
try {
    & python databento_doctor.py --bootstrap
    if ($LASTEXITCODE -ne 0) {
        throw "Databento Doctor terminou com codigo $LASTEXITCODE."
    }
}
catch {
    if ($HadEnv -and (Test-Path -LiteralPath $BackupPath)) {
        Move-Item -LiteralPath $BackupPath -Destination $EnvPath -Force
    }
    elseif (-not $HadEnv -and (Test-Path -LiteralPath $EnvPath)) {
        Remove-Item -LiteralPath $EnvPath -Force
    }
    throw
}
finally {
    Pop-Location
}
if (Test-Path -LiteralPath $BackupPath) {
    Remove-Item -LiteralPath $BackupPath -Force
}
Write-Host "Databento pronto. Aplique QHOS_RESTART para o dashboard carregar o novo estado." -ForegroundColor Green
