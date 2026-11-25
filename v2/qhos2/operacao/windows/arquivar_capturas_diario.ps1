# Dataset diario da V2 (Windows chama o WSL). Alvo da tarefa QHOS-Arquivar-Capturas (todo dia as 19:30).
# Uso: powershell -NoProfile -ExecutionPolicy Bypass -File arquivar_capturas_diario.ps1 [-Simular] [-Verificar] [-LiberarDisco] [-Dataset <pasta>]
#   padrao:        le e confere TODA captura fechada ha mais de 1 h que ainda nao esta no DataSet, cria
#                  DataSet\<AAAA-MM-DD>_<id> (dia da captura, horario de Brasilia) e insere a captura em xz, conferida
#                  pelo hash do manifesto. O DataSet e permanente: nada que entra nele e apagado. Os brutos em
#                  capturas\ tambem NAO sao apagados.
#   -Simular:      mesma coisa sem gravar nada
#   -Verificar:    confere os hashes de todos os arquivos ja feitos (verificar --rapido)
#   -LiberarDisco: tambem tira de capturas\ os brutos fechados ha mais de 7 dias, depois de reler o arquivo no
#                  DataSet. Irreversivel para o bruto: so com decisao do autor; a tarefa agendada nao usa.
# Selo: os pregoes de 9 a 20/11/2026 (janela de teste do pre-registro, "sem olhar") entram em DataSet\SELADO_ate_2026-11-21
#       e nao podem ser restaurados antes de 21/11; nessa data o proprio script os move para a raiz do DataSet.
# Nunca liga o WSL: se estiver desligado, so registra e sai. O .py roda com o hash conferido antes, como o heartbeat.
# Codigo de saida: 0 ok ou nada a fazer, 1 divergencia (o original NUNCA e apagado nesse caso), 2 falha de ambiente.
param([switch]$Simular, [switch]$Verificar, [switch]$LiberarDisco, [string]$Dataset = 'C:\Users\usuario\OneDrive\Desktop\DataSet')
$ErrorActionPreference = 'Continue'
$dir = 'C:\Users\usuario\Downloads\Rithmic\avisos_discord'
$script = "$dir\arquivar_capturas.py"
$hashEsperado = 'bcfafdaccc273ca750695a2bc7d7c4460b106643a3d1ab8d1704482a75db6a37'
$log = "$dir\arquivar_capturas.log"
$seloDe = '2026-11-09'; $seloAte = '2026-11-20'; $seloLibera = '2026-11-21'
function Registrar($texto) { Add-Content -Path $log -Value ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $texto) -Encoding UTF8 }

$run = ((& wsl.exe --list --running 2>$null) -join '') -replace "`0", ''
if ($run -notmatch 'Ubuntu') { Registrar 'WSL desligado: nada a fazer'; exit 0 }
if (-not (Test-Path $script) -or ((Get-FileHash $script -Algorithm SHA256).Hash.ToLower() -ne $hashEsperado)) {
    Registrar 'ERRO: arquivar_capturas.py ausente ou com hash diferente; nada foi executado'
    exit 2
}
if (-not (Test-Path -LiteralPath $Dataset -PathType Container)) {
    Registrar ('ERRO: pasta do DataSet nao existe: {0}; nada foi executado' -f $Dataset)
    exit 2
}
$py = '/mnt/c/' + ($script.Substring(3) -replace '\\', '/')
$ds = '/mnt/c/' + ($Dataset.Substring(3) -replace '\\', '/')
if ($Verificar) { $args2 = @('verificar', '--rapido', '--destino', $ds) }
else {
    $args2 = @('arquivar', '--destino', $ds, '--selar', $seloDe, $seloAte, '--liberar-em', $seloLibera)
    if (-not $LiberarDisco) { $args2 += '--manter-original' }
    if (-not $Simular) { $args2 += '--executar' }
}

$saida = (& wsl.exe -d Ubuntu -u usuario -- python3 -I $py @args2 2>&1) -join "`n"
$codigo = $LASTEXITCODE
if ($Verificar) {
    Registrar ('verificar rc={0}: {1}' -f $codigo, (($saida -split "`n" | Select-Object -Last 1)))
} else {
    try {
        $r = $saida | ConvertFrom-Json -ErrorAction Stop
        $linha = 'arquivar{0} rc={1}: publicadas={2} removidas={3} liberadas={4} elegiveis={5} divergentes={6} economia={7} GiB parada={8}' -f
            $(if ($Simular) { ' (simulacao)' } else { '' }), $codigo, @($r.arquivadas).Count, @($r.removidas).Count,
            @($r.liberadas).Count, @($r.elegiveis).Count, @($r.divergentes).Count, [math]::Round([double]$r.economia_bytes / 1GB, 2), $r.parada
        Registrar $linha
        foreach ($id in @($r.arquivadas)) { Registrar ('  publicada: {0}' -f $id) }
        foreach ($id in @($r.elegiveis)) { if ($Simular) { Registrar ('  seria publicada: {0}' -f $id) } }
        foreach ($d in @($r.divergentes)) { Registrar ('  DIVERGENTE {0}: {1}' -f $d.id, $d.motivo) }
    } catch {
        Registrar ('arquivar rc={0}: saida nao reconhecida: {1}' -f $codigo, $saida.Substring(0, [math]::Min(300, $saida.Length)))
    }
}
exit $codigo
