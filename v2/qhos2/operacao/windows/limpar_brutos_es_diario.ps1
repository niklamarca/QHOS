# Limpeza diaria dos brutos do ES (Windows chama o WSL). Alvo da tarefa QHOS-Limpar-Brutos-ES (todo dia as 19:30).
# Uso: powershell -NoProfile -ExecutionPolicy Bypass -File limpar_brutos_es_diario.ps1 [-Simular] [-Dataset <pasta>]
#   padrao:   tira de ~/qhos-v2-data/capturas/ as capturas do ES fechadas ha 7 dias ou mais, SO se o .tar.xz delas no
#             DataSet (pasta do dia) for relido e conferir com o manifesto e existir uma segunda copia local do mesmo
#             .tar.xz fora do OneDrive (a da coleta multiativo ou ~/qhos-v2-data/dataset_copia_local, criada se faltar).
#             O DataSet nunca e alterado. Captura sem copia no DataSet (ex.: ES de 9 a 20/11) fica e vai ao log.
#             Ate 1/1/2027 fica toda captura ESZ6 com primeiro quadro (data de NY) de 26/10 a 20/11 (Etapa 3).
#   -Simular: mostra o que faria, sem apagar nada
# Nunca liga o WSL: se estiver desligado, so registra e sai. O .py roda com o hash conferido antes, como o heartbeat.
# Codigo de saida: 0 ok ou nada a fazer, 1 divergencia (o bruto NUNCA e apagado nesse caso), 2 falha de ambiente.
param([switch]$Simular, [string]$Dataset = 'C:\Users\usuario\OneDrive\Desktop\DataSet')
$ErrorActionPreference = 'Continue'
$dir = 'C:\Users\usuario\Downloads\Rithmic\avisos_discord'
$script = "$dir\limpar_brutos_es.py"
$hashEsperado = '07fd9920d079d49211b31a894eb738c9d7717988b4a0c128ade67e05d7078f3c'
$log = "$dir\limpar_brutos_es.log"
function Registrar($texto) { Add-Content -Path $log -Value ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $texto) -Encoding UTF8 }

$run = ((& wsl.exe --list --running 2>$null) -join '') -replace "`0", ''
if ($run -notmatch 'Ubuntu') { Registrar 'WSL desligado: nada a fazer'; exit 0 }
if (-not (Test-Path $script) -or ((Get-FileHash $script -Algorithm SHA256).Hash.ToLower() -ne $hashEsperado)) {
    Registrar 'ERRO: limpar_brutos_es.py ausente ou com hash diferente; nada foi executado'
    exit 2
}
if (-not (Test-Path -LiteralPath $Dataset -PathType Container)) {
    Registrar ('ERRO: pasta do DataSet nao existe: {0}; nada foi executado' -f $Dataset)
    exit 2
}
$py = '/mnt/c/' + ($script.Substring(3) -replace '\\', '/')
$ds = '/mnt/c/' + ($Dataset.Substring(3) -replace '\\', '/')
$args2 = @('--dataset', $ds)
if (-not $Simular) { $args2 += '--executar' }

$saida = (& wsl.exe -d Ubuntu -u usuario -- python3 -I $py @args2 2>&1) -join "`n"
$codigo = $LASTEXITCODE
try {
    $r = $saida | ConvertFrom-Json -ErrorAction Stop
    $linha = 'limpar{0} rc={1}: removidas={2} prontas={3} no_prazo={4} protegidas={10} sem_copia={5} divergentes={6} copias_criadas={7} liberado={8} GiB parada={9}' -f
        $(if ($Simular) { ' (simulacao)' } else { '' }), $codigo, @($r.removidas).Count, @($r.prontas).Count, @($r.no_prazo).Count,
        @($r.sem_copia).Count, @($r.divergentes).Count, @($r.copias_criadas).Count, [math]::Round([double]$r.bytes_liberados / 1GB, 2), $r.parada,
        @($r.protegidas).Count
    Registrar $linha
    foreach ($id in @($r.removidas)) { Registrar ('  removida: {0}' -f $id) }
    foreach ($p in @($r.protegidas)) { Registrar ('  PROTEGIDA (Etapa 3, ate {0}) {1}: {2} {3}' -f $r.protecao.liberacao, $p.id, $p.motivo, $p.dia_ny) }
    foreach ($s in @($r.sem_copia)) { Registrar ('  SEM COPIA NO DATASET (bruto mantido) {0}: {1}' -f $s.id, $s.motivo) }
    foreach ($d in @($r.divergentes)) { Registrar ('  DIVERGENTE (bruto mantido) {0}: {1}' -f $d.id, $d.motivo) }
} catch {
    Registrar ('limpar rc={0}: saida nao reconhecida: {1}' -f $codigo, $saida.Substring(0, [math]::Min(300, $saida.Length)))
}
exit $codigo
