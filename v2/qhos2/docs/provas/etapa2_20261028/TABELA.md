# Etapa 2 (livro certo): releitura das 11 capturas reais com a 0.2.5

Código: `qhos2` 0.2.5, commit `57388f3` (instalado em 28/10 16:47 BRT; `diff` entre o pacote instalado e o git sem
diferença fora da pasta `terminal`, que só existe na instalação). Releitura em 28/10 das 16:48 às 17:13 BRT
(`RESUMO.txt`), mais a captura do dia depois do fechamento. Ferramentas: `qhos2.livro` (livro por ordem, regra do
retrato corrigida, conta `reaplicadas`) e `qhos2.inspecao --json` (contagem independente, que aplica na hora).
Os arquivos desta pasta são a saída bruta das duas, sem preço, quantidade ou id.

## Critérios da saída da etapa

1. Zero anomalias depois do retrato: nenhuma das anomalias que deixam o livro suspeito (`remove_desconhecida`,
   `altera_desconhecida`, `novo_ja_vivo`, `sem_id`, `quantidade_invalida`, `lado_ou_preco_invalido`,
   `retrato_apos_o_fim`, `evento_desconhecido`) depois do `RETRATO_FIM`.
2. Nenhum quadro qualificado cruzado: `cruzado` no fim do quadro tira a qualificação enquanto durar (por construção);
   `cruzado_dentro_do_quadro` é estado intermediário, contado à parte e permitido.
3. Contagem de fantasmas registrada: `reaplicadas.fantasmas` = ordens que a regra errada (aplicar na hora) teria
   deixado vivas e o livro certo não tem. Prova cruzada: `ordens_vivas_no_fim` da inspeção (regra antiga) menos
   `ordens` do livro (regra certa) deve ser igual aos fantasmas.
4. Teste sintético da corrida: `tests/test_livro.py` (38 testes, suíte completa verde em clone limpo).

## Tabela

| Captura | Início (BRT) | Duração | Quadros | Eventos | Durante o retrato (esperadas) | Depois do retrato (crit. 1) | Cruzado no fim / dentro do quadro | Guardadas e reaplicadas | Fantasmas | Defasadas | Inspeção − livro | Qualificado no fim |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `c4ff575e` | 23/10 16:25 | 60 s | 14.043 | 27.476 | — | 0 | 0 / 4 | 30 | **1** | 0 | 13.673 − 13.672 = **1** | sim |
| `d82fc104` | 23/10 16:27 | 60 s | 11.630 | 24.772 | remove_desc 1 | 0 | 0 / 4 | 1 | 0 | 0 | 0 | sim |
| `ee507157` | 23/10 19:31 | 0,3 s | 1.267 | 6.565 | — | 0 | 0 / 0 | 0 | 0 | 0 | 0 | sim |
| `151c0eba` | 25/10 16:16 | 0,3 s | 1.267 | 6.565 | — | 0 | 0 / 0 | 0 | 0 | 0 | 0 | sim |
| `e6946587` | 25/10 19:43 | 498 s | 24.760 | 31.770 | novo_ja_vivo 2, remove_desc 4 | 0 | 0 / 22 | 6 | 0 | 0 | 0 | sim |
| `798f765e` | 25/10 19:52 | 900 s | 51.586 | 59.172 | — | 0 | 0 / 23 | 0 | 0 | 0 | 0 | sim |
| `8aa43f0a` | 25/10 20:40 | 900 s | 57.368 | 64.787 | — | 0 | 0 / 76 | 0 | 0 | 0 | 0 | sim |
| `88e31bf5` | 26/10 10:08 | 2 h 24 min | 4.848.294 | 4.980.578 | altera_desc 2, novo_ja_vivo 13, remove_desc 8 | 0 | 46 / 5.441 | 27 | 0 | 0 | 0 | sim |
| `de39279b` | 26/10 12:31 | 4 h 34 min | 4.999.872 | 5.232.287 | novo_ja_vivo 10, remove_desc 11 | 0 | 24 / 6.474 | 44 | 0 | 0 | 0 | sim |
| `7fc5b0b8` | 27/10 15:34 | 8 s | 2.222 | 14.969 | novo_ja_vivo 1, remove_desc 1 | 0 | 0 / 24 | 2 | 0 | 0 | 0 | sim |
| `01f4476a` | 28/10 10:00 | 7 h 13 min | 7.421.889 | 7.651.367 | altera_desc 1, novo_ja_vivo 30, remove_desc 36 | **até 15:43:03 BRT: 0**; depois: remove_desc 2.025, novo_ja_vivo 7.821, altera_desc 487, passiva_fora 587 | até 15:43:03: 38 / 98.263 em todo o dia; depois: cruzado sem voltar desde 16:51:50 | 270 | 0 | 0 | 0 | **não** (suspeito e cruzado a partir de 16:45:39) |

Total de fantasmas que a regra antiga teria deixado nas 11 capturas: **1** (captura `c4ff575e`, 23/10). Nenhuma
ordem defasada. Retrato íntegro nas 11 (`fim_visto` em todas, nenhum quadro de retrato depois do fim).

## A captura de 28/10 (`01f4476a`): lacuna de 62 min não entregue pela origem

Decisão do autor em 28/10 (opção a): a captura entra como **válida até 15:43:03 BRT e inválida daí em diante por
falta de dados da origem**; o caso vai para a etapa 5. Não é a regra do retrato: o livro ficou 5 h 43 min sem uma
anomalia e com spread de 1 tick, e a primeira anomalia é o primeiro quadro depois do buraco.

Fatos (`01f4476a….diagnostico.json`, `….atraso_por_minuto.json`, manifesto da captura):

- Retrato íntegro às 10:00:02 BRT (1.207 quadros 116); 270 atualizações guardadas e reaplicadas; 0 fantasmas.
- Nenhum quadro de mercado entre **15:43:03,178 e 16:45:39,315 BRT** (3.756 s; o segundo maior intervalo do dia
  é 4 s). A sessão ficou viva o tempo todo: 2.597 heartbeats confirmados em 25.980 s (um a cada 10 s), nenhuma
  anomalia de sessão, nenhum retrato novo, fechamento normal.
- Atraso de chegada (relógio local − carimbo da origem): ~80 ms o dia inteiro; 0,8 s às 15:31; 6,5 s às 15:33;
  39 s às 15:34; depois cresce ~12 s por minuto, em linha reta, até **132 s às 15:43**. Os primeiros quadros após
  o silêncio (16:45:39) trazem carimbo de 16:45:39 e atraso de 72 ms: a hora entre 15:40:50 e 16:45:39 da origem
  **nunca foi entregue** (não chegou atrasada em rajada; simplesmente não veio).
- Dia de FOMC: as faixas 15:00–15:43 BRT têm o maior ritmo do dia (até 85 mil quadros por minuto).
- Teto local medido em 28/10 (`teto_local_88e31bf5.txt`, 1,5 milhão de quadros reais repassados em velocidade
  máxima pelo mesmo caminho da sessão, decodificar + gravar com flush e fsync): **59.440 quadros/s**; gravador
  sozinho 163.405/s; decodificar sozinho 102.922/s; nenhum quadro acima de 10 ms. Na rampa chegavam ~1.200 a 1.400
  quadros/s, 40 vezes menos que o teto. O gargalo da rampa e o silêncio estão fora desta máquina.
- Nota de método: o `rtt_ms` do manifesto é o menor RTT de uma janela recente, não a média; ele não serve para
  dizer se a leitura local atrasou. O que descarta o atraso local é o teste do teto acima.

Encaminhamentos: pergunta à Rithmic (rascunho entregue ao autor em 28/10) e três itens na etapa 5: medir e
registrar o teto local a cada versão; detectar atraso crescente e silêncio de mercado com a sessão viva; e
ressincronizar com retrato novo (geração por conexão) em vez de seguir com livro inválido.

## Erro de operação registrado em 28/10

Suítes de teste pesadas (a completa na pasta e duas em clone limpo) rodaram no WSL durante a janela de captura, a
pedido do assistente, que chegou a afirmar que a captura já tinha fechado. O teste do teto mostra que não foi a
causa da lacuna, mas a regra vale: nada pesado no WSL entre 10:00 e 17:15 BRT em dia de pregão.
