# Apply only after explicit consent to trust this exact local certificate.
$ErrorActionPreference='Stop'
if ([Security.Principal.WindowsIdentity]::GetCurrent().Name -ine 'MACHINE\usuario') {throw 'wrong_windows_account'}
$file='C:\Users\usuario\AppData\Local\QHOS\V2\brain-local-v2.crt'
if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'e1f488d3df304098e4cc672fa2b6015340c77153f2cc0961a98536276e73c20f') {throw 'certificate_changed'}
$certificate=[Security.Cryptography.X509Certificates.X509Certificate2]::new($file)
$hash=[Security.Cryptography.SHA256]::Create()
try {$derHash=[BitConverter]::ToString($hash.ComputeHash($certificate.RawData)).Replace('-','').ToLowerInvariant()} finally {$hash.Dispose()}
if ($derHash -ne 'ebb40bb81e3c4ab690c8c097539f3cb50f93a42ff09eb5fdd3a8a4ab3041e926') {throw 'certificate_identity_mismatch'}
if ($certificate.Subject -ne 'CN=localhost' -or $certificate.NotAfter -lt [DateTime]::Now) {throw 'certificate_invalid'}
$path='Cert:\CurrentUser\Root\'+$certificate.Thumbprint
if (-not (Test-Path -LiteralPath $path)) {
    Import-Certificate -FilePath $file -CertStoreLocation 'Cert:\CurrentUser\Root' -ErrorAction Stop | Out-Null
}
if (-not (Test-Path -LiteralPath $path)) {throw 'trust_not_confirmed'}
$response=Invoke-WebRequest -Uri 'https://localhost:8443/health' -UseBasicParsing -TimeoutSec 10
if ([int]$response.StatusCode -ne 200) {throw 'normal_https_validation_failed'}
@{certificate_sha256=$derHash;store='CurrentUser/Root';https_status=[int]$response.StatusCode;validation='normal';utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath 'C:\Users\usuario\AppData\Local\QHOS\V2\tls-trust-result.json' -Encoding UTF8
