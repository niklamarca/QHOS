# Runbook: capturar um pregao completo do ES

Regras do avaliador (avaliador.py). O dia so conta como completo se:
- existe UMA captura no dia; uma segunda vira `mais_de_uma_captura_no_dia` e o dia e perdido;
- ela comeca antes da abertura de NY (9:30) e termina depois das 16:00 de NY; desde 29/10, se os quadros pararam
  antes das 16:00 mas a sessao foi encerrada normalmente as 16:00 ou depois (o agendador sai as 16:13), o dia vale ate
  o ultimo quadro (`completo_ate_silencio_final`), como na lacuna; sessao parada antes das 16:00 perde o dia;
- nenhum intervalo maior que 60 s entre quadros que toque o pregao; desde 28/10 (N6) uma lacuna dessas nao perde o
  dia: ele vale ate o ultimo quadro antes dela (`completo_ate_lacuna`) e o que vem depois nao conta;
- livro qualificado em pelo menos 95 % dos quadros do pregao (ate a lacuna, se houver).

Reiniciar no meio do dia nao conserta: cria a segunda captura e perde o dia. Se cair, nao reinicie sem decidir
que o dia esta perdido.

## Horarios (Brasilia)
- Ate 01/11/2026 (EDT): abertura 10:30, fechamento 17:00. Inicio 06:00, `--duration 41400` (termina 17:30).
- De 02/11/2026 (EST): abertura 11:30, fechamento 18:00. Inicio 06:00, `--duration 45000` (termina 18:30).
- Nos dias em que o autor esta fora (qua a sex), iniciar por volta das 06:00, antes de sair.

## Antes de iniciar
1. Espaco livre real: `/home/usuario/qhos-v2/.venv/bin/python -m qhos2.operacao diagnosticar` e confira `livre_bytes` (e a mesma conta da CLI e do gravador, `qhos2.disco`: no WSL vale o menor entre o livre do Linux e o de `/mnt/c`; `df -h /home/usuario` sozinho mostra so o teto do vhdx, `df -h /mnt/c` mostra o outro limite): livre >= 24 GiB (2x o teto de 12 GiB; `livre_bytes` >= 25769803776). Abaixo disso a CLI avisa; abaixo de 12,5 GiB (13421772800 bytes) recusa.
2. Nada mais logando no Paper (regra: so a V2). O limite e 2 sessoes de dados.
3. PC ligado e WSL ativo durante todo o pregao.
4. A senha e digitada por voce no prompt; nunca guardada.

## Comando
    /home/usuario/qhos-v2/.venv/bin/python -m qhos2.captura --symbol ESZ6 --exchange CME --duration 41400 --online --agreements-accepted

O teto padrao e `--max-gb 12`. Ao fim a CLI verifica a captura pelo catalogo; confira `verificada: True`.

## Agendador: abre e fecha sozinho todo dia (a partir da 0.2.3)

Substitui o comando manual acima. A senha e digitada UMA vez e fica so na memoria do processo.

1. Calendario explicito (so dias uteis; feriados da CME entram com `--excluir`, conferidos na pagina oficial):

       /home/usuario/qhos-v2/.venv/bin/python -m qhos2.agenda calendario --de 2026-10-28 --ate 2026-11-25 \
           --fonte "horario regular da CME para o ES; feriados conferidos em cmegroup.com" \
           --saida /home/usuario/qhos-v2-data/agenda/calendario-20261028-20261125.json

   Janela de cada dia: 09:00-16:15 de NY (30 min antes da abertura, 15 depois do fechamento), convertida
   para UTC com o horario de verao de NY. Nada fora do calendario roda; ao fim dele o agendador para.
2. Conferir: `python -m qhos2.agenda plano --calendario <arquivo>`.
3. Rodar dentro do tmux, para sobreviver ao fechamento da janela:

       tmux new -s agenda
       /home/usuario/qhos-v2/.venv/bin/python -m qhos2.agenda rodar --calendario <arquivo> \
           --symbol ESZ6 --exchange CME --online --agreements-accepted

   Digite usuario e senha; depois `Ctrl+B` e `D` para sair sem parar. Voltar: `tmux attach -t agenda`.

Regras:
- Login recusado ou logout forcado: esquece as credenciais e encerra (codigo 3), sem nova tentativa.
- Guarda em quarentena (`python -m qhos2.operacao estado`), disco sem espaco ou partida depois de 09:25 de NY:
  pula o dia e registra.
- Uma captura por dia: queda no meio do dia nao e repetida no mesmo dia.
- PC ou WSL reiniciado: o processo morre com as credenciais; inicie de novo e digite a senha.
- Nao rode a captura manual com o agendador ligado.
- Registro: `<data_root>/agenda/estado.json` (estado atual) e `provas.jsonl` (uma linha por dia), sem credenciais.
- Troca de contrato (ESZ6 -> ESH7): pare o agendador e inicie de novo com o simbolo novo antes da data
  escrita no plano.

## Vigia: silencio, atraso crescente e o livro relido no fechamento (0.2.6, passo 9 da etapa 3)

So le. Roda ao lado da captura, em outro processo e com prioridade baixa; nao conecta nem mexe na captura. Diario em
`<data_root>/vigia/<captura>.jsonl` (uma linha por evento) e `<captura>.fechamento.json` no fim.

- Ao vivo, todo dia, pelo laco (instalado com a 0.2.6, ver a secao seguinte), num tmux proprio:

      tmux new -d -s vigia /home/usuario/qhos-v2/bin/vigia_laco.sh

  Ele espera a captura do agendador comecar, segue ate o fechamento e volta a esperar; uma linha por partida em
  `<data_root>/vigia/laco.log`. Nao volta sozinho depois de o WSL reiniciar. Para uma captura so, a mao:

      /home/usuario/qhos-v2/.venv/bin/python -m qhos2.vigia --em-andamento

  Eventos: `silencio` (mais de 60 s sem quadro novo, sem manifesto e com a leitura em dia), `lacuna` quando os quadros
  voltam (`silencio_sem_lacuna` se o dado nao mostra o buraco), `atraso_crescente` (mediana por minuto de chegada menos
  carimbo do servidor subindo 3 minutos seguidos, meio segundo cada, acima de 1 s), `livro_valido`/`livro_invalido`
  no instante exato. Quando o manifesto aparece: `captura_encerrada` e `fechamento`, e o processo sai.
- Fechamento de uma captura ja encerrada (serve para os dias ja gravados):

      /home/usuario/qhos-v2/.venv/bin/python -m qhos2.vigia --captura <data_root>/capturas/<id>

  Codigo de saida 0 = dia limpo (`achados` vazio); 1 = ha achados (`lacuna`, `atraso_crescente`, `livro_invalido`,
  `silencio_final`, `sessao_caiu`, `releitura_recusada`, `releitura_diferente_do_vivo`); 2 = captura nao verificada.
  A releitura e feita do zero, por um objeto independente, e comparada com o livro seguido ao vivo (`igual_ao_vivo`).
- Limites: "sessao viva" ao vivo e "a captura nao foi encerrada" (os heartbeats nao vao ao arquivo); a prova vem no
  fechamento, pelo manifesto (quadros depois da lacuna, ou sessao encerrada normalmente). A vigia nao ressincroniza o
  livro nem muda o que o avaliador conta: so registra. Lendo um pregao inteiro (2 GiB) o fechamento leva alguns
  minutos; nos dias de pregao, rodar o fechamento de dias antigos so fora de 10:00-17:15 de Brasilia.

## Trocar a versao instalada (0.2.6 em diante) e ligar a vigia

O registro de 7/11 e a avaliacao NAO usam o pacote instalado (rodam de um clone limpo, ver
`docs/provas/etapa3_20261029/conferencia_passo10.md`, R4). A instalacao serve aos servicos e a vigia ao vivo.

1. Fora de 10:00-17:15 de Brasilia em dia de pregao, com o agendador em `AGUARDANDO`:

       cat /home/usuario/qhos-v2-data/agenda/estado.json

2. No Windows, `git fetch` na pasta QHOSv2 e conferir que o ramo local `v2` e igual ao `origin/v2` (o instalador
   clona dessa pasta). Os scripts saem do git, nao da arvore de trabalho (ela pode estar em outro ramo).
3. No WSL, instalar (o terceiro argumento e o `identidade_codigo` congelado; o instalador para se o codigo do clone
   ou o instalado for outro):

       git -C /mnt/c/Users/usuario/OneDrive/Desktop/QHOSv2 show v2:qhos2/operacao/wsl/instalar_release.sh > /tmp/instalar_release.sh
       bash /tmp/instalar_release.sh <commit> 0.2.6 43a1de514b22461b2008a3b978d3c101d265b64d3962ee3ef6ff07a94ee7dcea

   O commit decide o que entra alem do pacote `qhos2` (o wheel tambem leva `brain` e `brain_service`): `eb0e3c8`
   (passo 10) e a 0.2.5 so com os modulos da etapa 3, Brain igual; a ponta da `v2` desde 29/10 traz tambem 37 arquivos
   do Brain da correcao-1. O `src/qhos2` e o mesmo nos dois, e o instalador confere.

4. Instalar e ligar o laco da vigia (o `sha256sum` tem de dar o valor de `operacao/SHA256SUMS.txt`):

       git -C /mnt/c/Users/usuario/OneDrive/Desktop/QHOSv2 show v2:qhos2/operacao/wsl/bin/vigia_laco.sh > /home/usuario/qhos-v2/bin/vigia_laco.sh
       chmod 700 /home/usuario/qhos-v2/bin/vigia_laco.sh && sha256sum /home/usuario/qhos-v2/bin/vigia_laco.sh
       tmux new -d -s vigia /home/usuario/qhos-v2/bin/vigia_laco.sh
       tail -2 /home/usuario/qhos-v2-data/vigia/laco.log

5. O agendador NAO precisa ser reiniciado: captura, sessao, gravador, agenda e conector da 0.2.6 sao identicos aos da
   0.2.5. Ele segue com o codigo que carregou (os manifestos dizem `versao_qhos2` 0.2.5 ate o proximo reinicio); o
   coletor que o supervisor abre a cada captura e a vigia ja nascem com a 0.2.6.
6. Desfazer: `tmux kill-session -t vigia` e o comando de rollback que o instalador imprimiu no fim.

## Passo 11 da etapa 3: calibrar, conferir os checklists e pre-registrar (6/11 a noite a 8/11 as 20h59)

O roteiro e `operacao/wsl/passo11.py`, em duas fases; roda de um clone limpo da `v2` com o Python do venv (R4) e confere
sozinho o `identidade_codigo` congelado. As capturas de 26/10 a 6/11 tem de estar em `<data_root>/capturas`.

1. Depois de a captura de 6/11 encerrar (o roteiro recusa antes de 6/11 18h20 de Brasilia), sem outra suite rodando:

       git clone -q /mnt/c/Users/usuario/OneDrive/Desktop/QHOSv2 /tmp/qhos2-registro && git -C /tmp/qhos2-registro checkout -q v2
       cd /tmp && PYTHONPATH=/tmp/qhos2-registro/qhos2/src /home/usuario/qhos-v2/.venv/bin/python \
           /tmp/qhos2-registro/qhos2/operacao/wsl/passo11.py medir --dados /home/usuario/qhos-v2-data

   Fase medir (cerca de 1 h com 8 pregoes): lista as capturas pela regra do roteiro, calibra P5/P95 e mede o custo em
   paralelo, roda o checklist 1 com o teto de US$ 12,50; se seguir, conta os sinais, roda o checklist 2 e o ensaio
   sintetico e escreve `proposta.json`. Tudo vai para `<data_root>/etapa3_registro/<instante UTC>/`. Codigo de saida:
   0 proposta pronta (imprime o SHA-256 dela), 2 recusado antes de ler, 3 parada (captura recusada, sem captura ou sem
   sinais: o autor decide), 4 abandono pedido por um checklist (nada e registrado; a decisao e a data vao ao plano).
2. O autor le `proposta.json` (pergunta, refutacao, P5, P95, taxa, alfa, FOMC) e o `resumo.json` e aprova pelo SHA-256.
3. Fase registrar (o codigo so aceita de 6/11 21h00 a 8/11 20h59 de Brasilia, pela data UTC):

       cd /tmp && PYTHONPATH=/tmp/qhos2-registro/qhos2/src /home/usuario/qhos-v2/.venv/bin/python \
           /tmp/qhos2-registro/qhos2/operacao/wsl/passo11.py registrar --dados /home/usuario/qhos-v2-data \
           --rodada <pasta da fase medir> --proposta-sha256 <sha aprovado>

   Nada e recalculado: registra exatamente a `proposta.json` aprovada. Repetir com a mesma proposta devolve o mesmo
   plano e nao gasta teste.
4. Copiar a rodada para `docs/provas/` e registrar no plano (commit na `v2`, com fetch antes).

## Captura recusada pelo catalogo por `linha_truncada` (disco cheio ou queda no meio de uma escrita)

Quando o disco enche (ENOSPC) ou o processo morre no meio de um `write`, a ultima linha de `market_frames.jsonl`
pode ficar pela metade. O gravador so conta um quadro depois de a linha inteira ser escrita, entao `frames`,
`bytes` e `sha256` do `manifest.json` descrevem apenas as linhas completas. O catalogo, por sua vez, recusa a
captura inteira (`verificada: False`, divergencia `linha_truncada`): nenhuma linha incompleta passa por boa.

Recuperacao (so quando o manifesto existe e a unica divergencia e `linha_truncada`). Abaixo, `python` e o do
venv (`/home/usuario/qhos-v2/.venv/bin/python`), `<data_root>` e a raiz de dados (padrao `~/qhos-v2-data`) e
`<captura_id>` e o nome da pasta da captura em `<data_root>/capturas`.

1. Confirme pelo catalogo, que recebe a pasta das capturas (nao a da captura) e o destino do `catalogo.json`
   (fora dela). Ele imprime uma linha por captura; a desta deve ter `verificada=False` e
   `divergencias=['linha_truncada']`, e nada mais (o comando sai com 2 enquanto houver captura nao verificada):

       python -m qhos2.catalogo <data_root>/capturas <data_root>/catalogo

2. Leia o campo `bytes` de `<data_root>/capturas/<captura_id>/manifest.json` (abaixo, `<bytes>`) e corte o
   arquivo de quadros exatamente nesse tamanho:

       truncate -s <bytes> <data_root>/capturas/<captura_id>/market_frames.jsonl

3. Verifique de novo; a linha da captura deve dar `verificada=True` com `quadros` igual a `frames`:

       python -m qhos2.catalogo <data_root>/capturas <data_root>/catalogo

4. O `fechamento_normal` continua `False` (a sessao nao terminou com logout) e a guarda, se ficou em quarentena,
   continua exigindo liberacao manual. Confira o estado e copie a `geracao` que ele mostra (`<geracao>`); so
   libere depois de confirmar que nenhuma sessao ficou aberta na Rithmic:

       python -m qhos2.operacao estado
       python -m qhos2.operacao liberar --geracao <geracao> --confirmo-sessoes-encerradas

Nada disso qualifica o dia para o avaliador: so recupera a leitura dos quadros completos.

Sem `manifest.json` (divergencia `manifesto_ausente`: o processo morreu antes do fechamento, ou o proprio
fechamento falhou por falta de disco) nao ha recuperacao: a captura e incompleta por definicao e aparece em
`capturas_incompletas` de:

       python -m qhos2.operacao diagnosticar --raiz <data_root>

Guarde-a para investigacao ou apague-a; nao a copie nem a "complete". Os comandos desta secao sao executados,
nesta ordem, por `tests/test_correcao_gravador.py::test_procedimento_do_runbook_recupera_a_captura_com_linha_truncada`
(QHOS-AUD-099).

## Dataset e arquivamento dos pregões fechados (disco)

**Estado em 29/10/2026, 21:30:** o DataSet tem uma pasta por dia (`<AAAA-MM-DD>/<BOLSA>_<CONTRATO>.tar.xz`, `INDEX.json`,
`SHA256SUMS.txt`), no formato da coleta multiativo, que insere o pregão do dia a partir de 30/10. As 12 capturas antigas
foram convertidas e conferidas. A tarefa do arquivador abaixo está desativada e ele não lê o formato novo; ver
`operacao/README.md`. O texto a seguir descreve a ferramenta como era.

**Brutos do ES (desde 29/10/2026):** a tarefa `QHOS-Limpar-Brutos-ES` (19:30) tira de `capturas/` a captura do ES
fechada há 7 dias ou mais, só depois de reler o `.tar.xz` do dia no DataSet e conferir uma segunda cópia local. Exceção da
Etapa 3: até 1/1/2027 00:00 de Brasília fica toda captura ESZ6 com primeiro quadro (data de Nova York) entre 26/10 e
20/11/2026. Detalhes em `operacao/README.md`. Para usar um pregão antigo com as ferramentas que leem `capturas/` (avaliador, custo, livro),
devolva-o numa pasta de trabalho, fora de `capturas/`, e renomeie a pasta extraída para o `capture_id` do manifesto:

    mkdir -p ~/aval && tar -xJf /mnt/c/Users/usuario/OneDrive/Desktop/DataSet/<dia>/CME_ESZ6.tar.xz -C ~/aval
    mv ~/aval/CME_ESZ6 ~/aval/$(python3 -c "import json;print(json.load(open('$HOME/aval/CME_ESZ6/manifest.json'))['capture_id'])")

O conteúdo extraído é byte a byte o da captura original (o SHA-256 do JSONL é o do manifesto); o pré-registro continua
valendo.

Sem isto cada pregão fica bruto para sempre (2 a 8 GiB por dia). O arquivador publica cada pregão fechado e conferido, em
xz (razão medida 9,2x no pregão real de 28/10), numa pasta de destino, o DataSet, e libera o disco dos brutos antigos.
Instalado em 29/10/2026: o DataSet é permanente (toda captura entra, nada que entra é apagado); a tarefa diária das 19:30
publica toda captura fechada e não apaga os brutos;
detalhes, estado e o aviso do OneDrive em `operacao/README.md`.

Comandos (WSL, `python3 -I operacao/windows/arquivar_capturas.py ...`; `--raiz` padrão `~/qhos-v2-data`; `--destino`
padrão `<raiz>/capturas_arquivadas`, e a pasta de destino tem de existir quando é informada):

- `listar [--manter-dias N]`: estado de cada pasta (`aberta`, `recente`, `elegivel`, `com_original`, `ignorada`) e a economia estimada.
- `arquivar [--executar] [--manter-dias 7] [--espera-horas 1] [--manter-original] [--recuperar] [--selar DE ATE --liberar-em DATA] [--so ID]`:
  sem `--executar` só simula. Cada pregão vira uma pasta `AAAA-MM-DD_<id da captura>` (data de Brasília do fechamento);
  `--so ID` (repetível) publica só as capturas pedidas, e um id que não existe é recusado; `--desde AAAA-MM-DD` deixa
  de fora as capturas fechadas antes desse dia.
- `verificar [--rapido]`: confere todos os arquivos feitos contra o manifesto (sem `--rapido` descomprime tudo).
- `restaurar <id> [--para pasta]`: recria a captura byte a byte (confere SHA-256) e mantém o arquivo.

Regras: só captura **fechada** (com manifesto) e fechada há mais de `--espera-horas`; a em curso, a que tem link ou
conteúdo desconhecido e a pasta do usuário no destino nunca são tocadas. O original só sai de `capturas/` depois de
`--manter-dias` e depois que o xz, lido de volta no destino, bate com o manifesto; qualquer divergência deixa o original,
sai com código 1 e é anotada em `<raiz>/.arquivador/arquivamento.jsonl`. Guarda `EM_ANDAMENTO` suspende a rodada. A pasta
restaurada em `capturas/` não é removida pela rodada diária; para tirá-la depois de usar: `arquivar --executar --recuperar`.

Selo da janela de teste: `--selar 2026-11-09 2026-11-20 --liberar-em 2026-11-21` manda esses pregões para
`<destino>/SELADO_ate_2026-11-21/` e `restaurar` os recusa até a data; no dia, a rodada os move para a raiz. É disciplina,
não proteção: a pasta é legível por quem a abrir.

O que muda para quem lê `capturas/`:

- O histórico de candles do terminal (`Historico`) lê toda captura fechada que estiver em `capturas/`: com 7 dias brutos,
  mostra cerca de 5 pregões.
- `avaliador`, `custo`, `desequilibrio`, `poder` e `pesquisa` recebem pastas de captura explícitas e leem o arquivo bruto.
  Para avaliar uma janela é preciso restaurar os pregões dela (cada um volta idêntico, com o mesmo SHA-256 do manifesto,
  então o pré-registro continua valendo). Uma janela de 60 pregões restaurada de uma vez ocupa de novo 150 a 250 GiB:
  restaure em outra pasta (`--para`) e confira o espaço, ou, depois do registro de 7 ou 8/11 e do congelamento, faça
  o código ler `.xz` (mudança em `src/qhos2` que muda `identidade_codigo`; decisão do autor, fora deste arquivador).
- Não rode `arquivar --executar` à mão entre 10:00 e 17:15 de Brasília (18:15 depois de 2/11): a rodada diária fica às 19:30.
