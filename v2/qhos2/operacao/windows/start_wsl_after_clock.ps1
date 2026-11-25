# QHOS V2 - partida independente do WSL.
# Preserva a espera limitada por evento de correcao do NetTime.
# Nao modifica o relogio e nao certifica seu desvio atual.
# Apos o limite, inicia apenas os servicos V2 de leitura/Brain e registra
# a ausencia de confirmacao. Nao inicia captura nem login de mercado.
param([int]$MaxWaitS = 180, [int]$PollS = 2, [string]$StartToken = '',
      [string]$Original = 'C:\Users\usuario\AppData\Local\QHOS\V2\start_wsl_hidden.ps1',
      [string]$Marca = 'C:\Users\usuario\AppData\Local\QHOS\V2\partida_wsl.log')
$ErrorActionPreference = 'Continue'
$inv = [System.Globalization.CultureInfo]::InvariantCulture
$dir = Split-Path $Marca -Parent
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
function Log($m) { Add-Content -Path $Marca -Value ([datetime]::UtcNow.ToString('o', $inv) + ' ' + $m) -Encoding ascii }
$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
Log ('inicio guarda; boot=' + $boot.ToUniversalTime().ToString('o', $inv) + ' maxwait=' + $MaxWaitS + 's')
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$ok = $false; $detalhe = ''
while ($sw.Elapsed.TotalSeconds -lt $MaxWaitS) {
  $ev = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-General'; Id=1; StartTime=$boot} -MaxEvents 5 -ErrorAction SilentlyContinue
  foreach ($e in $ev) {
    $evpid = [int]([xml]$e.ToXml()).Event.System.Execution.ProcessID
    $p = Get-Process -Id $evpid -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -match 'NetTimeService') { $ok = $true; $detalhe = ('evento Id1 em ' + $e.TimeCreated.ToUniversalTime().ToString('o', $inv) + ' pid=' + $evpid); break }
  }
  if ($ok) { break }
  Start-Sleep -Seconds $PollS
}
if ($ok) { Log ('relogio corrigido pelo NetTime antes de subir o WSL: ' + $detalhe + ' (espera ' + [int]$sw.Elapsed.TotalSeconds + ' s)') }
else { Log ('TETO VENCIDO sem correcao do NetTime em ' + $MaxWaitS + ' s: subindo o WSL SEM garantia de relogio - conferir monitor') }
& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File $Original -StartToken $StartToken


