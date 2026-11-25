param([string]$StartToken = '')
$ErrorActionPreference = 'Stop'
if (-not $StartToken) {$StartToken=[guid]::NewGuid().ToString()}
if ([guid]::Parse($StartToken).ToString() -cne $StartToken) {throw 'invalid_start_token'}
$mutex = New-Object System.Threading.Mutex($false, 'Local\QHOSV2LinuxKeepAlive')
if (-not $mutex.WaitOne(0)) { exit 0 }
try {
    while ($true) {
        & "$env:SystemRoot\System32\wsl.exe" -d Ubuntu -u usuario --exec /bin/sh /home/usuario/qhos-v2/bin/keep_wsl.sh $StartToken
        Start-Sleep -Seconds 60
    }
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
