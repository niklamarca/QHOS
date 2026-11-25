# Reviewed administrative step: register independent startup; retire legacy startup/alerts/QA.
# Does not change the clock, credentials, power plan, market login or application data.
$ErrorActionPreference = 'Stop'
$base = 'C:\Users\usuario\AppData\Local\QHOS\V2'
function Save-State([string]$phase,[string]$detail='') {
    @{phase=$phase;detail=$detail;utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $base 'admin-progress.json') -Encoding UTF8
}
try {
    $principalCheck = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'administrator_required' }
    Save-State 'PREPARING'
    foreach ($file in @('start_wsl_hidden.ps1','start_wsl_after_clock.ps1','keep_awake.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $base $file) -PathType Leaf)) { throw 'reviewed_script_missing' }
    }
    $oldLinux=Get-ScheduledTask -TaskName 'QHOS-Linux' -ErrorAction Stop
    $oldAwake=Get-ScheduledTask -TaskName 'QHOS-KeepAwake' -ErrorAction Stop
    foreach ($name in @('QHOS-V2-Linux','QHOS-V2-KeepAwake')) {
        $existing=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($existing -and [string]$existing.State -eq 'Running') {throw 'existing_v2_task_running_requires_status_review'}
    }
    $startToken=[guid]::NewGuid().ToString()
    $action=New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $base 'start_wsl_after_clock.ps1')+'" -StartToken '+$startToken) -WorkingDirectory $base
    $principal=New-ScheduledTaskPrincipal -UserId $oldLinux.Principal.UserId -LogonType S4U -RunLevel Limited
    Register-ScheduledTask -TaskName 'QHOS-V2-Linux' -Action $action -Trigger $oldLinux.Triggers -Settings $oldLinux.Settings -Principal $principal -Description 'QHOS V2: partida independente, sem login de mercado.' -Force -ErrorAction Stop | Out-Null
    $awakeAction=New-ScheduledTaskAction -Execute $oldAwake.Actions[0].Execute -Argument ('-B "'+(Join-Path $base 'keep_awake.py')+'"') -WorkingDirectory $base
    Register-ScheduledTask -TaskName 'QHOS-V2-KeepAwake' -Action $awakeAction -Trigger $oldAwake.Triggers -Settings $oldAwake.Settings -Principal $oldAwake.Principal -Description 'QHOS V2: preserva politica de impedir suspensao.' -Force -ErrorAction Stop | Out-Null
    Start-ScheduledTask -TaskName 'QHOS-V2-Linux' -ErrorAction Stop
    Start-ScheduledTask -TaskName 'QHOS-V2-KeepAwake' -ErrorAction Stop
    Save-State 'WAITING_FOR_V2_KEEPALIVE'
    $ready=$false
    $readinessWatch=[System.Diagnostics.Stopwatch]::StartNew()
    while ($readinessWatch.Elapsed.TotalSeconds -lt 240) {
        & "$env:SystemRoot\System32\wsl.exe" -d Ubuntu -u usuario --exec /usr/bin/python3 /home/usuario/qhos-v2/bin/verify_keepalive.py $startToken 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {$ready=$true;break}
        Start-Sleep -Seconds 2
    }
    if (-not $ready) {throw 'new_keepalive_not_confirmed_legacy_preserved'}
    foreach ($name in @('QHOS-V2-Linux','QHOS-V2-KeepAwake')) {
        $newTask=Get-ScheduledTask -TaskName $name -ErrorAction Stop
        if ([string]$newTask.State -ne 'Running' -or -not $newTask.Settings.Enabled) {throw 'new_task_not_running_legacy_preserved'}
    }
    $retire=@('QHOS-Linux','QHOS-KeepAwake','QHOS-Discord-Alerta-Captura','QHOS-Discord-Heartbeat-6h','QHOS QA - Continuo','QHOS Seguranca - Continuo')
    foreach ($name in $retire) {
        $task=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if (-not $task) {continue}
        $snapshot=Join-Path (Join-Path $base 'rollback') ($name+'.xml')
        if (-not (Test-Path -LiteralPath $snapshot)) {Export-ScheduledTask -TaskName $name | Set-Content -LiteralPath $snapshot -Encoding Unicode}
        Disable-ScheduledTask -TaskName $name -ErrorAction Stop | Out-Null
        Stop-ScheduledTask -TaskName $name -ErrorAction Stop
    }
    # Old QHOS-Linux's child PowerShell may outlive the scheduled parent. Stop only
    # a verified old -File launcher, never a shell carrying arbitrary command text.
    $oldScripts=@('C:\ProgramData\QHOS\clock\start_wsl_after_clock.ps1','C:\Users\usuario\Claude\Projects\trader\scripts\linux\start_wsl_hidden.ps1')
    foreach ($process in Get-CimInstance Win32_Process -Filter "Name='powershell.exe'") {
        $cmd=[string]$process.CommandLine
        if ($process.ProcessId -eq $PID) {continue}
        foreach ($script in $oldScripts) {
            # Match the entire known child launcher argv, not a suffix inside -c text.
            $exe=[regex]::Escape("$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe")
            $file=[regex]::Escape($script)
            $knownLauncher='(?i)^(?:"'+$exe+'"|'+$exe+')\s+-NoProfile\s+-WindowStyle\s+Hidden\s+-ExecutionPolicy\s+Bypass\s+-File\s+(?:"'+$file+'"|'+$file+')\s*$'
            if ($cmd -match $knownLauncher) {
                Stop-Process -Id $process.ProcessId -ErrorAction Stop
            }
        }
    }
    $states=Get-ScheduledTask -TaskName ($retire+@('QHOS-V2-Linux','QHOS-V2-KeepAwake')) -ErrorAction SilentlyContinue | ForEach-Object {@{name=$_.TaskName;enabled=$_.Settings.Enabled;state=[string]$_.State}}
    $states | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $base 'scheduled-tasks-final.json') -Encoding UTF8
    if (@($states | Where-Object {$_.name -in $retire -and $_.enabled}).Count) {throw 'legacy_task_still_enabled'}
    Save-State 'COMPLETE' 'New tasks running; legacy task XML preserved and legacy tasks disabled.'
} catch {
    Save-State 'FAILED' ([string]$_.Exception.Message)
    exit 1
}
