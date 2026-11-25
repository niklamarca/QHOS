# ferramentas/ — peça 1 (28/10/2026)

`adquirir.py` baixa **um lote aprovado** para `RAW/<fonte>/<AAAAMMDD>/`. Ele usa só a biblioteca padrão do Python.

```bash
python -I -B ferramentas\adquirir.py ferramentas\lotes\lote1.json
python -I -B ferramentas\adquirir.py ferramentas\lotes\lote1.json --executar
python -I -B ferramentas\testes\teste_adquirir.py
```

- O primeiro comando só simula: lista o que faria, sem rede e sem escrever nada.
- O segundo baixa de verdade.
- O terceiro roda os 11 testes. Eles usam só 127.0.0.1.

## O que o script garante

- **Antes de começar, recusa** quando:
  - a janela de captura Rithmic está aberta (seg–sex, 09:00–16:15 em Nova York);
  - falta a chave de alguma fonte;
  - já existe outra execução (trava `MANIFEST/adquirir.lock`);
  - o lote é inválido: URL sem `https://`, nome de arquivo com pasta, id repetido, fonte sem licença.
- **Antes de cada item, confere a janela de novo** (desde 29/10). Se ela abriu no meio do lote, o script para, registra "INTERROMPIDO" e sai com código 3. Ao rodar de novo depois das 16:15 NY, o checkpoint retoma do ponto onde parou.
- **Cada item vira uma linha em `MANIFEST/aquisicoes.jsonl`**, com:
  - a URL com o marcador `{CHAVE}` e a URL final;
  - UTC de início e de fim;
  - o status HTTP e o número de tentativas;
  - bytes e SHA-256;
  - `Last-Modified`, `ETag` e `Content-Type`;
  - a licença;
  - o resultado: `ok`, `quarentena` ou `falha`.
- **Tentativas:** até 3, com espera exponencial (2 s, depois 4 s) ou o `Retry-After` do servidor, limitado a 300 s. A espera vale para rede, 429 e 5xx. Um 4xx não é repetido.
- **Limite por fonte:** respeita `intervalo_s` entre requisições.
- **Nada é apagado nem sobrescrito.**
  - Parciais, HTML no lugar de CSV, `max_bytes` estourado, SHA-256 diferente do esperado ou do publicado e destino já existente vão todos para `QUARANTINE/<fonte>/<dia>/`, cada um com um `.motivo.json`.
  - Uma falha sem bytes deixa só o `.motivo.json`.
- **Checkpoint** em `MANIFEST/checkpoints/<lote>.json`. Ao rodar de novo, só tenta o que não deu `ok`.
- **Redirecionamento** para fora de `https://` é recusado.
- **Log** em `MANIFEST/logs/adquirir_<lote>_<UTC>.log`.

## Chaves (lote 2: FRED, EIA, BLS)

A chave nunca vai para arquivo. Guarde no Gerenciador de Credenciais do Windows. O `cmdkey` pede a senha sem mostrá-la, então ela não fica no histórico:

```bash
cmdkey /generic:QHOS_DADOS/FRED /user:fred /pass
```

O lote aponta para a credencial assim: `"chave": {"credman": "QHOS_DADOS/FRED", "env": "QHOS_FRED_KEY"}`. Se a credencial não existir, o script usa a variável de ambiente. Na URL, a chave entra no lugar de `{CHAVE}`. O script a retira de log, manifesto e quarentena.

## Formato do lote (JSON)

```json
{"lote": "lote1", "user_agent": "QHOS-pesquisa (contato: QHOS)", "timeout_s": 60,
 "fontes": {"<fonte>": {"intervalo_s": 2.0, "licenca": "<texto>", "chave": null}},
 "itens": [{"id": "<id>", "fonte": "<fonte>", "url": "https://...", "arquivo": "<nome>",
            "tipo": "csv|texto|json|zip|gz|html|binario", "min_bytes": 1, "max_bytes": null,
            "sha256_esperado": null, "checksum_url": null}]}
```

## Lotes (escritos em 29/10, aprovados pelo autor em 28/10 às 23:3x BRT)

Os lotes são gerados por `lotes/gerar_lote1.py` e `lotes/gerar_lotes_2a5.py`. O cabeçalho de cada gerador diz o que foi conferido e como.

| Lote | Itens | Conteúdo | Situação |
|---|---|---|---|
| lote1 | 200 | Cboe, Treasury, Fed (GSW e FOMC), CFTC COT, BCB (SGS, PTAX, Focus) | sem chave |
| lote2 | 71 | FRED/ALFRED, EIA, BLS | precisa das 3 chaves do autor |
| lote3 | 2112 | GPR, EPU, FINRA CNMS (desde 01/08/2018), tzdb 2026f | sem chave |
| lote4 | 4584 | Binance Vision BTC/ETH: spot 1m, USD-M 1m, funding, metrics; cada um com `.CHECKSUM` | sem chave |
| lote5 | 13 | Fama-French e fja05680/sp500 | sem chave |

Ficaram fora de propósito:
- **SEC FSDS:** a SEC responde 403 a um User-Agent sem e-mail de contato. Contornar isso não é permitido; incluir o e-mail é decisão do autor.
- **WGC:** não há arquivo direto, só uma página HTML.

## Peça 2 — `validar.py` (29/10)

```bash
python -I -B ferramentas\validar.py 20261029
python -I -B ferramentas\testes\teste_validar.py
```

- Lê `RAW/<fonte>/<dia>/` sem alterar nada e recusa rodar na janela de captura.
- Confere o SHA-256 de cada arquivo contra `MANIFEST/aquisicoes.jsonl`.
- Para cada dataset mede período, frequência, lacunas, duplicatas e anomalias, cada uma com etiqueta OBSERVED/CALCULATED/INFERRED.
- Escreve `MANIFEST/validacao/validacao_<dia>_<UTC>.json` e `.md`, sem sobrescrever relatórios anteriores.
- **Não muda a classe de nenhum dataset no catálogo.**
- Não lê o conteúdo de XLS (GPR), dos valores do XLSX (EPU), do HTML do FOMC nem dos blocos anuais do Fama-French. O relatório marca cada um como NÃO VALIDADO.
- Verificação: 6 testes com defeitos plantados; 10 mutações, todas pegas.
- SHA-256: `validar.py` `98de43eb131485f15a0dffea050074595770403b352e8d7859cc30aa0e0145e3`, `teste_validar.py` `6fb27ec59eac0f54881e5daab91ccbccf43b358d17a9425dd0f5c5a7e7ee5d7c`.
- O relatório `validacao_20261029_20261029T100313Z` é de uma versão anterior (`4ab6ae5d…`). Ela apontava como "mal formado" o rodapé de contagem da FINRA e não separava repetição idêntica de conflitante. O relatório que vale é o `…T100631Z`.

## Verificação da peça 1 (28/10, mais a trava por item em 29/10)

- 12 de 12 testes OK.
- 4 mutações (máscara desligada, janela sempre fechada, uma tentativa só, sem conferência por item): as quatro foram pegas.
- A simulação contra a pasta real não criou nenhum arquivo.

**Não testado:** a leitura de uma credencial real do Gerenciador do Windows. Só foi testado o caso em que a credencial não existe.

SHA-256 em 29/10:
- `adquirir.py`: `e446c3be479e3156a408e0b65d036c8b91ac0fc09fb0b5b96d6663f3a1cb6e4e`
- `teste_adquirir.py`: `2c336b979cb0107737bf089ea83ad9c5992d2f061b8e0cce84e0efa5e7695436`

Os valores de 28/10 (`8fa4a2c8…` e `c97c0eb9…`) eram de antes da trava por item.
