# Heartbeat da V2 (Windows + agendador de captura no WSL) em formato de painel. Somente leitura.
# Uso: powershell -NoProfile -ExecutionPolicy Bypass -File heartbeat_6h.ps1 [-DryRun] [-SoAlerta]
#   sem -SoAlerta: posta sempre (heartbeat de 6 em 6 h).
#   com -SoAlerta: posta so se houver item vermelho novo (tarefa de 30 em 30 min).
#   -LeituraV2 <arquivo.json>: so com -DryRun; usa o JSON no lugar do leitor (testar cenarios sem mexer na V2).
# A V2 e lida pelo heartbeat_v2_leitor.py (mesma pasta), com o hash conferido antes de rodar.
# Cada item do painel vem de uma medida; o que nao foi medido aparece em branco, nunca em verde.
# So vira alarme o que e verdade agora (pedido do autor, 31/10): pregao interrompido com o sistema ja pronto para o
# proximo nao e incidente; NTP so falta se nenhum servidor do ntp.br responde; KeepAwake parado so conta se o plano
# de energia deixa o PC suspender; codigo "no GitHub" vale em qualquer ramo do repositorio, nao so na v2.
# Nunca religa o WSL nem o agendador: se estiverem parados, so informa. Nao escreve nada no WSL.
param([switch]$DryRun, [switch]$SoAlerta, [string]$LeituraV2 = '')
if ($LeituraV2 -and -not $DryRun) { 'use -LeituraV2 so junto com -DryRun'; exit 2 }
$ErrorActionPreference = 'Continue'
$dir  = 'C:\Users\usuario\Downloads\Rithmic\avisos_discord'
$repo = 'C:\Users\usuario\OneDrive\Desktop\QHOSv2'
$estadoArq = "$dir\estado_heartbeat.json"
$agora = Get-Date
$br = [Globalization.CultureInfo]::GetCultureInfo('pt-BR')
$inv = [Globalization.CultureInfo]::InvariantCulture
$COR = @{ -1 = [char]::ConvertFromUtf32(0x26AA) + [char]0xFE0F;0 = [char]::ConvertFromUtf32(0x1F7E2); 1 = [char]::ConvertFromUtf32(0x1F7E1); 2 = [char]::ConvertFromUtf32(0x1F534) }
$SIRENE = [char]::ConvertFromUtf32(0x1F6A8)
$LARG = 34

# ---- itens do painel ----
$SECOES = [ordered]@{ INFRA = 'INFRAESTRUTURA'; PROC = 'PROCESSOS'; FLUXO = 'FLUXO DE DADOS'; RELOGIO = 'RELÓGIO'; INTEG = 'INTEGRIDADE'; SEG = 'SEGURANÇA'; CONFIG = 'CONFIGURAÇÃO'; MERCADO = 'MERCADO'; CICLO = 'CICLO COMPLETO' }
$itens = New-Object System.Collections.ArrayList
function Item($secao, $nivel, $texto = '', $acao = '') { [void]$itens.Add([pscustomobject]@{ Secao = $secao; Nivel = $nivel; Texto = $texto; Acao = $acao }) }
function NivelSecao($secao) { $n = @($itens | Where-Object { $_.Secao -eq $secao } | ForEach-Object { $_.Nivel }); if ($n.Count -eq 0) { -1 } else { ($n | Measure-Object -Maximum).Maximum } }
function Linha($rotulo, $valor) { $v = [string]$valor; $esp = [math]::Max(1, $LARG - $rotulo.Length - $v.Length); $rotulo + (' ' * $esp) + $v }
function Centro($t) { $e = [math]::Max(0, $LARG - $t.Length); [int]$a = [math]::Floor($e / 2); '║' + (' ' * $a) + $t + (' ' * ($e - $a)) + '║' }
function Num($n) { ([double]$n).ToString('N0', $br) }
function Dec($n, $casas = 1) { ([double]$n).ToString("N$casas", $br) }
function Local($iso, $fmt = 'dd/MM HH:mm') { if (-not $iso) { return '?' }; try { ([datetimeoffset]::Parse($iso, $inv)).ToLocalTime().ToString($fmt) } catch { $iso } }
function DeNs($ns, $fmt = 'HH:mm:ss.fff') { ([datetimeoffset]::FromUnixTimeMilliseconds([int64]([decimal]$ns / 1000000))).ToLocalTime().ToString($fmt) }
function Ms($ms) { $a = [math]::Abs($ms); if ($a -ge 120000) { (Num ($ms / 60000)) + ' min' } elseif ($a -ge 1000) { (Dec ($ms / 1000)) + ' s' } else { (Num $ms) + ' ms' } }

# ---- Windows ----
$pausaWsl = $false
$pf = "$dir\pausa_wsl.json"
if (Test-Path $pf) { try { foreach ($w in (Get-Content $pf -Raw -Encoding UTF8 | ConvertFrom-Json)) { if ($agora -ge [datetime]$w.inicio -and $agora -le [datetime]$w.fim) { $pausaWsl = $true } } } catch {} }
if ($pausaWsl -and $SoAlerta) { exit 0 }
$LivreC = [math]::Round((Get-PSDrive C).Free / 1GB, 0)
$TotalC = [math]::Round(((Get-PSDrive C).Used + (Get-PSDrive C).Free) / 1GB, 0)
if ($LivreC -lt 20) { Item INFRA 2 "disco C com $LivreC GiB livres" 'libere espaço no disco C' } elseif ($LivreC -lt 100) { Item INFRA 1 "disco C com $LivreC GiB livres" } else { Item INFRA 0 }
# O disco do WSL é um arquivo .vhdx dentro do C: ele diz ter 1 TB, mas só cresce até o livre do C.
$vhdxGiB = $null
try { $base = (Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' | ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object { $_.DistributionName -eq 'Ubuntu' }).BasePath; $vhdxGiB = [math]::Round((Get-Item (Join-Path $base 'ext4.vhdx') -ErrorAction Stop).Length / 1GB, 0) } catch {}
# O KeepAwake so sobe no login. Com o plano de energia sem suspender e sem hibernar na tomada, ele nao faz falta.
function IndiceAC($cfg) {
    $h = [regex]::Matches(((& powercfg /query SCHEME_CURRENT SUB_SLEEP $cfg 2>$null) -join "`n"), '0x[0-9a-fA-F]{8}')
    if ($h.Count -ge 2) { [Convert]::ToInt64($h[$h.Count - 2].Value, 16) } else { $null }  # penultimo = tomada (AC)
}
$nuncaSuspende = ((IndiceAC STANDBYIDLE) -eq 0) -and ((IndiceAC HIBERNATEIDLE) -eq 0)
foreach ($t in 'QHOS-V2-Linux', 'QHOS-V2-KeepAwake') {
    $st = try { [string](Get-ScheduledTask -TaskName $t -ErrorAction Stop).State } catch { 'ausente' }
    if ($st -eq 'Running') { Item INFRA 0 }
    elseif ($t -eq 'QHOS-V2-KeepAwake' -and $st -ne 'ausente' -and $nuncaSuspende) { Item INFRA 0 }
    else { Item INFRA 1 "tarefa $t $st" }
}
$nt = Get-Service -Name 'NetTimeSvc' -ErrorAction SilentlyContinue
$freq = try { [string](Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Subjective Software\NetTime' -ErrorAction Stop).SyncFreq } catch { '?' }
if ($nt -and $nt.Status -eq 'Running') { Item RELOGIO 0 } else { Item RELOGIO 1 'NetTime parado' 'abra o NetTime e confira a sincronia' }
# Desvio contra o NTP: o primeiro servidor do ntp.br que responder (o a.st1 parou de responder em 30/10).
$ntp = $null; $ntpServ = $null
foreach ($s in 'a.st1.ntp.br', 'b.st1.ntp.br', 'a.ntp.br', 'b.ntp.br', 'c.ntp.br') {
    $sc = (& w32tm /stripchart /computer:$s /samples:1 /dataonly 2>$null) -join ' '
    if ($sc -match ',\s*([+-]\d+[.,]\d+)s') { $ntp = [double]::Parse(($Matches[1] -replace ',', '.'), $inv) * 1000; $ntpServ = $s; break }
}
if ($ntp -eq $null) { Item RELOGIO 1 'NTP sem resposta em nenhum servidor do ntp.br (relógio não medido)' }
elseif ([math]::Abs($ntp) -ge 1000) { Item RELOGIO 2 "relógio do Windows a $(Ms $ntp) do NTP" 'force a sincronia no NetTime' }
elseif ([math]::Abs($ntp) -ge 100) { Item RELOGIO 1 "relógio do Windows a $(Ms $ntp) do NTP" }
else { Item RELOGIO 0 }
if (Test-Path "$env:LOCALAPPDATA\QHOS\credentials") { Item SEG 2 'pasta de senhas no Windows (AppData\Local\QHOS\credentials)' 'apague a pasta de senhas do Windows' }

# git: a pasta QHOSv2 e o código que está rodando estão no GitHub, e o código instalado é o último do git?
# Vale qualquer ramo do repositório que contenha o commit (v2, coleta-multi...). Sem rede: usa as referências locais
# do GitHub (refs/remotes/origin/*), que o push e o fetch desta pasta atualizam; a tarefa roda sem login, sem a
# credencial do GitHub.
$git = [bool](Get-Command git -ErrorAction SilentlyContinue)
$sujos = $null; $publicado = $null; $codigoGit = $null; $ramoHead = $null; $headCurto = $null
function RamosNoGitHub($sha) {
    # Ramos do GitHub (origin) que contêm o commit, sem o origin/HEAD. Medido=$false quando o próprio git falhou
    # (repositório ilegível): aí não se afirma nada. Commit desconhecido aqui (rc 129) = em ramo nenhum.
    $r = @(& git -C $repo for-each-ref --contains $sha --format '%(refname:lstrip=3)' refs/remotes/origin 2>$null | Where-Object { $_ -and $_ -ne 'HEAD' })
    $rc = $LASTEXITCODE
    [pscustomobject]@{ Medido = ($rc -eq 0 -or $rc -eq 129); Ramos = @(if ($rc -eq 0) { $r }) }  # @(): vazio fica vazio
}
function NomeDoRamo($ramos) { if ($ramos -contains 'v2') { 'v2' } else { @($ramos | Sort-Object)[0] } }
function Curto($sha) { ([string]$sha).Substring(0, [math]::Min(7, ([string]$sha).Length)) }
if ($git -and (Test-Path "$repo\.git")) {
    $head = (& git -C $repo rev-parse HEAD 2>$null)
    if ($LASTEXITCODE -eq 0 -and $head) {  # só com o repositório legível
        $sujos = @(& git -C $repo status --porcelain 2>$null).Count
        $headCurto = Curto $head
        $noGitHub = RamosNoGitHub $head
        if ($noGitHub.Medido) {
            $publicado = @($noGitHub.Ramos).Count -gt 0
            if ($publicado) { $ramoHead = NomeDoRamo $noGitHub.Ramos }
        }
        $codigoGit = (& git -C $repo log -1 --format=%H -- qhos2/src qhos2/pyproject.toml 2>$null)
    }
}
if ($sujos -eq $null) { Item CONFIG 1 'pasta QHOSv2 não conferida (git ausente ou repositório ilegível)' }
else {
    if ($sujos -gt 0) { Item CONFIG 1 "QHOSv2 com $sujos alteração(ões) sem commit" 'faça commit e push da QHOSv2' } else { Item CONFIG 0 }
    if ($publicado -eq $false) { Item CONFIG 1 "commit da QHOSv2 ($headCurto) não está em nenhum ramo do GitHub" 'faça push da QHOSv2' }
    elseif ($publicado) { Item CONFIG 0 }
}

# ---- V2 (WSL) ----
$leitor = "$dir\heartbeat_v2_leitor.py"
$hashEsperado = '6e0b88c5d9828667223d8065b18e715329dac7a0526c52c380b9d52b620b4152'
$cap = $null; $offWsl = $null; $incWsl = $null; $nrestarts = $null; $delta = 0
$prev = $null
if (Test-Path $estadoArq) { try { $prev = Get-Content $estadoArq -Raw -Encoding UTF8 | ConvertFrom-Json } catch {} }
$run = ((& wsl.exe --list --running 2>$null) -join '') -replace "`0", ''
if ($LeituraV2) {  # teste de cenario (so com -DryRun): a leitura da V2 vem do arquivo
    try { $cap = Get-Content $LeituraV2 -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop } catch { $cap = $null }
    if (-not $cap -or -not $cap.agenda) { Item INFRA 2 'leitura da V2 falhou (saída vazia ou inválida)' 'rode o heartbeat com -DryRun e veja o erro'; $cap = $null }
} elseif ($run -notmatch 'Ubuntu') {
    Item INFRA 2 'WSL desligado: V2 e agendador parados' 'ligue o PC/WSL e inicie o agendador no tmux'
} elseif (-not (Test-Path $leitor) -or ((Get-FileHash $leitor -Algorithm SHA256).Hash.ToLower() -ne $hashEsperado)) {
    Item SEG 2 'leitor da V2 ausente ou com hash diferente' 'confira heartbeat_v2_leitor.py contra o git'
} else {
    foreach ($k in 1..3) {
        $t0 = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        $w = (& wsl.exe -d Ubuntu -u usuario -- date +%s%3N 2>$null)
        $t1 = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        if ($w -match '^\d+$' -and ($incWsl -eq $null -or ($t1 - $t0) / 2 -lt $incWsl)) { $incWsl = [int](($t1 - $t0) / 2); $offWsl = [int64]$w - [int64](($t0 + $t1) / 2) }
    }
    $pyWsl = '/mnt/c/' + ($leitor.Substring(3) -replace '\\', '/')
    $j = Start-Job -ScriptBlock { param($p) & wsl.exe -d Ubuntu -u usuario -- python3 -I $p 2>$null } -ArgumentList $pyWsl
    $out = $null
    if (Wait-Job $j -Timeout 40) { $out = (Receive-Job $j) -join '' } else { Stop-Job $j }
    Remove-Job $j -Force -ErrorAction SilentlyContinue
    try { $cap = $out | ConvertFrom-Json -ErrorAction Stop } catch { $cap = $null }
    if (-not $cap -or -not $cap.agenda) { Item INFRA 2 'leitura da V2 falhou (saída vazia ou inválida)' 'rode o heartbeat com -DryRun e veja o erro'; $cap = $null }
}
if ($offWsl -ne $null) {
    $fora = [math]::Abs($offWsl) - $incWsl
    if ($fora -gt 2000) { Item RELOGIO 2 "relógio do WSL a $(Ms $offWsl) do Windows" 'reinicie o WSL fora da janela de captura' }
    elseif ($fora -gt 500) { Item RELOGIO 1 "relógio do WSL a $(Ms $offWsl) do Windows" }
    else { Item RELOGIO 0 }
}

$ag = $null; $av = $null; $capturando = $false
if ($cap) {
    $ag = $cap.agenda; $av = $cap.ao_vivo; $capturando = ($ag.estado -eq 'CAPTURANDO')
    $livreWsl = [math]::Round([math]::Min([double]$cap.disco_livre_gib, [double]$LivreC), 0)
    if ($livreWsl -lt 20) { Item INFRA 2 "WSL só pode crescer $livreWsl GiB" 'libere espaço no disco C' } elseif ($livreWsl -lt 50) { Item INFRA 1 "WSL só pode crescer $livreWsl GiB" } else { Item INFRA 0 }
    if ($cap.keepalive) { Item INFRA 0 } else { Item INFRA 1 'marcador de vida do WSL ausente' }
    foreach ($u in @(@('brain', $cap.brain), @('coletor', $cap.coletor))) {
        if ($u[1].estado -eq 'active/running') { Item PROC 0 } else { Item PROC 2 "serviço $($u[0]): $($u[1].estado)" "religue o qhos2-$($u[0]) (systemctl --user start)" }
    }
    $nrestarts = [int]$cap.brain.nrestarts + [int]$cap.coletor.nrestarts
    if ($prev -and $prev.nrestarts -ne $null) { $delta = $nrestarts - [int]$prev.nrestarts }
    if ($delta -gt 3) { Item PROC 1 "serviços religados $delta vezes desde o último aviso" }
    if (-not $cap.agendador_pid) { Item PROC 2 'agendador parado' 'religue o qhos2-agenda (systemctl --user start qhos2-agenda) ou o do tmux' } else { Item PROC 0 }
    switch ($ag.estado) {
        'QUARENTENA' { Item PROC 2 "agendador em QUARENTENA ($($ag.motivo))" 'leia o runbook antes de religar o agendador' }
        'PARADO' { Item PROC 2 "agendador PARADO ($($ag.motivo))" 'religue o qhos2-agenda (systemctl --user start qhos2-agenda) ou o do tmux' }
        'CALENDARIO_ESGOTADO' { Item CONFIG 2 'calendário esgotado' 'gere o próximo calendário e reinicie o agendador' }
    }
    $g = $cap.guarda.estado
    if ($capturando) { if ($g -eq 'EM_ANDAMENTO') { Item PROC 0 } else { Item PROC 2 "guarda em $g durante a captura" 'confira a captura no tmux' } }
    elseif ($g -ne 'LIVRE') { Item PROC 2 "guarda em $g ($($cap.guarda.motivo))" 'libere a guarda à mão (qhos2.operacao) após conferir' }

    if ($capturando) {
        if (-not $av -or $av.erro) { Item FLUXO 2 'arquivo da captura ilegível' 'confira a captura no tmux' }
        else {
            $fr = [int64]$av.frescor_ms
            if ($fr -gt 600000) { Item FLUXO 2 "captura sem dados há $(Ms $fr)" 'confira a conexão da captura no tmux' } elseif ($fr -gt 60000) { Item FLUXO 1 "captura sem dados há $(Ms $fr)" } else { Item FLUXO 0 }
            $restante = 0; try { $restante = [math]::Max(0, (([datetimeoffset]::Parse($ag.janela_fim, $inv)) - [datetimeoffset]::Now).TotalMinutes) } catch {}
            $proj = [double]$av.bytes + [double]$av.bytes_min * $restante
            if ($proj -gt [double]$cap.teto_bytes) { Item FLUXO 2 "arquivo do dia vai a $(Dec ($proj / 1GB)) GiB (teto $(Dec ($cap.teto_bytes / 1GB) 0) GiB)" 'avise: a captura vai parar no teto' } elseif ($proj -gt 0.75 * [double]$cap.teto_bytes) { Item FLUXO 1 "arquivo do dia perto do teto ($(Dec ($proj / 1GB)) GiB)" } else { Item FLUXO 0 }
            if ([int]$av.lacunas -gt 0) { Item INTEG 2 "$($av.lacunas) quadro(s) faltando na sequência" 'avise: perda de quadros na gravação' } else { Item INTEG 0 }
            if ([int]$av.duplicados -gt 0) { Item INTEG 2 "$($av.duplicados) quadro(s) fora de ordem ou repetidos" 'avise: quadros repetidos na gravação' } else { Item INTEG 0 }
            if ([int]$av.invalidos -gt 0) { Item INTEG 2 "$($av.invalidos) linha(s) inválida(s) no arquivo" 'avise: arquivo de quadros corrompido' }
            $livro = [int]$av.por_template_min.'160'; $neg = [int]$av.por_template_min.'150'
            if ($livro -gt 0 -and $neg -gt 0) { Item MERCADO 0 } elseif ($fr -le 60000) { Item MERCADO 1 "último minuto: livro $livro, negócios $neg" }
        }
    }
    $up = $cap.ultima_prova
    # Agendador esperando de verdade: o estado AGUARDANDO é regravado a cada 60 s no máximo. Velho com o processo
    # de pé há mais de 10 min = travado. Processo novo (reinício, espera da guarda) ganha 10 min para escrever.
    # Idade medida só com as duas horas presentes; estado "do futuro" (mais de 2 min) não é medida.
    $idadeEstado = $null
    if ($cap.wsl_agora_ns -and $ag.atualizado_utc) {
        try { $idadeEstado = ([double]$cap.wsl_agora_ns / 1e9) - ([datetimeoffset]::Parse($ag.atualizado_utc, $inv)).ToUnixTimeSeconds() } catch {}
        if ($idadeEstado -ne $null -and $idadeEstado -lt -120) { $idadeEstado = $null }
    }
    $processoNovo = $cap.agendador_idade_s -ne $null -and [double]$cap.agendador_idade_s -le 600
    if ($ag.estado -eq 'AGUARDANDO' -and $cap.agendador_pid -and $idadeEstado -ne $null -and $idadeEstado -gt 600 -and -not $processoNovo) {
        # texto fixo: a idade exata vai no bloco CAPTURA (assim o -SoAlerta não repete o mesmo alerta antes de 3 h)
        Item PROC 2 'agendador sem atualizar o estado há mais de 10 min' 'confira o agendador (tmux ou systemctl --user status qhos2-agenda)'
    }
    # Pregão interrompido só é incidente enquanto o sistema não voltou: agendador vivo e esperando (guarda LIVRE,
    # estado recente) ou gravando (guarda EM_ANDAMENTO) um dia depois do pregão da prova. Depois disso é história:
    # fica no bloco ETAPA 1. Só vale para "interrompido": dia perdido sem captura (pulado_*, falhou_*) segue vermelho.
    $proximoDia = if ($ag.estado -eq 'CAPTURANDO') { [string]$ag.dia } else { [string]$ag.proximo_dia }
    $esperando = $ag.estado -eq 'AGUARDANDO' -and $g -eq 'LIVRE' -and (($idadeEstado -ne $null -and $idadeEstado -le 600) -or $processoNovo)
    $gravando = $ag.estado -eq 'CAPTURANDO' -and $g -eq 'EM_ANDAMENTO'
    $sistemaPronto = $up -and $up.resultado -eq 'interrompido' -and $cap.agendador_pid -and ($esperando -or $gravando) -and
                     ([string]$up.dia -match '^\d{4}-\d{2}-\d{2}$') -and $proximoDia -and ($proximoDia -gt [string]$up.dia)
    if ($up) {
        if ($up.resultado -eq 'capturado' -and $up.verificada -eq $true) { Item CICLO 0; Item INTEG 0 }
        elseif ($sistemaPronto) {
            Item CICLO 0
            if ($up.verificada -eq $false) { Item INTEG 2 "captura de $($up.dia) não verificada" 'confira a captura do dia (catalogo)' }
            elseif ($up.verificada -eq $true) { Item INTEG 0 }
        }
        else {
            Item CICLO 2 "pregão $($up.dia): $($up.resultado)$(if ($up.erro) { " ($($up.erro))" })" 'leia provas.jsonl e o runbook do dia'
            if ($up.verificada -eq $false) { Item INTEG 2 "captura de $($up.dia) não verificada" }
        }
    }
    if (@($cap.portas_expostas).Count -gt 0) { Item SEG 2 "portas abertas para fora do WSL: $(@($cap.portas_expostas) -join ', ')" 'feche as portas expostas' } else { Item SEG 0 }
    if (@($cap.arquivos_de_senha).Count -gt 0) { Item SEG 2 "$(@($cap.arquivos_de_senha).Count) arquivo(s) de senha no WSL" 'apague os arquivos de senha (regra R6)' } else { Item SEG 0 }
    if ($cap.dados_modo -ne '0o700') { Item SEG 1 "pasta de dados com permissão $($cap.dados_modo)" } else { Item SEG 0 }
    $ins = $cap.instalacao
    if ($ins.versao_instalada -ne $ins.versao_release) { Item CONFIG 2 "instalada $($ins.versao_instalada), RELEASE diz $($ins.versao_release)" 'confira a instalação da V2' } else { Item CONFIG 0 }
    if ($codigoGit -and $ins.commit) {
        & git -C $repo merge-base --is-ancestor $codigoGit $ins.commit 2>$null
        if ($LASTEXITCODE -eq 0) { Item CONFIG 0 }
        elseif ($LASTEXITCODE -eq 1) { Item CONFIG 1 'há código novo no git ainda não instalado' }
        # outro código (128: commit instalado desconhecido aqui): não medido; o item do GitHub abaixo já diz
    }
    # o código que está rodando também tem de estar no GitHub (em qualquer ramo): a versão instalada e o coletor
    if ($git -and $sujos -ne $null) {  # só com o repositório legível
        foreach ($vivo in @(@('versão instalada', $ins.commit), @('coletor instalado', $cap.coleta_commit))) {
            if (-not $vivo[1]) { continue }  # não lido: fica sem medida
            $noGitHub = RamosNoGitHub $vivo[1]
            if (-not $noGitHub.Medido) { continue }
            if (@($noGitHub.Ramos).Count) { Item CONFIG 0 }
            else { Item CONFIG 1 "$($vivo[0]) ($(Curto $vivo[1])) não está em nenhum ramo do GitHub" 'faça push do commit instalado' }
        }
    }
    # Calendário: renova sozinho com o serviço contínuo do systemd, ou com esse serviço habilitado para assumir no
    # fim do calendário do tmux (timer qhos2-garantir). Contrato: só o serviço contínuo troca sozinho; o do tmux
    # grava o símbolo fixo até o fim do calendário dele, então ali o aviso de vencimento vale.
    $renovaCalendario = ($cap.agendador_modo -eq 'continuo') -or ($cap.agenda_unit_habilitada -eq $true)
    $renovaContrato = $cap.agendador_modo -eq 'continuo'
    $calFim = $null
    if ($cap.calendario_valido_ate) { try { $calFim = ([datetimeoffset]::Parse($cap.calendario_valido_ate, $inv)).AddHours(-12).ToLocalTime().Date } catch {} }
    if ($calFim) { $dc = ($calFim - $agora.Date).Days; if ($dc -lt 7 -and -not $renovaCalendario) { Item CONFIG 1 "calendário acaba em $dc dia(s)" 'gere o próximo calendário' } else { Item CONFIG 0 } }
    $venc = $null
    if ($cap.vencimento) { try { $venc = [datetime]::ParseExact([string]$cap.vencimento, 'yyyyMMdd', $inv) } catch {} }
    if ($venc) { $dv = ($venc - $agora.Date).Days; if ($dv -le 14 -and -not $renovaContrato) { Item CONFIG 1 "contrato $($cap.simbolo) vence em $dv dia(s)" 'troque o símbolo do agendador para o próximo vencimento' } else { Item CONFIG 0 } }
}

# ---- painel ----
$nivelGeral = ($itens | ForEach-Object { $_.Nivel } | Measure-Object -Maximum).Maximum
$vermelhos = @($itens | Where-Object { $_.Nivel -eq 2 })
$amarelos = @($itens | Where-Object { $_.Nivel -eq 1 })
$geralTxt = @{ 0 = 'SAUDÁVEL'; 1 = 'ATENÇÃO'; 2 = 'PROBLEMA' }[[int]$nivelGeral]
$meses = 'JAN','FEV','MAR','ABR','MAI','JUN','JUL','AGO','SET','OUT','NOV','DEZ'
$topo = New-Object System.Collections.ArrayList
[void]$topo.Add('╔' + ('═' * $LARG) + '╗')
[void]$topo.Add((Centro 'QHOS V2 — SISTEMA'))
[void]$topo.Add((Centro ("{0:dd} {1} {0:yyyy HH:mm} BRT" -f $agora, $meses[$agora.Month - 1])))
[void]$topo.Add('╠' + ('═' * $LARG) + '╣')
[void]$topo.Add((Linha 'GERAL' "$($COR[[int]$nivelGeral]) $geralTxt"))
[void]$topo.Add('')
foreach ($s in $SECOES.Keys) { [void]$topo.Add((Linha $SECOES[$s] $COR[[int](NivelSecao $s)])) }

$blocos = New-Object System.Collections.ArrayList   # [prioridade, linhas]; os de prioridade maior saem primeiro se a mensagem passar do limite
if ($cap) {
    $b = @("CAPTURA  $($cap.simbolo)")
    $pidTxt = if ($cap.agendador_pid) { "PID $($cap.agendador_pid)" } else { 'SEM PROCESSO' }
    $b += Linha 'Agendador' "$($ag.estado) ($pidTxt)"
    if ($capturando -and $av -and -not $av.erro) {
        $b += Linha 'Janela até' (Local $ag.janela_fim 'HH:mm')
        $b += Linha 'Quadros/min' (Num $av.quadros_min)
        $b += Linha '  Livro (DBO)' (Num ([int]$av.por_template_min.'160'))
        $b += Linha '  Negócios' (Num ([int]$av.por_template_min.'150'))
        $b += Linha 'Último quadro' (DeNs $av.ultimo_ns)
        $b += Linha 'Frescor' (Ms $av.frescor_ms)
        $b += Linha 'Maior pausa (1 min)' (Ms $av.maior_pausa_ms)
        $b += Linha 'Lacunas' $av.lacunas
        $b += Linha 'Duplicados' $av.duplicados
        $b += Linha 'Arquivo agora / fim' ("$(Dec ($av.bytes / 1GB)) / $(Dec ($proj / 1GB)) GiB")
    } elseif ($ag.estado -eq 'AGUARDANDO') {
        $b += Linha 'Próxima janela' (Local $ag.proxima_janela)
        if ($idadeEstado -ne $null -and $idadeEstado -gt 600) { $b += Linha 'Estado gravado há' (Ms ($idadeEstado * 1000)) }
    }
    $b += Linha 'Guarda' $cap.guarda.estado
    [void]$blocos.Add(@(0, $b))
    $b = @('PROCESSOS')
    $b += Linha 'Brain' $(if ($cap.brain.estado -eq 'active/running') { 'ativo' } else { $cap.brain.estado })
    $b += Linha 'Coletor' $(if ($cap.coletor.estado -eq 'active/running') { 'ativo' } else { $cap.coletor.estado })
    $b += Linha 'Religamentos' "$nrestarts$(if ($delta -gt 0) { " (+$delta)" })"
    [void]$blocos.Add(@(2, $b))
}
$b = @('RELÓGIO')
$b += Linha "NTP ($(if ($ntpServ) { $ntpServ } else { 'ntp.br' }))" $(if ($ntp -eq $null) { 'sem resposta' } elseif ([math]::Abs($ntp) -lt 10) { "$(if ($ntp -ge 0) { '+' })$(Dec $ntp) ms" } else { "$(if ($ntp -ge 0) { '+' })$(Ms $ntp)" })
if ($offWsl -ne $null) { $b += Linha 'WSL x Windows' ("$(if ($offWsl -ge 0) { '+' })$(Ms $offWsl) ±$(Ms $incWsl)") }
$b += Linha 'NetTime' $(if ($nt -and $nt.Status -eq 'Running') { "ativo, $freq min" } else { 'parado' })
[void]$blocos.Add(@(1, $b))
$b = @('ARMAZENAMENTO', (Linha 'SSD C: livre' "$LivreC de $(Num $TotalC) GiB"))
if ($vhdxGiB -ne $null) { $b += Linha 'WSL ocupa no C:' "$vhdxGiB GiB" }
if ($cap) { $b += Linha 'WSL pode crescer' "$livreWsl GiB"; $b += Linha 'Capturas guardadas' $cap.capturas }
[void]$blocos.Add(@(3, $b))
if ($cap) {
    $b = @('SEGURANÇA')
    $b += Linha 'Portas expostas' @($cap.portas_expostas).Count
    $b += Linha 'Senhas em disco' (@($cap.arquivos_de_senha).Count + [int](Test-Path "$env:LOCALAPPDATA\QHOS\credentials"))
    $b += Linha 'Pasta de dados' ($cap.dados_modo -replace '^0o', '')
    [void]$blocos.Add(@(4, $b))
    $b = @('CONFIGURAÇÃO')
    $b += Linha 'Versão' "$($cap.instalacao.versao_instalada) ($(([string]$cap.instalacao.commit).Substring(0, [math]::Min(7, ([string]$cap.instalacao.commit).Length))))"
    if ($calFim) { $b += Linha 'Calendário até' "$($calFim.ToString('dd/MM')) ($dc d)" }
    if ($venc) { $b += Linha "Contrato $($cap.simbolo) vence" "$($venc.ToString('dd/MM')) ($dv d)" }
    $b += Linha 'No GitHub' $(if ($sujos -eq $null -or $publicado -eq $null) { '?' } elseif ($publicado) { "$ramoHead$(if ($sujos -gt 0) { " +$sujos" })" } else { 'NÃO' })
    [void]$blocos.Add(@(5, $b))
    $b = @('ETAPA 1')
    $up = $cap.ultima_prova
    $b += Linha 'Último pregão' $(if ($up -and $up.dia) { "$((Local "$($up.dia)T12:00:00Z" 'dd/MM')) $($up.resultado)" } elseif ($up) { "? $($up.resultado)" } else { 'nenhum ainda' })
    $b += Linha 'Seguidos verificados' "$($cap.pregoes_seguidos)/5"
    [void]$blocos.Add(@(1, $b))
}

$decisao = if ($nivelGeral -ge 2) { 'AGIR AGORA' } elseif ($nivelGeral -eq 1) { 'FUNCIONANDO, COM PENDÊNCIA' } elseif ($capturando) { 'CAPTURA SEGUINDO NORMAL' } else { 'PRONTO PARA A PRÓXIMA JANELA' }
function LinhasInc($lista) { @($lista | ForEach-Object { "$($COR[[int]$_.Nivel]) $($_.Texto)" }) }
function AcoesDe($lista) { @($lista | Where-Object { $_.Acao } | ForEach-Object { $_.Acao } | Select-Object -Unique) }
function MontarFim($linhasInc, $listaAcoes) {
    # lista vazia chega aqui como $null (e @($null).Count = 1): filtra antes de contar
    $linhasInc = @($linhasInc | Where-Object { $_ }); $listaAcoes = @($listaAcoes | Where-Object { $_ })
    $f = New-Object System.Collections.ArrayList
    [void]$f.Add("$SIRENE INCIDENTES")
    if (@($linhasInc).Count) { foreach ($x in $linhasInc) { [void]$f.Add($x) } } else { [void]$f.Add('Nenhum') }
    [void]$f.Add('')
    [void]$f.Add('AÇÃO NECESSÁRIA')
    if (@($listaAcoes).Count) { $n = 0; foreach ($a in $listaAcoes) { $n++; [void]$f.Add("$n. $a") } } else { [void]$f.Add('Nenhuma') }
    [void]$f.Add('')
    [void]$f.Add('DECISÃO')
    [void]$f.Add("$($COR[[int]$nivelGeral]) $decisao")
    [void]$f.Add('╚' + ('═' * $LARG) + '╝')
    , $f
}
$fim = MontarFim (LinhasInc (@($vermelhos) + @($amarelos))) (AcoesDe (@($vermelhos) + @($amarelos)))

$sep = '─' * $LARG
$pref = if ($nivelGeral -ge 2) { "$SIRENE **QHOS V2: PROBLEMA**`n" } else { '' }
function Montar($bl) {
    $l = New-Object System.Collections.ArrayList
    foreach ($x in $topo) { [void]$l.Add($x) }
    [void]$l.Add($sep)
    $primeiro = $true
    foreach ($b in $bl) { if (-not $primeiro) { [void]$l.Add('') }; $primeiro = $false; foreach ($x in $b[1]) { [void]$l.Add($x) } }
    [void]$l.Add($sep)
    foreach ($x in $fim) { [void]$l.Add($x) }
    $pref + '```' + "`n" + ($l -join "`n") + "`n" + '```'
}
$usar = @($blocos)
$msg = Montar $usar
while ($msg.Length -gt 1980 -and $usar.Count -gt 0) {
    $pior = ($usar | ForEach-Object { $_[0] } | Measure-Object -Maximum).Maximum
    $usar = @($usar | Where-Object { $_[0] -ne $pior })
    $msg = Montar $usar
}
# Sem blocos e ainda grande (o Discord recusa acima de 2.000): os amarelos viram uma linha, depois sobram só os
# primeiros vermelhos. Nada some sem aviso: a linha de resumo diz quantos ficaram de fora.
if ($msg.Length -gt 1980 -and $amarelos.Count) {
    $fim = MontarFim (@(LinhasInc $vermelhos) + "$($COR[1]) +$($amarelos.Count) pendência(s) amarela(s)") (AcoesDe $vermelhos)
    $msg = Montar $usar
}
$k = $vermelhos.Count
while ($msg.Length -gt 1980 -and $k -gt 1) {
    $k--
    $resto = "$($COR[2]) +$($vermelhos.Count - $k) problema(s) vermelho(s)$(if ($amarelos.Count) { " e $($amarelos.Count) amarelo(s)" })"
    $fim = MontarFim (@(LinhasInc ($vermelhos | Select-Object -First $k)) + $resto) (AcoesDe ($vermelhos | Select-Object -First $k))
    $msg = Montar $usar
}
if ($msg.Length -gt 1980) { $c = 1970; if ([char]::IsHighSurrogate($msg[$c - 1])) { $c-- }; $msg = $msg.Substring(0, $c) + "`n" + '```' }

# ---- envio ----
# Assinatura sem os números (idades e contagens mudam a cada rodada): o mesmo problema não chama de novo antes de 3 h.
$sig = ($vermelhos | ForEach-Object { $_.Texto -replace '\d+([.,]\d+)?', '#' }) -join ' | '
if ($SoAlerta -and -not $DryRun) {
    if ($vermelhos.Count -eq 0) { exit 0 }
    $ultimo = $null; if ($prev -and $prev.alerta_hora) { try { $ultimo = [datetime]$prev.alerta_hora } catch {} }
    if ($prev -and $prev.alerta_sig -eq $sig -and $ultimo -and (($agora - $ultimo).TotalHours -lt 3)) { exit 0 }
}
if ($DryRun) { $msg; "($($msg.Length) caracteres)"; exit 0 }

$novo = [ordered]@{ hora = $agora.ToString('o'); nrestarts = $(if ($nrestarts -ne $null) { $nrestarts } elseif ($prev) { $prev.nrestarts } else { $null }); alerta_sig = $sig; alerta_hora = $(if ($vermelhos.Count) { $agora.ToString('o') } elseif ($prev) { $prev.alerta_hora } else { $null }) }
[IO.File]::WriteAllText($estadoArq, ($novo | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
$args2 = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "$dir\chamar_autor.ps1", '-Mensagem', $msg)
if ($vermelhos.Count) { $args2 += '-Chamar' }
& powershell.exe @args2
