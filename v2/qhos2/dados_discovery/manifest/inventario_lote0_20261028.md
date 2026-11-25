# Lote 0 — inventário das fontes próprias (28/10/2026)

**Como foi feito:**
- Execução: 20:25–20:35Z (17:25–17:35 BRT), com o agendador em `AGUARDANDO` (próxima janela: 2026-10-29T13:00Z).
- Só leitura no WSL e sem rede. Nada foi copiado, movido ou escrito em `~/qhos-v2-data`.
- Scripts usados: `lote0_inventario.sh`, `lote0b.sh` e `lote0c.sh`. Os hashes foram calculados com `nice -n 19 ionice -c3`.
- Etiquetas: OBSERVED = saída de comando; INFERRED = dedução a partir de nomes e calendário.

## H06 — MBO Databento (`~/qhos-v2-data/arquivo_v1/databento_GLBX.MDP3_ES`)

| Item | Valor | Etiqueta |
|---|---|---|
| Integridade | `sha256sum -c SHA256SUMS.txt`: os 54 arquivos de dados conferem. A única "falha" é a linha em que o SHA256SUMS.txt lista a si mesmo, o que não tem como conferir. | OBSERVED |
| Arquivo fora do SHA256SUMS | `LEIA.txt` (568 bytes, nota de 27/10) | OBSERVED |
| Instrumento | `ES.c.0`, publisher 1, instrument_id 42140870, tick 0,25, multiplicador 50 (`catalog/instruments.json`, montado a partir da especificação da CME) | OBSERVED |
| Arquivos brutos | 13 `.mbo.dbn.zst`, 1.667.299.181 bytes (1,55 GiB), além de manifests, checkpoints e reports de replay | OBSERVED |
| Dias UTC | 03, 04, 05, 06, 07, 08, 09, 10, 11, 12, 13, 14 e 17 de agosto de 2026 | OBSERVED (pelo nome) |
| Pregões completos | 11: 03–07/08, 10–14/08 e 17/08 | INFERRED |
| Lacunas | 08/08 tem 108 bytes (sábado, só o cabeçalho). 09/08 tem 2,9 MB (abertura de domingo). Não há arquivo para 15 e 16/08, então falta a abertura de domingo, 16/08, entre 22:00 e 24:00Z. | INFERRED |
| Formato de nome | 12 arquivos `AAAA-MM-DDT000000Z_…​.<hash>.dbn.zst` e 1 `2026-08-17.mbo.dbn.zst`, gerado por outro pipeline | OBSERVED |
| Conteúdo | Não foi decodificado (o DBN pede o pacote `databento`/`dbn`, que não roda nesta etapa) | NOT_VERIFIED |
| Licença | Termos da Databento na época da compra. A conta está encerrada, então não dá para baixar de novo. Não redistribuir. | NOT_VERIFIED |
| Uso | A V2 não usa este arquivo (regra R4: a fonte única é a Rithmic). Ele serve ao laboratório. | OBSERVED (LEIA.txt) |

Também há em `arquivo_v1`: `capturas_v2_nao_verificadas/2957b1d3…/market_frames.jsonl`, de 36.453.384 bytes. Essa captura não passa no catálogo da V2 e não tem manifest.

## H07 — capturas Rithmic V2 (`~/qhos-v2-data/capturas/`)

Todas as capturas são de ESZ6 na CME, plant `TICKER_PLANT`, sistema "Rithmic Paper Trading", esquema `qhos_rithmic_depth_capture_v1`, `research_only=true` e `can_execute=false`.

| Captura | Início–fim (UTC) | Horas | Quadros | Bytes | SHA-256 vs manifest | Linhas = frames |
|---|---|---|---|---|---|---|
| c4ff575e | 23/10 19:25:42–19:26:42 | 0,02 | 14.043 | 4.443.025 | OK | OK |
| d82fc104 | 23/10 19:27:27–19:28:27 | 0,02 | 11.630 | 3.755.236 | OK | OK |
| ee507157 | 23/10 22:31:31 (retrato) | 0,00 | 1.267 | 525.846 | OK | OK |
| 151c0eba | 25/10 19:16:17 (retrato) | 0,00 | 1.267 | 525.846 | OK | OK |
| e6946587 | 25/10 22:43:16–22:51:34 | 0,14 | 24.760 | 7.168.334 | OK | OK |
| 798f765e | 25/10 22:52:05–23:07:05 | 0,25 | 51.586 | 14.642.560 | OK | OK |
| 8aa43f0a | 25/10 23:40:31–23:55:31 | 0,25 | 57.368 | 16.205.610 | OK | OK |
| **88e31bf5** | **26/10 13:08:47–15:33:08** | 2,41 | 4.848.294 | 1.374.277.316 | OK | OK |
| **de39279b** | **26/10 15:31:41–20:05:17** | 4,56 | 4.999.872 | 1.430.946.366 | OK | OK |
| 7fc5b0b8 | 27/10 18:34:35–18:34:43 | 0,00 | 2.222 | 1.126.374 | OK | OK |
| **01f4476a** | **28/10 13:00:01–20:13:02** | 7,22 | 7.421.889 | 2.110.420.721 | não conferido | — |

**Observações:**
- Todas as linhas acima são OBSERVED, com uma exceção: o 01f4 não foi conferido porque o processo `qhos2.coletor --captura 01f4…` ainda estava aberto às 20:35Z. A prova da agenda registra `verificada=true` e `fechamento_normal=true` para ele.
- Pregões úteis para backtest: 26/10 (88e3 + de39, 13:08–20:05Z) e 28/10 (01f4, a janela inteira). As duas capturas de 26/10 se sobrepõem por cerca de 1,5 min, entre 15:31:41 e 15:33:08Z, e é preciso tirar a duplicata ao juntar. 27/10 não tem pregão capturado.
- O catálogo da V2 (`~/qhos-v2-data/catalogo/catalogo.json`, gerado em 28/10 01:59Z) lista 10 capturas, todas com `verificada=true`, `relogio_voltou=0`, `indice_continuo=true` e `divergencias=[]`. O 01f4 ainda não entrou nele.
- O relógio de 01f4 está com `utc_qualificado=false`, ou seja, carimbos sem garantia de UTC. Isso conta para o alinhamento com outras fontes.
- A licença para uso não-display ainda depende da resposta da CME.

## O que este lote **não** fez

- Não decodificou o DBN nem o base64 dos quadros. Período, lacunas e anomalias de conteúdo continuam NOT_VERIFIED.
- Não comparou H06 com H07: os períodos não se sobrepõem (agosto contra outubro).
- Não classificou nada como A ou B. H06 e H07 continuam P até a validação de conteúdo.
