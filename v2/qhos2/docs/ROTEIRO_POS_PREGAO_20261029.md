# Roteiro pós-pregão de 29/10/2026 (preparado às 16:20 de Brasília)

Nada abaixo foi aplicado. `v2` (local, GitHub e instalada) continua em `14eb165`.

## O que foi salvo (todos publicados no GitHub como branches novas; `v2` não foi tocada)

| Origem | Branch | Commit | Estado |
|---|---|---|---|
| Correção 1 | `correcao-1` | `c83da27` (221 commits sobre 231fc75) | só testes, docs e `brain`/`brain_service`; **nenhum** arquivo de `src/qhos2` |
| organização & QA (arquivador) | `salvar/arquivador-20261029` | `8915c75` | 4 arquivos novos + 2 edições de doc; estavam sem commit; ainda NÃO instalado |
| Data discovery | `salvar/dados-discovery-20261029` | `cc08b45` | ferramentas, lotes, manifesto e catálogo em `qhos2/dados_discovery/`; sem dados e sem chaves |
| Front-end de ativos (R2) | `salvar/ativos-r2-3af898a` | `3af898a` | candidato sintético; muda 5 `.py` de `src/qhos2`; provider remoto bloqueado |
| Front-end de ativos (R3) | **não é branch**: instantâneo em `ativos-r3-*_20261029T191927Z.*` | — | **em andamento** (outro agente escrevendo às 16:15); sem commit |

Cópia offline: `salvamento_20261029.bundle` (sha256 `28c72075…9304`). Dados do data discovery: `C:\Users\usuario\Documents\QHOS_DADOS` (fora do git, 1,6 GB).

## Ensaio de merge (clone descartável, nada gravado na pasta QHOSv2)

- `correcao-1` em `v2`: **1 conflito**, só em `qhos2/docs/RUNBOOK_CAPTURA.md` (e a sua edição não commitada nesse arquivo vem no branch do arquivador).
- `ativos-r2` direto em `v2`: limpo (traz junto `correcao-1` até `958c7bf`).
- `ativos-r2` sobre `correcao-1` completa: **2 conflitos de teste** (`test_correcao_coletor_observacao_brain.py`, `test_historico_parcial.py`).
- Suíte NÃO foi rodada em nenhum desses (captura aberta; a suíte do passo 9 da Etapa 3 já está agendada para depois das 17:20 e não pode coincidir com outra).

## Ordem sugerida depois das 17:20 (captura fechada, `provas.jsonl` com o dia 29/10)

1. Esperar a suíte da Etapa 3 em `14eb165` terminar. Duas suítes juntas, não.
2. `correcao-1` → `v2`: resolver o RUNBOOK, suíte em clone limpo, reiniciar só `qhos2-brain.service`. Não muda `identidade_codigo`. Mover a `v2` exige o seu "sim".
3. Arquivador e data discovery → `v2`: sem risco para o código da captura; o arquivador só é instalado depois, em separado (a primeira captura elegível é a de 28/10, em 4/11).
4. **Front-end de ativos: decisão sua antes de qualquer merge** (abaixo).

## Decisão sobre o front-end

`identidade_codigo` é o hash de todos os `.py` de `src/qhos2`. O R2 altera `__init__.py`, `coletor.py` e cria `ativos.py`, `ativos_rprotocol.py`, `ativos_transporte.py`. O R3 em andamento altera também `sessao.py`, `conector.py`, `leitura.py`, `catalogo.py`, `historico.py`, `inspecao.py` e `supervisao_coletor.py`.

- A Etapa 3 está no passo 10 de 12 (congelar o código, depois registrar em 7 ou 8/11). Entrar antes do congelamento muda o que será congelado; entrar depois quebra o pré-registro.
- O plano trava o código da captura (regra 21). `sessao.py`, `conector.py` e `leitura.py` são desse caminho.
- Só o HTML e os `.js` de `terminal/` não entram no hash, mas o R2 depende dos módulos novos de `src/qhos2`; não testei o terminal novo contra o coletor antigo.
- O que o R2 declara não ter: catálogo real de ativos, permissões e limites da conta, e o compartilhamento de sessão com o R|Trader (provider bloqueado). Hoje o terminal novo mostraria ES/NQ/GC/NVDA só com dado sintético.

Opções: (A) entrar o R2 hoje, antes do congelamento da Etapa 3, e a Etapa 3 congela já com ele; (B) esperar o R3 ser commitado e decidir com a Etapa 3 em outra janela; (C) deixar para depois do teste de 9 a 20/11.
