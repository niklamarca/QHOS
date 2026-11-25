# Operação da V2 (cópia dos scripts que rodam fora do pacote)

Estes arquivos rodam hoje no PC e no WSL, mas não estavam no git. Esta pasta é o
**registro versionado** deles em 27/10/2026. A instalação não lê desta pasta: se um
arquivo instalado mudar, esta cópia precisa ser atualizada no mesmo commit.

Cada cópia é byte a byte igual ao original; `SHA256SUMS.txt` guarda os hashes.
`.gitattributes` desliga a conversão de fim de linha (os `.ps1` e `keep_awake.py`
usam CRLF; os `.sh` e `.py` do WSL usam LF).

## Windows (instalados em `C:\Users\usuario\AppData\Local\QHOS\V2`)

| Arquivo | Para que serve |
|---|---|
| `windows/start_wsl_after_clock.ps1` | Alvo da tarefa `QHOS-V2-Linux`. Espera, por no máximo 180 s, a correção do relógio pelo NetTime e então chama `start_wsl_hidden.ps1`. Não inicia captura nem login de mercado. |
| `windows/start_wsl_hidden.ps1` | Mantém o WSL vivo: a cada 60 s executa `keep_wsl.sh` dentro do Ubuntu. Um mutex impede duas cópias. |
| `windows/keep_awake.py` | Alvo da tarefa `QHOS-V2-KeepAwake`. Impede a suspensão do PC enquanto o processo viver. |
| `windows/finish_windows_startup_admin.ps1` | Passo administrativo já executado: registra `QHOS-V2-Linux` e `QHOS-V2-KeepAwake` e desabilita as tarefas antigas do V1. Exige administrador; guarda o XML de cada tarefa antiga em `rollback\` (fora do git). |
| `windows/trust_local_brain_certificate.ps1` | Confia no certificado local do Brain (`CN=localhost`, porta 8443) conferindo dois hashes SHA256 fixos. Os hashes são pins públicos de verificação, não segredos. |

## Windows: heartbeat do Discord (instalado em `C:\Users\usuario\Downloads\Rithmic\avisos_discord`)

| Arquivo | Para que serve |
|---|---|
| `windows/heartbeat_6h.ps1` | Alvo das tarefas `QHOS-Discord-Heartbeat-6h` (posta sempre) e `QHOS-Discord-Alerta-Captura` (`-SoAlerta`, de 30 em 30 min, posta só com item vermelho novo e não repete o mesmo em 3 h). Monta um painel com nove áreas (infraestrutura, processos, fluxo de dados, relógio, integridade, segurança, configuração, mercado, ciclo completo), detalhes, incidentes, ação necessária e decisão. Cada cor vem de uma medida; área sem medida fica em branco (⚪), nunca verde. Só vira alarme o que é verdade agora (pedido do autor, 31/10). Mede no Windows o disco C, as tarefas da V2 (a `QHOS-V2-KeepAwake` só sobe no login e só conta se o plano de energia deixa o PC suspender ou hibernar na tomada), o NetTime, o desvio do relógio contra o primeiro servidor do ntp.br que responder (`a.st1`, `b.st1`, `a`, `b`, `c`; `w32tm /stripchart`, uma amostra), o desvio do WSL contra o Windows (três amostras, com a incerteza) e se o código vivo está no GitHub: o commit da pasta QHOSv2 (sem alterações fora de commit), o da versão instalada e o do coletor instalado precisam estar em algum ramo do repositório (v2, coleta-multi...), conferido nas referências locais `refs/remotes/origin/*` (a pasta busca todos os ramos e o push as atualiza; a tarefa roda sem login, sem a credencial do GitHub). Pregão "interrompido" só é incidente enquanto o sistema não voltou: voltou quando o agendador está vivo e esperando (guarda livre e estado regravado há no máximo 10 min, ou processo com menos de 10 min) ou gravando (guarda em andamento) um dia depois do pregão da prova; aí fica só no bloco ETAPA 1. Dia perdido sem captura (`pulado_*`, `falhou_antes_do_login`) continua vermelho. Agendador esperando com o estado parado há mais de 10 min (processo com mais de 10 min) é vermelho. Calendário e contrato perto do fim só amarelam se a renovação não for automática (serviço contínuo do systemd ou `qhos2-agenda` habilitado para assumir). Se a mensagem passar de 1.980 caracteres, saem os blocos de detalhe, depois os amarelos viram uma linha de resumo e, por fim, só ficam os primeiros vermelhos com a contagem do resto. `-DryRun -LeituraV2 <arquivo.json>` testa um cenário sem chamar o WSL e sem postar. O webhook vem da variável de usuário `QHOS_AVISOS_WEBHOOK_URL`, lida pelo `chamar_autor.ps1`; vermelho acrescenta `@everyone`. Mensagem limitada a 1.980 caracteres (o Discord aceita 2.000); se passar, os blocos de detalhe menos importantes saem primeiro. |
| `windows/heartbeat_v2_leitor.py` | Leitor somente leitura, rodado no WSL com `python3 -I`. O `.ps1` fixa o SHA256 dele e não roda se o hash mudar. Lê o estado do agendador (o do tmux, `qhos2.agenda rodar`, ou o do systemd, `coleta_multi.servico agenda-continuo`), as provas, a guarda, as units, o marcador de vida, a versão instalada, o commit do coletor instalado (`git rev-parse` em `~/qhos-coleta`), o calendário (o da linha de comando ou o `calendario-atual.json` do serviço) e o contrato. Durante a captura lê só a cauda (24 MiB) de `market_frames.jsonl` e mede quadros por minuto e por tipo, frescor, maior pausa, lacunas e repetições de `index` e a projeção do tamanho no fim da janela contra o teto (o menor entre `--max-gb` e os 8 GiB que o catálogo aceita). Confere também portas abertas para fora do WSL, arquivos com nome de senha e a permissão da pasta de dados. |
| `windows/chamar_autor.ps1` | Único caminho de envio ao Discord: `heartbeat_6h.ps1` o chama com `-Mensagem` (e `-Chamar` quando há vermelho, que acrescenta `@everyone`). Lê a URL de `QHOS_AVISOS_WEBHOOK_URL` (variável do usuário, `HKCU\Environment`) e sai com 2 se ela faltar ou não for do Discord. Versionado em 29/10/2026; antes só existia na pasta instalada. |

## WSL

| Arquivo | Para que serve |
|---|---|
| `wsl/bin/keep_wsl.sh` | Sobe `qhos2-brain` e `qhos2-coletor`, confere que estão ativos, registra o marcador de vida e fica em `sleep infinity`. Instalado em `/home/usuario/qhos-v2/bin`. |
| `wsl/bin/write_keepalive.py` | Grava `keepalive.json` (token de partida, pid, uid) em `qhos-v2-data/run`, com modo 0600. |
| `wsl/bin/verify_keepalive.py` | Confere o marcador, o processo `sleep` e as duas units. Imprime `V2_KEEPALIVE_VERIFIED` ou sai com código 2. |
| `wsl/bin/vigia_laco.sh` | Laço da vigia (etapa 3, passo 9). Espera cada captura começar, roda `qhos2.vigia --captura` até o fechamento e volta a esperar; só lê a captura e escreve só em `qhos-v2-data/vigia/` (`laco.log` e as saídas de cada partida). No máximo 3 partidas por captura, para não reler gigabytes em laço; quando a captura encerra sem fechamento posterior ao manifesto, faz o fechamento tardio. Instalado em `/home/usuario/qhos-v2/bin` (a partir da 0.2.6); roda no tmux `vigia`: `tmux new -d -s vigia ~/qhos-v2/bin/vigia_laco.sh`. Não volta sozinho depois de o WSL reiniciar. |
| `wsl/instalar_release.sh` | Não é instalado: roda de uma cópia em `/tmp`, tirada do git. Troca a versão em `~/qhos-v2`: `instalar_release.sh <commit> <versão> <identidade_codigo>`. Clone limpo, `identidade_codigo` do clone conferido antes do wheel e o dos arquivos instalados depois (sem `terminal/`, que a instalação acrescenta de outra pasta), `pip --no-deps --force-reinstall`, `releases/<commit>/` com o `RELEASE.json` anterior, `RELEASE.json` novo. Não reinicia serviço nem o agendador. Imprime o rollback. |
| `wsl/passo11.py` | Não é instalado: roda de um clone limpo da `v2` com `PYTHONPATH=<clone>/qhos2/src` e o Python do venv. Passo 11 da etapa 3 em duas fases: `medir` (calibração, checklists 1 e 2, ensaio sintético e `proposta.json`, sem registrar) e `registrar` (só a proposta cujo SHA-256 o autor aprovou). Confere o `identidade_codigo` congelado; captura recusada ou checklist de abandono param tudo. Testado em `tests/test_passo11.py` com dado sintético. Roteiro em `docs/RUNBOOK_CAPTURA.md`. |
| `wsl/systemd-user/qhos2-brain.service` | Unit do usuário instalada e **a que vale para instalar** (`OPERACAO.md`, "Brain e terminal"). Difere de `deploy/qhos2-brain.service`, que fica como referência, por uma linha: `EnvironmentFile=%h/qhos-v2-data/brain/service.env` (modelo em `wsl/systemd-user/service.env.example`). |

`qhos2-coletor.service` instalado não foi duplicado aqui nem entrou em `SHA256SUMS.txt`; a
referência é `deploy/qhos2-coletor.service`. Este repositório não confere a igualdade com a unit
instalada (sem cópia nem hash dela): para conferir, rode
`diff deploy/qhos2-coletor.service ~/.config/systemd/user/qhos2-coletor.service` (sem saída = iguais).
Até 28/10/2026 o arquivo de `deploy/` estava com CRLF no índice do git, logo não era byte a byte
igual a uma unit LF; `deploy/.gitattributes` fixa LF para `*.service` e `install.sh` (QHOS-AUD-002).

## Windows: dataset diário das capturas (instalado em 29/10/2026; DESATIVADO às 21:30 do mesmo dia)

**Estado atual.** O DataSet passou a ter **uma pasta por dia**, no formato da coleta multiativo (`qhos2/coleta_multi`,
branch `coleta-multi-20261029`): `DataSet\<AAAA-MM-DD>\<BOLSA>_<CONTRATO>.tar.xz` (com `_<HHMM>` quando há mais de uma
captura do mesmo ativo no dia), mais `INDEX.json` (schema `qhos2.dataset.index.v1`) e `SHA256SUMS.txt` do dia. Em
29/10/2026 às 21:30 as 12 capturas do ES de 23/10 a 29/10 foram convertidas para esse formato com o próprio código da
coleta (`compactar`, `conferir`, `publicar`, `fechar_dia`), cada `.tar.xz` relido do DataSet e conferido contra o
manifesto antes de a pasta antiga `AAAA-MM-DD_<id>` sair. As 9 capturas anteriores a 27/10 não têm `capture_identity.json`;
o `.tar.xz` delas leva só `manifest.json` e `market_frames.jsonl`, e o `INDEX.json` marca `identidade` como ausente.
A partir de 30/10 quem insere o pregão do dia (todos os ativos e a cópia do ES) é o fechamento da coleta multiativo.
A tarefa `QHOS-Arquivar-Capturas` foi **removida** às 21:42 (a limpeza dos brutos, na seção seguinte, tomou o lugar
dela) e o `arquivar_capturas.py` abaixo não reconhece o formato novo: não o use para publicar sem adaptá-lo. O que segue
descreve a ferramenta como era.

Os dois primeiros arquivos estão instalados e seus hashes estão em `SHA256SUMS.txt`; `registrar_tarefa_arquivar.ps1`
roda do repositório e não é instalado.
Alvo de instalação: a mesma pasta do heartbeat, `C:\Users\usuario\Downloads\Rithmic\avisos_discord`. Destino dos dados:
`C:\Users\usuario\OneDrive\Desktop\DataSet` (parâmetro `-Dataset` do `.ps1`).

| Arquivo | Para que serve |
|---|---|
| `windows/arquivar_capturas.py` | Roda no WSL com `python3 -I`, só biblioteca padrão, fora de `src/qhos2` (não muda a identidade do código do pré-registro). `listar`, `arquivar`, `verificar`, `restaurar`. **Publica** no destino, uma hora depois do fechamento, cada captura fechada, numa pasta `AAAA-MM-DD_<id da captura>` (data de Brasília do fechamento; `--so ID` publica só uma captura): arquivo de quadros em xz (razão medida 9,2x no pregão real de 28/10: 2,11 GB viram 0,23 GB), `manifest.json` e `capture_identity.json` byte a byte e um `ARQUIVO.json` com os hashes; o original fica em `capturas/` por 7 dias. Depois dos 7 dias **tira a pasta bruta** de `capturas/`, só depois de reler o arquivo no destino e conferir de novo. A montagem e a conferência acontecem em `<data_root>/.arquivador` (mesmo disco das capturas); se o destino fica em outro disco (o `/mnt/c` visto do WSL), cada arquivo é copiado e relido e `ARQUIVO.json` é o último a aparecer. Sem `--executar` só simula. Captura em curso (sem manifesto), recente, com link ou com conteúdo desconhecido nunca é tocada; pasta do usuário dentro do DataSet também não. Guarda `EM_ANDAMENTO` suspende a rodada. Saída 0 ok, 1 divergência (o original fica), 2 uso incorreto. |
| `windows/arquivar_capturas_diario.ps1` | Alvo da tarefa `QHOS-Arquivar-Capturas`. Não liga o WSL; confere o SHA256 fixo do `.py` antes de rodar (se mudar, sai com 2 sem executar); exige que a pasta do DataSet exista (nunca a cria); registra uma linha por rodada em `arquivar_capturas.log`. Passa `--manter-original --selar 2026-11-09 2026-11-20 --liberar-em 2026-11-21`: publica toda captura fechada e não apaga nada, nem do DataSet nem os brutos. `-Simular` e `-Verificar` para uso manual; `-LiberarDisco` tira de `capturas/` os brutos com mais de 7 dias e só deve ser usado por decisão do autor. |
| `windows/registrar_tarefa_arquivar.ps1` | Registra a tarefa diária (19:30, mesma conta e molde das tarefas do heartbeat, limite de 3 h, uma instância por vez, recupera rodada perdida). `-Mostrar` só imprime; `-Remover` apaga só essa tarefa. |

**Selo.** Os pregões de 9 a 20/11 (dias de Brasília) são a janela de teste do pré-registro ("sem olhar", avaliação uma
única vez, histórico escondido até 21/11). Eles entram em `DataSet\SELADO_ate_2026-11-21\<AAAA-MM-DD>_<id>\`, e `restaurar` recusa
(`captura_selada_ate_2026-11-21`) até a data. Em 21/11 a rodada diária move as pastas para a raiz do DataSet. O selo é
disciplina, não segredo: nada impede abrir a pasta à mão.

**OneDrive.** A Área de Trabalho está dentro do OneDrive (cópia de pastas conhecidas ligada, conta pessoal). Tudo que entrar
em `DataSet` é enviado à nuvem da Microsoft e conta na cota da conta; a pasta cresce uns 0,4 GB por pregão. Decisão do
autor antes da primeira execução real: aceitar isso, ou criar o DataSet fora do OneDrive (ex.: `C:\QHOS_DataSet`) e passar
`-Dataset`. Marque a pasta como "Manter sempre neste dispositivo" para o WSL nunca ler um marcador de nuvem sem conteúdo.

**O DataSet é permanente** (decisão do autor em 29/10/2026): toda captura entra nele, numa pasta com o dia da captura,
e nada que entra é apagado. O arquivador nunca remove uma pasta completa do destino; isso está preso pelo teste
`test_dataset_e_permanente_nenhuma_operacao_apaga_ou_altera_uma_pasta_completa`. Só os brutos de `capturas/` podem sair, e
só com `-LiberarDisco`.

Estado em 29/10/2026: os dois arquivos estão instalados e a tarefa `QHOS-Arquivar-Capturas` roda todo dia às 19:30,
publicando toda captura fechada sem apagar os brutos; uma rodada disparada pelo Agendador terminou com código 0. As 12
capturas de 23/10 a 29/10 estão publicadas e conferidas (8,60 GB brutos, 0,93 GB no DataSet). Atenção para ML: o pregão de
28/10 tem a lacuna de 62 min da origem descrita em `docs/provas/etapa2_20261028/TABELA.md`, o de 29/10 tem um atraso de 3,9 s
às 15:38:37, e as duas partes de 26/10 se sobrepõem entre 12:31:41 e 12:33:08. A remoção dos brutos (`-LiberarDisco`)
depende de decisão do autor. Qualquer mudança no `.py` exige atualizar o `$hashEsperado` do `.ps1` e reinstalar os dois.
Efeito colateral a decidir antes: o histórico do terminal e qualquer ferramenta que lê `capturas/` (avaliador, custo, poder)
só enxergam o que está bruto; ver `docs/RUNBOOK_CAPTURA.md`, seção "Dataset e arquivamento dos pregões fechados".

## Windows: limpeza diária dos brutos do ES (instalada e agendada em 29/10/2026)

Decisão do autor em 29/10/2026: o bruto do ES sai de `~/qhos-v2-data/capturas/` 7 dias depois do fechamento, desde que
esteja conferido no DataSet. Instalados na mesma pasta do heartbeat; hashes em `SHA256SUMS.txt`.

| Arquivo | Para que serve |
|---|---|
| `windows/limpar_brutos_es.py` | Roda no WSL com `python3 -I`, só biblioteca padrão, fora de `src/qhos2`. Para cada captura fechada há 7 dias ou mais: acha o dia dela no DataSet (data de Brasília do fechamento), exige `INDEX.json` com uma entrada publicada que tenha o SHA-256 do `manifest.json` dela e o mesmo SHA-256 do `.tar.xz` no INDEX, no `SHA256SUMS.txt` e no arquivo; relê o `.tar.xz` (membros exatos, JSONL igual ao manifesto em SHA-256, bytes e quadros, manifesto e identidade byte a byte); e exige uma segunda cópia local do mesmo `.tar.xz` fora do OneDrive, a da coleta (`coleta_multi/staging/<dia>/.xz/`) ou a própria (`~/qhos-v2-data/dataset_copia_local/<dia>/`, criada e conferida se faltar). Só então tira a pasta de `capturas/` (rename atômico, depois apaga). Nunca escreve no DataSet nem na pasta da coleta. Sem `--executar` só simula. Captura sem cópia no DataSet (ex.: ES de 9 a 20/11, que a coleta não copia) fica e é registrada. Antes de tudo, a proteção da Etapa 3 (abaixo) guarda o ESZ6 de 26/10 a 20/11 até 1/1/2027. Saída 0 ok, 1 divergência (o bruto fica), 2 uso incorreto. |
| `windows/limpar_brutos_es_diario.ps1` | Alvo da tarefa `QHOS-Limpar-Brutos-ES` (19:30, depois do fechamento da coleta). Não liga o WSL; confere o SHA256 fixo do `.py`; exige a pasta do DataSet; uma linha por rodada em `limpar_brutos_es.log`, com cada captura removida, sem cópia ou divergente. `-Simular` para uso manual. |
| `windows/tarefas_avisos_sem_login.ps1` | Faz `QHOS-Discord-Heartbeat-6h`, `QHOS-Discord-Alerta-Captura` e `QHOS-Limpar-Brutos-ES` rodarem também com o Windows na tela de senha (depois de um apagão, sem ninguém entrar), como a `QHOS-V2-Linux`: troca só o tipo de logon de `Interactive` para `S4U`, sem guardar senha; ações, gatilhos e configurações ficam iguais. Sem parâmetro só mostra; `-Executar` e `-Desfazer` exigem administrador. Guarda o XML original de cada tarefa em `AppData\Local\QHOS\V2\rollback\<tarefa>.antes_s4u.xml` (nunca sobrescrito) e `-Desfazer` registra de novo a partir dele. Pedido do autor em 30/10. Roda do repositório. |
| `windows/registrar_tarefa_limpar_es.ps1` | Registra a tarefa (mesmo molde das outras). `-Mostrar` só imprime; `-Remover` apaga só essa tarefa. Roda do repositório. |

Primeira rodada disparada pelo Agendador em 29/10 às 21:42: código 0, as 12 capturas no prazo, nada removido.

**Proteção da Etapa 3 (decisão do autor em 30/10/2026):** o passo 11 (a partir de 6/11) e a avaliação só leem
`<data_root>/capturas`. Por isso, até **1/1/2027 00:00 de Brasília**, a limpeza não remove nenhuma captura ESZ6 cujo
primeiro quadro (`received_ns`, data de Nova York, a mesma conta do avaliador) caia entre 26/10 e 20/11/2026. Isso
cobre a calibração (26/10 a 6/11) e o teste (9 a 20/11). Se não dá para ler o contrato no manifesto ou o primeiro
quadro, a captura também fica. A regra está em `PROTECAO_ETAPA3`, no `.py`. A data vem do autor: mudar a data é um
commit novo, com o teste que fixa os valores. Cada rodada mostra `protegidas` e `protecao` na saída e uma linha
`PROTEGIDA` por captura no log.

Pela regra, os testes de 23/10 saem em 30 e 31/10 e os de 25/10 em 1/11. As capturas de 26/10 em diante ficam até
1/1/2027. Efeito: o histórico de candles do terminal cobre a Etapa 3 inteira e, fora dela, só os últimos 7 dias. Qualquer
ferramenta que lê `capturas/` precisa que o pregão de fora da janela seja devolvido do DataSet (ver
`docs/RUNBOOK_CAPTURA.md`).

## Modelos, definições e diagnóstico (versionados em 29/10/2026; não são instalados daqui)

| Arquivo | Para que serve |
|---|---|
| `wsl/systemd-user/service.env.example` | Modelo do `~/qhos-v2-data/brain/service.env` que a unit do Brain lê: as 25 chaves do instalado, com valores de exemplo seguros. O real fica fora do git. |
| `windows/tarefas/*.xml` | `Export-ScheduledTask` das tarefas QHOS ativas, com o modo de restaurar e de onde vem o webhook; ver `windows/tarefas/LEIA.md`. |
| `diagnostico/` | Ferramentas só leitura de releitura e diagnóstico da captura (`reler_dia.sh`, `diagnostico_livro_captura.py`, `teto_local.py`); ver `diagnostico/LEIA.md`. |

## O que NÃO está aqui, de propósito

- Credenciais e tokens: `coletor.token`, `service.env`, `~/.config/qhos/credentials/*`,
  `databento.env`. Nenhum script desta pasta lê ou grava senha (regra 10 do plano, `docs/PLANO_V2.md`,
  seção 5).
- O `StartToken` da tarefa `QHOS-V2-Linux` é um GUID gerado no registro da tarefa e vive só
  nela; os scripts o recebem como parâmetro.
- Estado e logs: `admin-progress.json`, `partida_wsl.log`, `tls-trust-result.json`,
  `scheduled-tasks-final.json`, `RELEASE.json`.
- Certificado e rollback: `brain-local-v2.crt` (regenerável) e `rollback\*.xml` (contêm o
  usuário e o estado das tarefas antigas).

## Dados pessoais

Os scripts do Windows citam o caminho `C:\Users\usuario\...` e a conta `MACHINE\usuario`. O
repositório é privado; se ele deixar de ser, troque esses valores por parâmetros.
