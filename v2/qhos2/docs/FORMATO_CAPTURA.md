# Formato de uma captura: arquivo único e segmentos (quem lê o quê)

Registro de 28/10/2026 (QHOS-AUD-165), conferido contra o código em `36f0e1f` e provado por
`tests/test_segmentos_v1.py` com uma captura sintética reescrita nos dois formatos.

## Os dois formatos

- **Arquivo único** (`market_frames.jsonl` ao lado de `manifest.json`): é o único formato que a V2 grava.
  `gravador.py:29` fixa `ARQUIVO = "market_frames.jsonl"` e o manifesto escrito em `gravador.py:118` não tem a
  chave `segments`.
- **Segmentos** (`frames/00000.jsonl`, `frames/00001.jsonl`... listados em `manifest.json["segments"]`, cada um
  com `name`, `frames`, `bytes` e `sha256`): formato herdado do V1. Na V2 **só o catálogo o entende**
  (`catalogo.py:27-29`, `_arquivos_da_captura` em `catalogo.py:231-257`, limite `SEGMENTOS_MAX = 4096`).

A frase da docstring do catálogo (`catalogo.py:3-4`, "ou segmentos `frames/00000.jsonl`...") descreve o que o
**catálogo** aceita, não o que a cadeia inteira aceita. A matriz abaixo é a referência.

## Matriz: cada consumidor diante de uma captura segmentada verificável

| Consumidor | Como chega aos quadros | Captura segmentada | Evidência |
|---|---|---|---|
| `catalogo.verificar_captura` | `_arquivos_da_captura` (lê `segments` ou `market_frames.jsonl`) | **Aceita**: verifica cada segmento (`segmento_diverge`, `segmento_sha256_diverge`, `segmento_fora_da_captura`) e os totais | `catalogo.py:81-94, 231-257`; `test_catalogo.py::test_captura_segmentada_boa`; `test_segmentos_v1.py::test_catalogo_verifica_a_segmentada_e_a_unica_com_os_mesmos_totais` |
| `eventos.eventos_da_captura` | `verificar_captura` + `_arquivos_da_captura` | **Aceita** (mesma assinatura de eventos que o arquivo único) | `eventos.py:31, 205, 227`; `test_segmentos_v1.py::test_eventos_atraso_inspecao_e_historico_leem_segmentos_pelo_catalogo` |
| `atraso.medir` | idem | **Aceita** | `atraso.py:28, 102-105` |
| `inspecao.inspecionar` | idem | **Aceita** | `inspecao.py:26, 115-118` |
| `historico.barras_da_captura` (e `Historico`) | `verificar_captura` + `eventos_da_captura` | **Aceita** (as mesmas barras de 1 min que o arquivo único; cache escrito) | `historico.py:104, 122`; `test_segmentos_v1.py::test_eventos_atraso_inspecao_e_historico_leem_segmentos_pelo_catalogo` |
| `microestrutura.estados_da_captura` | `eventos_da_captura` | **Aceita** (os mesmos estados que o arquivo único) | `microestrutura.py:162-163`; `test_segmentos_v1.py::test_microestrutura_le_segmentos_pelos_eventos` |
| `livro`, `desequilibrio`, `custo`, `poder`, `avaliador` | passam por `eventos_da_captura` | **Aceitam a leitura dos quadros** pelo mesmo caminho (nenhum deles abre `market_frames.jsonl` direto) | `eventos.py:205` (ponto comum) |
| `pesquisa._identificar_capturas` | hash de `market_frames.jsonl` direto | **Recusa**: `captura_inacessivel_ou_invalida` (sem o arquivo único); a sintética é recusada antes, por `instrumento_fora_do_protocolo` | `pesquisa.py:233, 257-263`; `test_segmentos_v1.py::test_pesquisa_recusa_a_segmentada` |
| `pesquisa.amostras_da_captura`, `pesquisa.executar_registrado` (todo o caminho de pesquisa e teste) | chamam `_identificar_capturas` **antes** de ler qualquer quadro | **Recusam** a segmentada (mesmo código acima), embora a leitura em si fosse por `eventos_da_captura` | `pesquisa.py:385` e `:298`; `test_segmentos_v1.py::test_caminho_de_pesquisa_e_hipoteses_recusa_a_segmentada` |
| `hipoteses.barras_da_captura` (e `avaliar_captura`) | `_identificar_capturas` antes de `eventos_da_captura` | **Recusa** a segmentada | `hipoteses.py:197-200`; mesmo teste |
| `leitura.Leitor` (leitura ao vivo e `--conferir`) | abre só `market_frames.jsonl` | **Não suporta**: sem o arquivo único fica `AGUARDANDO` (nenhuma volta abre nada); com o arquivo único presente e `segments` no manifesto, termina em `FALHA segmentos_nao_suportados` | `leitura.py:73-77, 167-168`; `test_segmentos_v1.py::test_leitor_*` |
| `coletor` | idade do arquivo via `ARQUIVO_LEGADO`; o resto vem do `Leitor` | **Não suporta** (herda do `Leitor`) | `coletor.py:66, 582` |
| `supervisao_coletor.selecionar` | `lstat` de `market_frames.jsonl` | **Trata como incompleta**: `captura_incompleta_nao_resolvida` (sem raw, não há última atividade) | `supervisao_coletor.py:99-104`; `test_segmentos_v1.py::test_supervisor_trata_a_segmentada_como_incompleta` |
| `operacao.copiar_captura` (backup/restore) | lista fixa `market_frames.jsonl` (+ identidade) e `manifest.json` | **Recusa com código**: `arquivo_regular_esperado`, sem criar o destino, com ou sem `capture_identity.json` | `operacao.py:286-289`; `test_segmentos_v1.py::test_copia_recusa_a_segmentada_com_codigo` |
| `operacao.diagnosticar` | só `manifest.json` | lista como captura (não distingue formato) | `operacao.py:318-319` |
| `agenda` | `verificar_captura` da captura que ela mesma gravou | não se aplica (só grava arquivo único) | `agenda.py:215` |
| `operacao/windows/heartbeat_v2_leitor.py` | cauda de `market_frames.jsonl` | "arquivo de quadros ilegivel" | `heartbeat_v2_leitor.py:131-138` |

Leitura prática: uma captura segmentada herdada do V1 pode ser **verificada, inspecionada, medida, lida pelos eventos,
pelo histórico e pela microestrutura**, mas **não pode** ser seguida ao vivo, servida ao terminal, selecionada pelo
supervisor, copiada pela operação, nem entrar em nada de `pesquisa` ou `hipoteses` (pré-registro, amostras, teste
registrado, barras das hipóteses): todo esse caminho passa por `_identificar_capturas`, que exige `market_frames.jsonl`. Nada disto é defeito novo: é o limite declarado aqui. A espera indefinida do
`Leitor` com manifesto presente e sem `market_frames.jsonl` é o QHOS-AUD-015 (fora deste registro).

## O que não muda com este documento

Nenhum suporte a segmentos foi adicionado nem removido; a docstring de `catalogo.py` continua como está (código
congelado na janela J0). Este arquivo é a desambiguação pedida pelo achado.
