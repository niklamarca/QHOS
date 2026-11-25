# Coleta multi-ativo (DataSet) — desenho da Etapa 1

Branch `coleta-multi-20261029`, saída de `v2`. Nada em `qhos2/src/qhos2/**` nem no agendador muda.
Código novo só em `qhos2/coleta_multi/`, importando `qhos2` apenas para ler
(`rprotocol`, `mercado`, `gravador`, `catalogo`, `operacao.GuardaCaptura` com raiz própria, `agenda.janelas`).

## 1. Peças

| Módulo | Função |
|---|---|
| `lista.py` | os 15 itens do Quote Board como dado (símbolo exibido, descrição, tipo: `front` ou `explicito`) |
| `codecs_extra.py` | pedido e resposta de front month, mais busca de símbolos, só leitura, com lista permitida própria e teste de que nenhum template de ordem é codificável |
| `resolucao.py` | resolve cada item em (bolsa, contrato) pelo dado do instrumento; decide excluir ou incluir |
| `sessao_multi.py` | UM login, N assinaturas; roteia cada quadro ao Gravador do ativo |
| `roteador.py` | extrai (símbolo, bolsa) do quadro e escolhe o dono; tetos por ativo e global |
| `coletor.py` | laço diário (calendário da agenda), guarda própria, parada segura |
| `fechamento.py` | compacta, verifica, copia o ES, gera `INDEX.json` e `SHA256SUMS.txt` |
| `tests/` | servidor falso multi-ativo e os cenários da Etapa 2 |

## 2. Fluxo da sessão (uma conexão de login, só Ticker Plant)

1. Descoberta e login, como a `Sessao` atual (10/11, heartbeat do login).
2. **Resolução**, ainda dentro do mesmo login. `front`: pede o front month da raiz e depois o dado do instrumento (14/15) do contrato devolvido. `explicito`: dado do instrumento direto, na bolsa esperada, e nas outras três bolsas CME Group se a esperada recusar. Confere `symbol`/`exchange` devolvidos, `is_tradable` e `expiration_date`, e dedup por (bolsa, contrato).
3. Para cada ativo resolvido e negociável: assina MBO (117/118). Recusa vira exclusão daquele ativo com o motivo, e os outros seguem.
4. Retrato (115/116), **um ativo por vez**, cada um com seu token em `user_msg`, igual à `Sessao` atual (o mesmo teste de correlação).
5. Assina o último negócio (100/101) de cada ativo que aceitou MBO. Se um ativo não aceitar, grava só o que existir e registra no INDEX.
6. Streaming com heartbeat (período da `Sessao`). Cada quadro vai para o Gravador do dono. Cancela as assinaturas, logout 12/13, 120 s antes do fim da janela.

Um Gravador por ativo (`staging/<dia>/<BOLSA>_<CONTRATO>/`), com `capture_identity.json` e `manifest.json`
como o ES, `requested_symbol` = contrato resolvido, `exchange` = a devolvida pela referência.
O status da sessão entra no manifesto e leva também o símbolo pedido no Quote Board.

## 3. Roteamento (o ponto delicado)

- **160 e 150**: `symbol` e `exchange` não repetidos no quadro. Chave (bolsa, símbolo) para o Gravador.
- **116**: o dono é o ativo cujo retrato está em curso (token). Se o quadro traz símbolo e bolsa, precisam bater; se divergir, a sessão falha. O 116 final (`rp_code`) não traz identidade: vale o token.
- **161**: `symbol`/`exchange` são listas. Cada ativo listado recebe os mesmos bytes. Se vier sem identidade, o quadro **não é copiado para ninguém**: vai a `sem_dono.jsonl` do dia e conta no INDEX. Não inventamos dono. Só o ensaio real diz se o 161 do Paper traz identidade (ver pergunta 4).
- Quadro com ativo desconhecido: conta em `quadros_sem_dono` e nunca vai a um Gravador.
- Custo: o roteador lê só os campos 110100/110101 por varredura de bytes, sem decodificar o quadro inteiro. A carga é medida na Etapa 3 (quadros/s e atraso do laço).

## 4. Falhas, uma por uma

| Situação | Comportamento |
|---|---|
| ativo recusado na assinatura | ativo excluído com `motivo`; os demais seguem |
| ativo sem dado (0 quadros após o retrato) | grava o que existir; INDEX `sem_dado` |
| teto do ativo | só esse Gravador fecha (`teto_do_ativo`), os outros seguem |
| teto global ou disco abaixo da reserva | logout limpo do coletor novo, dia `interrompido`; **o ES nunca é tocado** |
| queda de conexão, heartbeat sem resposta, `gravacao_falhou` | dia `interrompido`, manifestos fechados com `transport_closed_normally=false`, **sem nova tentativa no mesmo dia** |
| login recusado ou logout forçado | para, **não tenta de novo**, entra em quarentena **própria** e avisa |
| `gravacao_falhou` num ativo | só esse ativo fecha como falho; os demais seguem (diferente da `Sessao` atual, que derruba tudo) |
| ES do agendador fora do pregão (queda, reinício, guarda, sem partida) | **reserva do ES** (decisão do autor, 30/10): a parte seguinte do coletor assina também o ES e ele vai ao DataSet como `<BOLSA>_<CONTRATO>_reserva[_pN].tar.xz`, origem `reserva_do_coletor`; se o ES cair com o coletor gravando, a parte sai e a seguinte começa na hora, já com o ES, desde que ainda caiba uma parte inteira (perto do fim não troca); "capturado" registrado antes do fim da janela (parada ordenada no meio) também aciona a reserva; nunca com o agendador `CAPTURANDO`, nem depois de login recusado ou logout forçado dele; o código congelado do ES não muda |

Quarentena e guarda ficam em `~/.local/state/qhos2/coleta_multi/` (ver pergunta 1).

## 5. Fechamento diário

1. Fecha os manifestos; `catalogo.verificar_captura` em cada pasta. Só o que passa segue.
2. Compacta em `<BOLSA>_<CONTRATO>.tar.xz` (`manifest.json`, `capture_identity.json`, `market_frames.jsonl`), xz como o arquivador, no staging.
3. Lê o xz de volta e confere SHA-256, bytes e quadros contra o manifesto.
4. Só então move o `.tar.xz` para `C:\Users\usuario\OneDrive\Desktop\DataSet\<AAAA-MM-DD>\` (rename dentro do mesmo disco, ou cópia e releitura). Original nunca apagado.
5. **ES**: espera a prova do dia em `~/qhos-v2-data/agenda/provas.jsonl` e o `manifest.json` da captura; abre os arquivos em modo leitura, verifica com o catálogo (somente leitura), compacta como `CME_ESZ6.tar.xz`. Nenhum arquivo do ES é escrito. A reserva do ES, se houve, vai junto como `CME_ESZ6_reserva[_pN].tar.xz`, para o mesmo lugar (inclusive a pasta selada de 9 a 20/11).
6. `INDEX.json` (por ativo: bolsa, símbolo pedido, contrato resolvido, quadros, bytes brutos, bytes xz, sha256 do xz, sha256 do manifesto, estado ou motivo) e `SHA256SUMS.txt`, os dois por último e de forma atômica.

O `arquivar_capturas.py` (ainda sem commit neste tree) não é reutilizado: ele grava `market_frames.jsonl.xz`, não `.tar.xz`, e pode remover originais depois de 7 dias. A lógica de "ler de volta e conferir" é copiada, não importada.

## 6. Testes da Etapa 2 (servidor falso, quadros de `sintetico.py` + campos símbolo/bolsa)

`sintetico.py` gera quadros sem `symbol`/`exchange`; o servidor falso acrescenta esses dois campos. Cenários: roteamento correto com 3 a 15 ativos intercalados; 161 com e sem identidade; ativo recusado; ativo sem dado; queda no meio do dia; disco cheio (por ativo e global); login recusado (sem 2º login); GEZ2 não negociável; dedup do "CL"/ZT; xz corrompido não publica; sem escrita na pasta do ES; senha nunca em log.

## 7. Calendário e parada

Janela e calendário da agenda (`calendario-20261028-20261125.json`); login às 09:01 NY (1 min depois do ES, para não competirem no mesmo segundo), logout 16:13 NY, uma captura por dia, nunca reinicia no mesmo dia. Se não estiver validado até 09:30 de Brasília de 30/10, o coletor novo não inicia.
