# Etapa 3, passo 10: emendas conferidas contra o código (29/10/2026)

Código conferido: `qhos2/src/qhos2` no commit `14eb165` (nenhum arquivo de `qhos2/src` mudou depois dele; conferido
com `git diff --stat 14eb165 f15f4c5 -- qhos2/src`, vazio). Suíte inteira em clone limpo de `f15f4c5`: verde, 1326
testes, 2 pulados (sem `cryptography`/Node no WSL).

**Hash congelado de `identidade_codigo`: `43a1de514b22461b2008a3b978d3c101d265b64d3962ee3ef6ff07a94ee7dcea`**
(38 arquivos, Python 3.12.3 CPython). Lista arquivo a arquivo em `identidade_codigo.json`, nesta pasta.

Resultado: as 23 regras abaixo conferem com o código, e nenhuma mudança de código foi necessária. Há 6 leituras do
texto que o código adota (seção 2), para o autor vetar até o registro, e 6 condições operacionais para o registro e a
avaliação darem o mesmo hash e o mesmo resultado (seção 3).

## 1. Regras do plano contra o código

| # | Regra | Onde está no plano | Onde está no código | Confere |
|---|---|---|---|---|
| 1 | Pregão regular 9h30 às 16h de Nova York, dias úteis | seção 10, item 1 | `desequilibrio.ABERTURA`, `FECHAMENTO`, `no_pregao` (dia útil) | sim |
| 2 | Só conta quadro com livro qualificado: fora do retrato, sem anomalia aberta, não cruzado | item 1 | `livro.Livro.qualificado`; `avaliador.simular` (`e.qualificado`); `calibrar` (`nao_qualificado`) | sim |
| 3 | FOMC: 13h55 às 16h em 28/10 e 9/12 não contam | item 1; emenda 25/10 (b); C1 de 28/10 | `desequilibrio.FOMC_INICIO`, `FOMC`, `na_faixa_do_fomc`; a calibração usa `FOMC`; o avaliador usa `parametros.fomc` do registro | sim (condição R1) |
| 4 | D = (Qc − Qv)/(Qc + Qv) nos 3 melhores níveis, ao fim de cada quadro | item 2; esclarecimento 24/10 | `desequilibrio.desequilibrio`, `NIVEIS = 3`, `estados` (um estado por quadro) | sim |
| 5 | P5/P95 pelo posto mais próximo, D com 4 casas, cada quadro pesa 1, janela 26/10 a 6/11, dia incompleto conta | item 3; complemento 24/10 | `Histograma` (`ESCALA = 10000`, `ceil`), `JANELA_CALIBRACAO`, `calibrar` sem exigência de pregão completo | sim |
| 6 | Na calibração, quadros depois da lacuna de entrega (> 60 s tocando o pregão) não entram | emenda 28/10 (a) | `calibrar` (`depois_da_lacuna`), `LACUNA_MAX_NS = 60 s` | sim |
| 7 | Sinal D > P95 compra, D < P5 vende; uma posição; sinal com posição aberta ignorado e contado; rearme; quadro que não conta não dispara nem rearma | item 4; complemento 24/10 | `simular`: `armado`, `ocupado`, `sinal_ignorado_posicao_aberta`, `fora_sem_rearme` | sim (leitura L1) |
| 8 | Entrada 0,5 s depois do sinal no livro 150 ms depois; saída 10 s depois, também 150 ms depois; livro vigente = último quadro até o instante; latência medida maior prevalece | item 5; emenda 25/10 (a) | `ATRASO_ENTRADA_NS`, `LATENCIA_NS`, `SEGURA_NS`; `resolver` com o estado anterior; `validar_parametros` recusa latência abaixo de 150 ms | sim (leitura L4) |
| 9 | Entrada sem livro qualificado cancelada e contada; saída adiada ao próximo qualificado e contada; sinal cuja saída passa das 16h ignorado e contado; operação sem fim descartada e contada | item 5 | contagens `entrada_cancelada_livro_nao_qualificado`, `saida_adiada_livro_nao_qualificado`, `sinal_perto_do_fechamento`, `operacao_sem_fim` | sim (leituras L2, L3) |
| 10 | Resultado em ticks menos a taxa convertida (US$ 12,50 por tick); teto declarado de US$ 12,50 | item 6; emenda 28/10 | `TICK_USD`, `liquido`; a taxa entra pelos parâmetros do registro | sim (R1) |
| 11 | Bootstrap em blocos de 5 min por dia e hora da entrada; 10.000 reamostras; semente 20261109; posto mais próximo; alfa bicaudal 5 %/3 | item 7 | `intervalo`, `BLOCO_S`, `REAMOSTRAS`, `SEMENTE`, `posto` | sim (R1, leitura L5) |
| 12 | Critério por pregão: inversão de sinal sobre a soma líquida diária, unilateral, alfa/2; os dois critérios têm de passar | emenda 28/10 (b) | `por_pregao`, `p_inversao_de_sinal` (exato até 2^n ≤ 10.000), `julgar` | sim |
| 13 | No mínimo 7 pregões; se faltar, adiado | item 8 | `MIN_PREGOES`, `julgar` → `ADIADO` | sim |
| 14 | Pregão completo: uma captura no dia, começa antes de 9h30, termina depois das 16h, 95 % de livro qualificado; a faixa do FOMC não torna o dia incompleto | item 8 | motivos de `simular`; `mais_de_uma_captura_no_dia` em `avaliar`; `QUALIFICADO_MIN` | sim (leitura L6) |
| 15 | Lacuna > 60 s que toca o pregão: o dia vale até a lacuna, conta para o mínimo, operação aberta descartada | emenda 28/10 (a) | `corte_ns`, `completo_ate_lacuna`, `lacuna_antes_da_abertura`, `quadros_depois_da_lacuna` | sim |
| 16 | Recuo do relógio preso e contado; dia recusado acima de 100 ms num recuo ou de 100 recuos | emenda 28/10 (c) | `RECUO_MAX_NS`, `RECUOS_MAX_POR_PREGAO`, `recuo_de_relogio_acima_do_limite` | sim |
| 17 | Silêncio final com a sessão encerrada normalmente às 16h ou depois: vale até o último quadro | emenda 29/10 (e) | `fim_da_sessao_ns`, `completo_ate_silencio_final` | sim |
| 18 | Janela de teste 9 a 20/11, nada olhado antes; avaliação só depois do fim | item 8 | `JANELA_TESTE`; `avaliador.main` recusa até 20/11; `executar_registrado` recusa antes do fim da janela registrada | sim |
| 19 | Medida secundária: direção do preço médio 10 s depois do sinal, empate fora; não decide | item 9 | `DIRECAO_NS`, direção `None` no empate; `acerto_direcao` fora de `julgar` | sim |
| 20 | Orçamento: 3 testes, alfa 5 %/3, prazo 30/04/2027, hipótese nova só com a anterior avaliada; código diferente = hipótese nova | emenda 28/10 (d); regra 14 | `pesquisa.ALFA_TOTAL`, `MAX_TESTES`, `PRAZO_FINAL`; `registrar_plano` | sim |
| 21 | Checklist 1: abandono com acerto necessário acima de 65 % | seção 4 | `custo.ACERTO_MAX = 0.65`, `checklist` (`>`) | sim |
| 22 | Checklist 2: abandono com menor efeito detectável (o maior dos dois critérios) acima do custo; poder 80 % | seção 4 | `poder.PODER = 0.80`, `checklist` (`mde > custo`) | sim |
| 23 | Vigia: silêncio > 60 s; atraso crescente em 3 minutos seguidos, 0,5 s cada, o último acima de 1 s | seção 4.2 | `vigia.SILENCIO_NS`, `SUBIDAS`, `DEGRAU_NS`, `ATRASO_ALERTA_NS` | sim |

## 2. Leituras do texto adotadas pelo código (nada muda; o autor pode vetar até o registro)

- **L1.** O sinal compara D sem arredondar com P95 e P5, que têm 4 casas. O arredondamento existe só no histograma
  da calibração (para caber um mês de quadros na memória). O efeito é limitado aos quadros com D entre P95 e
  P95 + 0,00005, menos de meio passo de arredondamento.
- **L2.** A saída de uma operação aberta antes das 13h55 pode ser preenchida dentro da faixa do FOMC: a faixa barra
  sinal e rearme, não a saída. Não há FOMC entre 9 e 20/11, então não há efeito no teste.
- **L3.** "Livro qualificado" para preencher uma ordem também exige os dois lados presentes; sem um lado, a entrada
  é cancelada e a saída espera.
- **L4.** "Saída 10 s depois da entrada" conta a partir da ordem de entrada (sinal + 0,5 s): a ordem de saída sai em
  sinal + 10,5 s e o preço é tomado em sinal + 10,65 s. É o mesmo instante usado no checklist 1 (`custo.SAIDA_NS`).
- **L5.** A frase da hipótese fala em intervalo de 95 %. Valem o item 7 (25/10, alfa bicaudal 5 %/3, intervalo de
  98,33 %) e a emenda (b) de 28/10 (critério por pregão).
- **L6.** "Termina depois das 16h00": um quadro recebido às 16h00min00s já basta.

## 3. Condições para o registro (passo 11) e para a avaliação (etapa 4)

- **R1, parâmetros.** `alfa` exatamente `0.05/3` (0.016666666666666666); "1,67 %" ou 0,0167 é recusado com
  `alfa_diferente_do_orcamento`. `taxa_usd` 12,5 (teto) enquanto não houver a tabela documentada. `p5` e `p95` da
  calibração de 7/11. `fomc` = `["2026-10-28", "2026-12-09"]`. Latência: a padrão (150 ms).
- **R2, janelas.** `calibracao` = `["2026-10-26", "2026-11-06"]`, `teste` = `["2026-11-09", "2026-11-20"]`.
- **R3, hora do registro.** O código compara a data em UTC: aceita de 6/11 às 21h00 até 8/11 às 20h59 de Brasília.
  O plano mantém 7 ou 8/11; depois das 20h59 de 8/11 o registro é recusado (`registro_fora_da_janela_pre_teste`) e
  não há como repetir.
- **R4, de onde o código roda.** Registro e avaliação rodam com `PYTHONPATH=<clone>/qhos2/src` num clone limpo do
  commit congelado e com o Python do venv (`/home/usuario/qhos-v2/.venv`, 3.12.3). Medido hoje: o mesmo código lido
  da pasta OneDrive dá outro hash (`550f572a…`), porque 33 dos 38 arquivos estão com CRLF ali; e o pacote instalado
  (hoje a 0.2.5) tem 36 arquivos `.py`, 8 com CRLF, e inclui `terminal/__init__.py` e `terminal/extrair_modulos.py`,
  que não estão em `src/qhos2`. O hash de qualquer um dos dois nunca bate com o congelado. `identidade_codigo`
  também guarda a versão do Python, e a avaliação compara o registro inteiro: o mesmo intérprete no registro e na
  avaliação.
- **R5, quais capturas entram na avaliação.** `executar_registrado` recusa a avaliação inteira se uma pasta tiver
  sessão com falha (`captura_sem_qualificacao_positiva`). A lista de 9 a 20/11 é, por regra, toda pasta ESZ6 da
  janela cujo manifesto passa nessa checagem; as demais ficam listadas com o motivo. Uma reavaliação com outra lista
  é recusada (`dados_diferentes_da_avaliacao_oficial`).
- **R6, nada muda até a avaliação.** Nenhum `.py` de `qhos2/src/qhos2` muda entre este congelamento e a avaliação. Se
  mudar antes de 7/11, o passo 10 é refeito e registrado; depois do registro, código diferente é hipótese nova e
  gasta um teste.

A vigia (passo 9) só acompanha um pregão ao vivo com a versão instalada, e a instalação continua sendo uma decisão
do autor. Instalar não muda as condições acima: o registro e a avaliação não usam o pacote instalado.
