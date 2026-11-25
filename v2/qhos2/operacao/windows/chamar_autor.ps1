# Envia uma mensagem avulsa ao Discord do autor (pedidos de aprovacao, lembretes).
# Uso: powershell -NoProfile -ExecutionPolicy Bypass -File chamar_autor.ps1 -Mensagem "texto" [-Chamar]
# -Chamar acrescenta @everyone (so ha o autor no servidor) para gerar notificacao no celular.
param([Parameter(Mandatory=$true)][string]$Mensagem, [switch]$Chamar)
$u = [Environment]::GetEnvironmentVariable('QHOS_AVISOS_WEBHOOK_URL', 'User')
if (-not $u -or $u -notmatch '^https://(discord|discordapp)\.com/api/webhooks/') { exit 2 }
$obj = @{ content = $Mensagem; username = 'QHOS Avisos' }
if ($Chamar) { $obj.content = '@everyone ' + $Mensagem; $obj.allowed_mentions = @{ parse = @('everyone') } }
$corpo = [Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Compress -Depth 4))
Invoke-RestMethod -Uri $u -Method Post -ContentType 'application/json; charset=utf-8' -Body $corpo | Out-Null