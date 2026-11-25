# BRIEFING — Pendências Quant HPC OS

Gerado em 2026-08-19 (revisão de arquitetura, somente leitura — nenhum arquivo de código foi alterado).
Todas as correções abaixo são **propostas**. Aprove uma a uma.

---

## STATUS DE EXECUÇÃO — atualizado em 2026-08-19, com o servidor desligado

| Item | Situação | Onde |
|---|---|---|
| **P0** — a pergunta estratégica do Databento | **RESPONDIDA: não contratar.** O argumento de "fundação de medição" foi testado de graça e falsificado | `HIPOTESE_SYMB1_spec_falsificavel.md` §6, `lab/symb1_source_sensitivity.py` |
| **P1** — `CURRENT_STATE` reportando OK sobre snapshot velho | **CORRIGIDO.** Idade virou parte do veredito; blocker agora carrega o motivo | `governance/epistemics.py` (`_reconciliation_fact`) |
| **P1** — causa raiz do `WinError 5` | **INSTRUMENTADO** (não corrigido em código: a causa é externa). Log agora aponta `handle64.exe` e a exclusão de indexação | `governance/reconciliation.py` |
| **P1** — divergência `unmarked_broker_position` / `orphan_broker_positions` | **RESOLVIDA sozinha.** Janela BLOCKED foi 17/08 20:02 → 18/08 01:50; 8 transições OK desde então, carteira zerada | `data_lake/governance/reconciliation.jsonl` |
| **P2** — agente alpha morto invisível no diagnóstico | **CORRIGIDO.** Canal inoperante agora entra em `research_blockers` | `evolution/llm_agents.py`, `governance/epistemics.py` |
| **P3** — três causas, uma mensagem | **CORRIGIDO.** ERROR / WARNING / INFO separados, termo "validada" removido | `portfolio/constructor.py` |
| **P4 a P6** — entitlement, vintage de ajuste, `event_plane_enabled` | **NÃO EXECUTADOS, por decisão.** Só fazem sentido se o Databento for contratado; ver P0 |
| **P7** — gate circular 20/20 | **NÃO EXECUTADO.** Mesma razão de P4–P6 |
| **P8** — `except` genérico no execution engine | **CORRIGIDO** (2ª rodada, 19/08). Exceções operacionais e bugs agora são classes distintas de evento de auditoria (`*_UNAVAILABLE` vs `INTEGRITY_LAYER_FAULT`); fail-closed intacto. `execution_engine.py` está fora do fingerprint científico — época preservada | `execution/execution_engine.py` |
| **P9** — cache de `_data_fingerprint` | **ADIADO, por decisão.** Performance pura (~200 ms por escrita); o gargalo da pesquisa não é esse, e um erro de invalidação de cache carimbaria época errada em experimento — risco desproporcional ao ganho enquanto ninguém supervisiona |
| Extra — violação de contrato de linguagem no dashboard | **CORRIGIDO.** "Validadas / Hall of Fame" → "Aprovadas nos testes internos / Top fitness" | `dashboard/_hybrid.html` |
| Extra — bateria VRP da §7 da spec | **EXECUTADA. VRP falsificado** pelo critério 2 | `HIPOTESE_VRP_spec_falsificavel.md` §8 |
| Extra — coluna `scientific_epoch` | **CONFIRMADA diretamente** no DuckDB (o briefing só tinha evidência indireta) | `data_lake/quant_hpc.db` |

**Verificações de segurança das edições:** 34 testes passam; `code_hash` permanece `add8baec…` (nenhum arquivo do fingerprint científico foi tocado, logo **a época não virou e o forward não zerou**); ledgers sem data duplicada; braços congelados em 18/08.

**Ponto cego do §1.2 — RESOLVIDO em 2026-08-19 (Event Log do Windows):** os 8 reinícios de 18/08 01:33–02:19 **não eram crash-loop do QHOS**. Linha do tempo provada:

| Hora | Evento (System log) | Leitura |
|---|---|---|
| 01:15:43 | desligamento inesperado (Evento 6008) | máquina travou/caiu |
| 01:30:52 | Kernel-Power 41 + **bugcheck 0x00000093** (Evento 1001) | **tela azul** — defeito de driver/kernel, fora do QHOS |
| 01:37:28 | Evento 1074, `StartMenuExperienceHost`, usuário MACHINE | **reinício manual pelo menu Iniciar** |
| 02:00:09 | Evento 1074, `StartMenuExperienceHost`, usuário MACHINE | segundo reinício manual |
| 02:03:46 | Evento 1074, `msiexec.exe` (SYSTEM) | instalador forçou reboot |

Cada boot da máquina dispara a tarefa `QHOS-Server` (+90 s), e os inits restantes entre reboots foram reinícios manuais do servidor durante a sessão de manutenção (a presença do operador está provada pelos 1074 do menu Iniciar). O último boot (02:18) ficou de pé ~17,5 h. A vigia (`remote_monitor.py`) foi verificada: **só alerta, não mata nem reinicia** — a hipótese de watchdog agressivo está descartada. O medo de "o sistema pode reiniciar sozinho de novo" era infundado; o que merece atenção é a **tela azul 0x93** (não recorrente até aqui — se repetir, é hardware/driver, não este software). A janela BLOCKED da reconciliação terminando às 01:50 foi *consequência* do mesmo episódio (posições órfãs pós-queda, zeradas na sequência), não causa.

---

## 1. Estado do boot e das correções de 18/08

### 1.1 Veredito rápido

O processo está online com o código novo carregado. As correções de 18/08 pegaram, com **uma exceção parcial (reconciliação)** e **um efeito colateral que precisa ser lido corretamente**.

| Verificação pedida | Resultado | Leitura |
|---|---|---|
| `process.loaded_code_state` | **MATCH** | Correção pegou. O relatório é confiável. `code_hash == runtime_code_hash == 821a89c3…` |
| `PaperBroker: estado retomado do disco` | **Presente, 24 ocorrências** | Track-record não fragmentou. Retomou com caixa $65.008,12, 3 posições, NAV≈$99.749,84 |
| `EventPlane: WAITING_FOR_KEY` | **Presente**, repetindo de hora em hora | Zero chamadas de rede. Correto |
| WARNING de época no catálogo | **Presente**. N=0, M=3.866 | Ver 1.3 abaixo |
| Coluna `scientific_epoch` em `strategy_catalog` | **Existe** (evidência indireta, ver 1.4) | Migração rodou entre 23:28 e 23:38 de 18/08 |
| Boots do dia (`grep -c OPERACIONAL`) | **11 no arquivo de log corrente** | Ver 1.2 — não é o que parece, mas há um problema real |
| `Reconciliação indisponível: [WinError 5]` | **Ainda ocorreu 3× em 18/08**; depois **mudou de forma** | O retry não resolveu. Ver 1.5 |

### 1.2 Contagem de boots — leitura correta

O arquivo `logs/quant_hpc_2026-08-18.log` cobre de 18/08 01:30 até 19/08 09:00 (a rotação é por boot, não por dia — os 11 "OPERACIONAL" não são todos de hoje).

| Janela | Boots | Interpretação |
|---|---|---|
| 18/08 01:33 → 02:19 | **8 boots em 46 minutos** | **Restart storm não explicado.** Anterior às correções, mas nunca investigado |
| 18/08 19:57, 23:13, 23:39 | 3 boots | Janela de deploy manual das correções. Esperado |
| 19/08 (desde 23:39 de 18/08) | **0 boots** | Uptime contínuo de ~9,5 h. Estável |

**Ponto cego:** oito reinícios em 46 minutos é um crash-loop. Ninguém olhou. Se o autostart estava mascarando isso, o sistema já reiniciou sozinho antes e pode reiniciar de novo — e o `PaperBroker` retomando do disco 24× significa que houve **24 retomadas de estado**, não 1. Cada retomada é uma oportunidade de divergência entre broker e motor de risco (que é exatamente o que a reconciliação está acusando hoje).

### 1.3 O WARNING de época: N=0, M=3.866

```
2026-08-19 00:40:28 | WARNING | Storage | Catálogo: 0 aprovadas na época corrente;
3866 de épocas anteriores ignoradas (preservadas no banco, fora da pesquisa)
```

M=3.866 grande é **correto e esperado** — são vereditos de épocas científicas mortas que antes vazavam para o portfolio. A correção fez exatamente o que deveria.

N=0 é o problema, e não é bug: é o estado real da pesquisa. `research.internally_passed = 0`, `internally_rejected = 252`, geração 129, 299 hipóteses únicas testadas nesta época. Zero passaram.

### 1.4 Coluna `scientific_epoch`

Não consegui abrir `data_lake/quant_hpc.db` nem em read-only — o processo servidor mantém lock exclusivo do arquivo no Windows (o mount devolve `Permission denied`). A confirmação é indireta mas conclusiva:

| Evidência | Timestamp | Conclusão |
|---|---|---|
| `ERROR Storage: Filtro de época indisponível: Binder Error: Referenced column "scientific_epoch" not found` | 18/08 23:27:59 | Boot antigo, sem a coluna |
| `WARNING Storage: Catálogo: 0 aprovadas na época corrente; 3866 ignoradas` | 18/08 23:38:20 em diante, sempre | A query com `AND scientific_epoch = ?` executa sem erro |
| `data/storage.py:197` — `ADD COLUMN IF NOT EXISTS scientific_epoch VARCHAR` | — | Migração idempotente no bootstrap |

Zero ocorrências do Binder Error após 23:38. **A coluna existe.**

### 1.5 Reconciliação — o retry NÃO resolveu

O `[WinError 5]` original apareceu mais 3× em 18/08 (11:49, 13:29, 14:31) e depois **mudou de forma**, não sumiu:

```
2026-08-18T23:05:22 | ERROR | Reconciliation | Reconciliação OK calculada, mas o snapshot
não foi gravado (PermissionError: [Errno 5] Acesso negado); reconciliation.json está DESATUALIZADO
2026-08-18T23:05:24 | ERROR | Reconciliation | RECONCILIACAO BLOCKED:
unmarked_broker_position; orphan_broker_positions
```

Duas coisas distintas, e a segunda é a que importa:

1. O fail-open na persistência funcionou como projetado: calcula, falha ao gravar, avisa em vez de derrubar. Mas **a causa raiz do lock não foi resolvida** — só ficou menos ruidosa. `os.replace` em `data_lake/governance/` continua colidindo com alguém (candidato mais provável: indexação do Windows Search / antivírus sobre a pasta do projeto, ou o dashboard lendo o arquivo sem `FILE_SHARE_DELETE`).
2. **`RECONCILIACAO BLOCKED: unmarked_broker_position; orphan_broker_positions`** — isto não é problema de disco. É divergência real de estado entre o `PaperBroker` e o motor de risco. Está fail-closed (correto), mas está aberto e não aparece em `CURRENT_STATE.json`, que reporta alegremente `execution.reconciliation: "OK"`.

**Isso é uma contradição de governança:** o estado autoritativo diz OK enquanto o log diz BLOCKED. O `CURRENT_STATE` está lendo um `reconciliation.json` que o próprio sistema sabe estar desatualizado.

### 1.6 Ponto cego que ninguém pediu para eu procurar

```
366 erros: "Your credit balance is too low to access the Anthropic API"
primeiro: 2026-06-15T20:37 — último: 2026-08-19T00:44
```

`evolution/llm_agents.py` → `LLMAgentPool.alpha_agent` → injetado em `evolution/genetic.py:583` (`generate_strategies`) → é **um dos canais de geração de hipóteses do motor evolutivo**.

Esse canal está morto há **65 dias**, ininterruptamente. E o `research_blockers` diz `zero_internal_candidates_passed` sem mencionar que metade da máquina de geração está desligada por falta de crédito.

Não estou afirmando que ligar o agente resolveria o zero — a §4 argumenta que provavelmente não resolveria, e que o gargalo é outro. Mas **o sistema está reportando um diagnóstico de pesquisa sem declarar que um componente da pesquisa está inoperante**. Isso é falha de honestidade epistêmica do relatório de estado, que é justamente o que o `EPISTEMIC_CONTRACT` existe para impedir. Vale mais do que várias das pendências listadas abaixo.

---

## 2. Pendências em ordem de impacto real

A ordem do briefing original é a ordem em que os problemas foram descobertos. Esta é a ordem por consequência.

| # | Pendência | Impacto | Por que nesta posição |
|---|---|---|---|
| **P0** | A pergunta estratégica: nenhuma hipótese pré-registrada exige intradiário | Decide se o Databento deve ser contratado | Se a resposta é "não há", tudo abaixo sobre Databento é trabalho sobre um problema que não deveria existir |
| **P1** | Reconciliação BLOCKED + `CURRENT_STATE` reportando OK | Governança mentindo sobre si mesma | Estado autoritativo divergindo da realidade é a falha mais cara possível num sistema cuja única defesa é o registro honesto |
| **P2** | Agente alpha morto há 65 dias, invisível no `research_blockers` | Diagnóstico de pesquisa incompleto | Mesma classe do P1: o relatório omite que um componente está inoperante |
| **P3** | `constructor.py:160` — três causas, uma mensagem | Já ocorreu, não é hipotético | Em 18/08 23:27 o `except` de época devolveu vazio e o log disse "Nenhuma estratégia validada". O cenário de falha silenciosa **já aconteceu** |
| **P4** | Entitlement da Reference API | Bloqueia contratação, custo real | Resolvido pela pesquisa (§3.4). Precisa decidir ANTES de qualquer chave |
| **P5** | Vintage de ajuste inconsistente | Bug estrutural real, custo zero hoje | Só morde depois da chave — mas a documentação do Databento (§3.5) mostra que o design atual é irrecuperável, não só imperfeito |
| **P6** | `event_plane_enabled=True` vs doc | Risco latente | Só a ausência da chave protege. Colar a chave arma tudo de uma vez |
| **P7** | Gate circular 20/20 | Erro de método, sem dado para morder | Ver §3.7: o gate pode ser aprovado sem nunca exercitar o código que deveria validar |
| **P8** | `execution_engine.py:136` — `except` genérico | Dívida de observabilidade | Execução já está bloqueada por outros motivos. Corrigir antes de destravar |
| **P9** | `_data_fingerprint()` sem cache | ~200 s por escrita grande | Performance pura. O sistema não está limitado por isso |

---

## 3. Correções propostas

### P1 — Reconciliação: o estado autoritativo precisa herdar o BLOCKED

O problema não é o retry. É que `CURRENT_STATE.json` lê o snapshot do disco e reporta `"reconciliation": "OK"` mesmo quando o próprio sistema registrou que o snapshot está desatualizado e que o status corrente é BLOCKED.

O snapshot já carrega `age_seconds` e `snapshot_persisted`. O que falta é o consumidor **usar** esses campos em vez de ler só o veredito.

Proposta, no ponto onde `CURRENT_STATE` monta `execution.reconciliation`:

```python
# Antes: reporta o veredito gravado, sem qualificar.
#   "reconciliation": snapshot.get("status", "UNKNOWN")
#
# Depois: um snapshot velho ou não persistido NÃO pode ser reportado como OK.
# A idade é parte do veredito, não metadado.

_MAX_SNAPSHOT_AGE_S = 3 * 3600   # 3h: mais que o ciclo de reconciliação, menos que uma sessão

snapshot = reconciliation.latest()
age = snapshot.get("age_seconds")
persisted = snapshot.get("snapshot_persisted", True)

if age is None:
    reconciliation_state = "UNKNOWN_NO_SNAPSHOT"
elif not persisted:
    # O cálculo rodou, o disco recusou. O veredito em memória vale; o do disco não.
    reconciliation_state = f"{snapshot.get('status', 'UNKNOWN')}_SNAPSHOT_STALE"
elif age > _MAX_SNAPSHOT_AGE_S:
    reconciliation_state = f"STALE_{int(age // 3600)}H"
else:
    reconciliation_state = snapshot.get("status", "UNKNOWN")

state["execution"]["reconciliation"] = reconciliation_state
if reconciliation_state != "OK":
    state["execution"]["execution_blockers"].append(
        f"reconciliation_not_ok:{reconciliation_state}")
```

E, separadamente, a causa raiz do `WinError 5`: o retry trata o sintoma. O diagnóstico correto é descobrir **quem** segura o handle. Proposta de instrumentação (não de correção):

```python
# Em governance/reconciliation.py, no ramo do PermissionError final.
# Não corrige o lock; torna-o diagnosticável em vez de recorrente e anônimo.
except PermissionError as exc:
    log.error(
        "os.replace bloqueado após %d tentativas em %s. "
        "Causa provável: indexador/antivírus/leitor sem FILE_SHARE_DELETE. "
        "Verifique com: handle64.exe -a %s",
        attempts, path.name, path,
    )
    raise
```

**Ação humana antes de qualquer patch:** rodar `handle64.exe` (Sysinternals) sobre `data_lake\governance\` durante uma janela de erro, ou simplesmente excluir a pasta do projeto da indexação do Windows Search e da varredura em tempo real do antivírus. Isso pode eliminar o problema inteiro sem uma linha de código — e é mais provável que seja a resposta certa do que qualquer retry.

**Separado e mais urgente:** `unmarked_broker_position` e `orphan_broker_positions` são divergência real de posição. Isso precisa ser inspecionado manualmente (`data_lake/paper_broker_state.json` vs estado do motor de risco) antes de qualquer coisa. Provável origem: um dos 8 reinícios de 18/08 01:33–02:19.

### P2 — Declarar componentes de pesquisa inoperantes no `research_blockers`

O `LLMAgent` já tem `_enabled`. Falta propagá-lo.

```python
# Onde research_blockers é montado (governança / epistemics).
# Um componente de geração desligado é um blocker de pesquisa, não um detalhe de log.

blockers = []
if research.get("internally_passed", 0) == 0:
    blockers.append("zero_internal_candidates_passed")
if not regime_model_usable:
    blockers.append("regime_model_without_oos_skill")

# NOVO — canais de geração inoperantes
if not llm_agents.alpha_agent._enabled:
    blockers.append("llm_alpha_agent_disabled")
elif llm_agents.alpha_agent.consecutive_failures > 3:
    blockers.append(
        f"llm_alpha_agent_failing:{llm_agents.alpha_agent.last_error_class}")
```

Requer adicionar `consecutive_failures` e `last_error_class` ao `LLMAgent` (hoje o `except` em `llm_agents.py:118` só loga). Sem isso, o sistema continua reportando um diagnóstico de pesquisa incompleto.

**Decisão de negócio associada, não técnica:** ou se paga o crédito da API e o canal volta, ou se declara o canal desligado por escolha e se remove do caminho. O estado atual — tentar 366 vezes e falhar em silêncio — é o pior dos três.

### P3 — `portfolio/constructor.py:160`, três mensagens distintas

Termo "validada" é proibido pelo `EPISTEMIC_CONTRACT` (`"validated": "use_internal_tests_passed_with_code_and_data_hash"`). As três mensagens usam "aprovadas nos testes internos".

```python
        # Carrega candidatas da época científica corrente.
        strats_df = await loop.run_in_executor(
            None,
            lambda: self.storage.get_validated_strategies(
                min_sharpe=config.validation_min_oos_sharpe,
                limit=100,
            )
        )
        internal_pass_count = len(strats_df)

        # ``aprovada nos testes internos`` é pesquisa, não autorização de capital.
        governance_available = True
        try:
            from governance.promotion import promotion
            approved_ids = promotion.paper_approved_ids()
            if not strats_df.empty:
                strats_df = strats_df[strats_df["strategy_id"].isin(approved_ids)].copy()
            log.info(
                f"Promoção: {internal_pass_count} aprovadas nos testes internos, "
                f"{len(strats_df)} autorizadas para paper"
            )
        except Exception as _promotion_error:
            governance_available = False
            strats_df = strats_df.iloc[0:0].copy()

        if strats_df.empty:
            if not governance_available:
                # (c) fail-closed da governança — NÃO é estado normal.
                log.error(
                    "Portfolio vazio por INDISPONIBILIDADE DE GOVERNANÇA "
                    f"({type(_promotion_error).__name__}: {_promotion_error}). "
                    "Nenhuma promoção foi avaliada; o portfólio anterior foi mantido "
                    "por precaução, não por decisão."
                )
            elif internal_pass_count == 0:
                # (b) pesquisa não produziu nada nesta época — pode ser normal
                #     OU pode ser filtro de época/pipeline quebrado.
                log.warning(
                    "Zero estratégias aprovadas nos testes internos na época corrente "
                    f"({self.storage._scientific_epoch(fresh=False)[:48]}...). "
                    "Se este estado persistir por mais de uma época de dados, "
                    "suspeite do pipeline de pesquisa, não do mercado."
                )
            else:
                # (a) caminho normal: houve pesquisa, nada foi promovido.
                log.info(
                    f"{internal_pass_count} aprovadas nos testes internos, "
                    "0 autorizadas para paper — portfólio mantido. "
                    "Este é o estado esperado enquanto nenhuma candidata "
                    "completou o forward pré-registrado."
                )
            return self._weights
```

Note os níveis: (c) é `ERROR`, (b) é `WARNING`, (a) é `INFO`. Hoje os três são `WARNING` com o mesmo texto — que é exatamente por que o evento de 18/08 23:27 passou despercebido.

### P4 — Entitlement da Reference API do Databento (PESQUISADO)

**Resposta: sim, é plano separado. `bootstrap_shadow` vai gastar e falhar.**

| Fato | Fonte |
|---|---|
| Reference data (corporate actions, adjustment factors, security master) é vendida como **assinatura separada, sales-gated**, não em créditos de uso | [Blog Databento, 21/08/2024](https://databento.com/blog/corporate-actions): "To access this dataset, contact our sales team... **Pricing starts at $299 per month**" |
| Planos de reference têm **cota de símbolos**; estourar a cota devolve **erro de autorização** | [Página Security master, FAQ](https://databento.com/security-master): "Any API request exceeding the symbol limit will **return an authorization error**"; Starter = 1.000 símbolos, adicional US$ 1,75/símbolo/mês; Standard = 1.000 símbolos + US$ 0,50/símbolo/mês |
| Dados históricos de série temporal (`EQUS.MINI`, `ohlcv-1m`) são **metered / pay-as-you-go**, com US$ 125 de crédito para novos usuários | [Metered pricing](https://databento.com/docs/api-reference-historical/basics/metered-pricing); [Security master, rodapé](https://databento.com/security-master) |
| `adjustment_factors`: histórico só a partir de **2018-05-01**, atualização diária | [Adjustment factors — data feed specs](https://databento.com/docs/venues-and-datasets/adjustment-factors) |
| `security_master`: histórico desde 2005, atualização **semanal** | [Security master — data feed specs](https://databento.com/docs/venues-and-datasets/security-master) |

Consequência exata para o código atual: uma chave criada em `signup` tem crédito histórico mas **não tem entitlement de reference**. `bootstrap_shadow` faz, nesta ordem: (1) paga o bronze `ohlcv-1m`; (2) chama `_sync_reference_window`; (3) recebe erro de autorização; (4) `reference.research_ready` fica False; (5) `data/event_plane.py:242` levanta `EventContractError`. **Dinheiro gasto, plano abortado, e nenhuma mensagem que explique que a causa é comercial, não técnica.**

Proposta — checar entitlement **antes** de gastar, e falhar com mensagem que aponta para a decisão certa:

```python
# data/event_plane.py, no início de bootstrap_shadow, ANTES de qualquer
# chamada que consuma crédito.

def _reference_entitlement_probe(self) -> tuple[bool, str]:
    """Sonda barata de entitlement da Reference API.

    A Reference API do Databento é assinatura separada da API histórica
    (a partir de US$299/mês, com cota de símbolos). Uma chave nova tem
    crédito histórico e NÃO tem reference. Descobrir isso depois de pagar
    o bronze é desperdício evitável.
    """
    try:
        import databento as db
        client = db.Reference(config.databento_api_key)
        # Janela mínima, 1 símbolo: custo desprezível, resposta binária.
        client.adjustment_factors.get_range(
            symbols=[config.databento_shadow_symbol],
            start="2024-01-01", end="2024-01-02", countries=["US"],
        )
        return True, "reference_entitled"
    except Exception as exc:
        text = str(exc).lower()
        if any(k in text for k in ("authorization", "entitle", "forbidden",
                                   "403", "not subscribed", "plan")):
            return False, f"reference_not_entitled: {exc}"
        return False, f"reference_probe_failed: {type(exc).__name__}: {exc}"

# ...e no topo de bootstrap_shadow:
entitled, reason = self._reference_entitlement_probe()
if not entitled:
    raise EventContractError(
        "Reference API indisponível ANTES de qualquer gasto em bronze. "
        f"Motivo: {reason}. A Reference API (adjustment_factors / security_master) "
        "é assinatura separada da API histórica, a partir de US$299/mês com cota "
        "de símbolos. Sem ela, o silver não pode back-ajustar e o plano de eventos "
        "não sai do shadow. Decisão comercial, não técnica: ou se contrata reference, "
        "ou se remove a exigência de adjustments_applied do contrato do silver."
    )
```

**A pergunta que isso levanta e que vale mais que o patch:** o plano assume `adjustments_applied` como condição obrigatória do silver. Para SPY/QQQ/IWM/GLD/TLT — cinco ETFs grandes, nenhum split em anos, dividendos trimestrais bem documentados — **um assinatura de US$ 299/mês para obter fatores de ajuste é desproporcional**. É mais barato e mais honesto validar os fatores contra a série de dividendos que já existe via yfinance do que contratar reference data institucional para cinco tickers.

### P5 — Vintage de ajuste: o design write-once é irrecuperável, não só imperfeito

O briefing pediu para avaliar duas saídas. A documentação do próprio Databento decide a questão:

> "Note that **rescinded adjustments can happen years after the `ex_date`** for various reasons." — [Adjustment factors, status codes](https://databento.com/docs/venues-and-datasets/adjustment-factors)
>
> "If you are back-adjusting price series data then you will need to **reverse the rescinded adjustment record from your price series data using the inverted factor**."

E os `status` codes que o código já lê parcialmente: `'A'` apply, `'P'` pending, `'R'` **rescind**. Hoje `_apply_adjustments` aborta em `'P'` (correto) e filtra para `'A'` — mas **nunca trata `'R'`**, e como a partição é write-once (`data/event_silver.py:267`, `if not path.exists()`), uma rescisão futura não tem como alcançar as partições já gravadas.

**Opção A — preço bruto + fator PIT separado, ajuste no leitor. Esta é a correta.**

| Critério | Opção A (bruto + fator PIT) | Opção B (reconstruir a cada fator novo) |
|---|---|---|
| Converge para o vintage corrente | Sim | Sim |
| Reproduz um backtest rodado no passado | **Sim** — basta ler o fator com o vintage daquela data | **Não** — a série antiga foi destruída |
| Trata rescisão `'R'` | Naturalmente (o fator sai da tabela PIT) | Só se a reconstrução varrer todo o histórico |
| Custo de reescrita | Zero | O(sessões) a cada novo fator, para sempre |
| Auditabilidade | O bronze permanece imutável e é a fonte | Partição muda sem trilha |

A Opção B resolve a convergência e **destrói a reprodutibilidade** — que é justamente o ativo que este sistema inteiro existe para proteger. Num sistema cujo contrato epistêmico exige `code_hash` + `data_hash` para qualquer alegação, uma série que se reescreve silenciosamente invalida todo veredito anterior sem avisar.

Proposta (esqueleto):

```python
# data/event_silver.py

# 1) build() grava SOMENTE preço bruto. Sem back-ajuste, sem exceção.
def build(self, result, request):
    ...
    daily = self._daily_from_parts(result)          # bruto, sem _apply_adjustments
    ...
    for row in daily.itertuples(index=False):
        directory = (self.root / "provider=databento" / f"dataset={request.dataset}"
                     / "schema=ohlcv-1d-raw" / f"symbol={row.symbol}" / f"date={row.session}")
        path = directory / f"part-{request.request_id[:24]}.parquet"
        if not path.exists():
            _atomic_parquet(pd.DataFrame([row._asdict()]), path)
    # A reconciliação contra a fonte de referência passa a comparar
    # bruto-vs-bruto (ou ambos ajustados pelo MESMO vintage), nunca cruzado.

# 2) Fatores viram tabela PIT append-only, com o vintage em que foram VISTOS.
def snapshot_adjustment_factors(self, factors: pd.DataFrame, *, observed_at: str) -> None:
    """Append-only. Nunca sobrescreve. `observed_at` é o vintage.

    Preserva status 'A', 'P' e 'R'. 'R' NÃO é descartado — é o registro de
    que um fator anteriormente publicado foi cancelado, e é o único jeito
    de reconstruir a visão de quem rodou o backtest antes da rescisão.
    """
    frame = factors.copy()
    frame["_observed_at"] = observed_at
    path = (self.root.parent / "events_reference" / "adjustment_factors_pit"
            / f"observed_at={observed_at[:10]}" / f"snap-{observed_at[11:19].replace(':','')}.parquet")
    if not path.exists():
        _atomic_parquet(frame, path)

# 3) O ajuste passa a ser função pura, aplicada na LEITURA, com vintage explícito.
def adjusted_view(self, symbol: str, *, as_of_vintage: str | None = None) -> pd.DataFrame:
    """Série ajustada reconstruída sob demanda.

    as_of_vintage=None  -> vintage corrente (o que o pesquisador quer hoje)
    as_of_vintage="..." -> reproduz exatamente o que o sistema via naquela data
                           (o que a auditoria de um backtest antigo exige)
    """
    raw = self._read_raw(symbol)
    factors = self._read_factors_pit(symbol, as_of_vintage=as_of_vintage)

    # Resolve rescisões ANTES de aplicar (recomendação explícita do Databento).
    rescinded = set(factors.loc[factors["status"] == "R", "event_id"])
    factors = factors.loc[(factors["status"] == "A")
                          & (~factors["event_id"].isin(rescinded))]
    if (factors["status"] == "P").any():
        raise EventContractError("fator pendente ('P') na janela — vintage não é final")

    # Vetorizado: hoje o loop é O(linhas × fatores) com .at[] linha a linha.
    factors = factors.sort_values("_ex_date")
    cum = factors.set_index("_ex_date")["_factor"][::-1].cumprod()[::-1]
    multiplier = cum.reindex(raw["session"], method="bfill").fillna(1.0).to_numpy()
    out = raw.copy()
    out[self.PRICE_COLUMNS] = out[self.PRICE_COLUMNS].to_numpy() * multiplier[:, None]
    out["_adjustment_vintage"] = as_of_vintage or self._current_vintage()
    return out
```

O campo `_adjustment_vintage` deve entrar no `data_fingerprint` da época científica. Sem isso, duas rodadas com vintages diferentes recebem o mesmo hash e o contrato epistêmico mente.

### P6 — `event_plane_enabled=True` vs `EVENT_PLANE.md`

Quem deve ceder: **o código**.

`data/EVENT_PLANE.md:5` diz "nasce desativado e isolado da pesquisa diária". `core/config.py:171` diz `event_plane_enabled: bool = True`. Hoje a única coisa que impede tráfego é a ausência de `databento_api_key`. Colar a chave arma simultaneamente: `databento_bootstrap_shadow=True`, `databento_backfill_enabled=True`, `automatic_ingestion=True`. Um único ato (colar a chave) dispara três comportamentos automáticos que ninguém aprovou individualmente.

Alinhar o doc ao código seria escolher documentar um default perigoso. Alinhar o código ao doc restaura o princípio de que **habilitar precisa ser um ato deliberado e separado de fornecer a credencial**.

```python
# core/config.py
    # O plano de eventos nasce DESATIVADO (contrato em data/EVENT_PLANE.md).
    # Fornecer a chave é condição necessária, nunca suficiente: habilitar é um
    # segundo ato, deliberado. Hoje a ausência da chave é a única proteção, o que
    # transforma "colar a chave" em "armar ingestão + backfill + shadow de uma vez".
    event_plane_enabled: bool = False
```

E, no `DATABENTO_SETUP.bat` / `setup_databento.ps1`, a chave deve ser gravada **sem** ligar o plano, com uma instrução explícita de que o segundo passo é manual.

### P7 — Gate circular: por que 20 é errado, e o que colocar no lugar

`databento_reconciliation_min_sessions = 20 == databento_backfill_sessions = 20`. Isto é um critério calibrado à entrega do pipeline. Mas o problema é pior do que circularidade:

**Com 20 sessões (≈1 mês corrido), o gate pode ser aprovado sem nunca exercitar o caminho de código que ele existe para validar.** SPY, QQQ, IWM, GLD e TLT pagam dividendos trimestralmente. Uma janela de 20 sessões pode conter **zero ex-dates**. Nesse caso `_apply_adjustments` devolve `no_adjustments_in_window` e o gate aprova o silver tendo testado exatamente nada do back-ajuste. É um teste que passa por vacuidade.

Proposta com justificativa estatística, em duas partes:

**(a) Piso amostral — regra dos três.** Para afirmar com 95% de confiança que a taxa verdadeira de sessão defeituosa é inferior a `p`, com zero defeitos observados, exige-se `n ≈ 3/p`.

| Taxa máxima tolerada de sessão defeituosa | n necessário (0 falhas) | Comentário |
|---|---|---|
| 15% | 20 sessões | **É o que 20 sessões realmente prova.** Inaceitável para dado que vai fundamentar decisão de capital |
| 5% | **60 sessões** | Piso defensável |
| 1% | 300 sessões | Ideal, mas 14 meses de backfill |

**(b) Cobertura de evento — o critério que realmente importa.** O gate deve exigir que o caminho de ajuste tenha sido exercitado, não só que sessões tenham passado.

```python
# core/config.py
    # ─── Promoção do plano de eventos ───────────────────────────────
    # O critério NÃO pode ser igual ao que o backfill entrega, senão mede o
    # pipeline em vez de medir a estatística. Dois requisitos independentes:
    #
    # (a) Piso amostral pela regra dos três: com 0 falhas em n sessões, o
    #     limite superior 95% da taxa de defeito é ~3/n. n=60 => <5%.
    #     n=20 só provaria <15%, que é alto demais para fundamentar capital.
    databento_reconciliation_min_sessions: int = 60
    #
    # (b) Cobertura de evento: 20 sessões podem não conter NENHUM ex-date nos
    #     5 ETFs do universo — o gate aprovaria sem jamais exercitar o
    #     back-ajuste. Exige-se que o caminho tenha sido testado de fato.
    databento_reconciliation_min_ex_dates_per_symbol: int = 1
    databento_reconciliation_min_quarters_covered: int = 2
    #
    # (c) O backfill precisa ENTREGAR mais do que o gate EXIGE, senão a
    #     circularidade volta pela porta dos fundos.
    databento_backfill_sessions: int = 130          # ~6 meses; 2 ciclos de dividendo
```

E a crítica que fica de pé mesmo com os números corrigidos: **o gate mede concordância entre Databento e yfinance, não qualidade.** Se as duas fontes errarem junto (mesma origem consolidada a montante — provável), o gate aprova. Um erro relativo de fechamento tolerado de 0,5% (`databento_reconciliation_max_close_error = 0.005`) é enorme para barra diária de ETF líquido: 0,5% no SPY é ~US$ 3. Uma discrepância de 3 dólares no fechamento do SPY não é ruído, é erro grave — e passa no gate. Sugiro `0.0005` (5 bps) e tratar qualquer coisa acima como incidente a investigar, não como tolerância.

### P8 — `execution/execution_engine.py:136`, distinguir causa sem afrouxar o fail-closed

O `except Exception` converte `ImportError`, `AttributeError` e `TypeError` — bugs de programação — em "ordem bloqueada por risco". A ordem continua bloqueada nas duas propostas abaixo; o que muda é que a causa para de ser lavada.

```python
        # Falhas OPERACIONAIS da camada de integridade: esperadas, recuperáveis,
        # bloqueiam a ordem em silêncio disciplinado.
        _INTEGRITY_OPERATIONAL = (OSError, TimeoutError, ValueError, KeyError)

        try:
            from governance.reconciliation import reconciliation
            reconciled, reconciliation_reason = reconciliation.execution_allowed(
                self._default_broker, self._risk_engine, self._signal_executor)
            if not reconciled:
                from governance.control import audit
                audit.record("ORDER_BLOCKED_BY_RECONCILIATION", {...},
                             actor="reconciliation", subject=symbol)
                log.error(f"Ordem bloqueada pela reconciliação: {reconciliation_reason}")
                return None

        except _INTEGRITY_OPERATIONAL as _op_error:
            # Indisponibilidade real. Fail-closed, sem drama.
            from governance.control import audit
            audit.record("ORDER_BLOCKED_INTEGRITY_UNAVAILABLE",
                         {"symbol": symbol, "error_class": type(_op_error).__name__,
                          "error": str(_op_error)},
                         actor="reconciliation", subject=symbol)
            log.error("Ordem bloqueada: reconciliação indisponível (%s): %s",
                      type(_op_error).__name__, _op_error)
            return None

        except Exception as _bug:
            # ImportError / AttributeError / TypeError / etc. — isto NÃO é uma
            # decisão de risco, é um defeito. A ordem continua bloqueada
            # (fail-closed intacto), mas o evento é de classe diferente e
            # precisa acordar alguém, não virar mais uma linha de "bloqueado".
            from governance.control import audit
            audit.record("INTEGRITY_LAYER_FAULT",
                         {"symbol": symbol, "error_class": type(_bug).__name__,
                          "error": str(_bug), "traceback": traceback.format_exc()},
                         actor="execution_engine", subject=symbol)
            log.critical(
                "DEFEITO na camada de integridade (%s: %s). Ordem bloqueada, mas a "
                "causa é BUG, não risco. Trate como incidente de código.",
                type(_bug).__name__, _bug, exc_info=True,
            )
            return None
```

O mesmo padrão vale para os dois `except Exception` seguintes (autonomia, linha ~154). E `INTEGRITY_LAYER_FAULT` deve escalar para `research_blockers` / `execution_blockers`, senão volta a ser invisível.

### P9 — Cache de `_data_fingerprint()`

O fingerprint depende de: conteúdo dos JSONs em `data_lake/provenance/`, e do conjunto de ativos ativos (`config.data_assets_stocks + crypto + reference`). A invalidação **precisa** ocorrer quando qualquer um dos dois muda — em particular quando um manifesto é reescrito por um novo download, que é justamente o momento em que a época científica deve virar.

Verificar isso com `stat` (microssegundos) em vez de re-hashear (51 ms) mantém a correção e elimina o custo.

```python
# governance/experiment_registry.py

    def __init__(self):
        ...
        self._code_hash = self._fingerprint_code()
        self._data_fp_cache: tuple[tuple, str] | None = None   # (chave, fingerprint)

    @staticmethod
    def _provenance_key() -> tuple:
        """Chave barata que muda sempre que o fingerprint PODE ter mudado.

        Inclui mtime_ns E tamanho de cada manifesto: mtime sozinho falha quando
        duas gravações caem no mesmo tick do relógio de arquivo. Inclui também o
        universo ativo, porque trocar de ativo muda o fingerprint sem tocar em
        nenhum arquivo existente.
        """
        root = Path(config.data_lake_dir) / "provenance"
        files = tuple(sorted(
            (p.name, s.st_mtime_ns, s.st_size)
            for p in root.glob("*.json")
            if (s := p.stat())
        ))
        universe = tuple(sorted(
            config.data_assets_stocks + config.data_assets_crypto
            + list(getattr(config, "data_assets_reference", []))
        ))
        return (files, universe)

    def _data_fingerprint(self) -> str:
        key = self._provenance_key()
        if self._data_fp_cache is not None and self._data_fp_cache[0] == key:
            return self._data_fp_cache[1]
        from governance.epistemics import current_data_fingerprint
        fingerprint = current_data_fingerprint()
        self._data_fp_cache = (key, fingerprint)
        return fingerprint
```

**Quando o cache PRECISA ser invalidado (e é por isso que TTL puro não serve):**

| Evento | Detectado pela chave? | Como |
|---|---|---|
| Novo download reescreve um manifesto | Sim | `st_mtime_ns` + `st_size` |
| Manifesto novo (ativo adicionado) | Sim | novo nome no tuple |
| Manifesto removido (ativo aposentado) | Sim | nome some do tuple |
| `config.data_assets_*` alterado em runtime | Sim | `universe` no tuple |
| Reescrita idêntica byte a byte no mesmo tick | **Não** | Aceitável: se o conteúdo é idêntico, o fingerprint também é |
| Relógio do sistema retrocede | **Não** | Risco residual; mitigado por `st_size` e por não usar TTL |

Não use `time.time()` como componente da chave. TTL faria o fingerprint mudar sem que o dado mude — o oposto do que o contrato epistêmico exige.

---

## 4. A pergunta estratégica

O NAV forward está em US$ 99.889,54 sobre US$ 100.000, retorno de **−0,076%** em 24 sessões desde 22/06. O modelo de regime tem ganho OOS de **−0,1373** (acurácia 0,635 contra baseline 0,772): o modelo é **pior que chutar a classe majoritária**, e serve baseline por isso. `research_blockers` = `zero_internal_candidates_passed` + `regime_model_without_oos_skill`.

Neste estado, a pergunta é:

> **Qual hipótese pré-registrada e falsificável exige dado intradiário, e por que ela não pode ser testada em barra diária?**

### Verifiquei os arquivos `HIPOTESE_*.md`. A resposta é: nenhuma delas exige intradiário.

| Arquivo | Resolução exigida | Precisa de Databento? |
|---|---|---|
| `HIPOTESE_SYMB1_spec_falsificavel.md` | Barra diária (`r`, σ5/σ21/σ63 de retornos diários), defasagem de 1 dia | **Não** |
| `HIPOTESE_TREND_spec_falsificavel.md` | Barra diária (sinais de 63d/126d/252d, vol 63d) | **Não** |
| `HIPOTESE_VRP_spec_falsificavel.md` | Barra diária + estrutura a termo do VIX (`^VIX`, disponível no yfinance, dito na própria spec) | **Não** |
| `HIPOTESE_modulo_opcoes_greeks.md` | Tick com lado agressor (OFI, footprint) | **Sim — e a própria spec já concluiu que não se deve fazer** |

### Qualidade das especificações

As três specs falsificáveis são de qualidade **alta e incomum**. Isso não é elogio de cortesia — é relevante para o argumento:

- Critérios de morte pré-registrados **antes** de rodar o forward, com números (`Sharpe ≥ 0,42 e retorno > 0`).
- Correção de método admitida por escrito quando o nulo estava errado (`§2c` do SYMB-1 e do TREND: permutação de retornos testa contra drift zero, não contra o benchmark). Descobrir e registrar que quatro "finalistas" eram `posição = +1 sempre` — o benchmark fantasiado de descoberta — é exatamente o tipo de autocrítica que a maioria dos sistemas quant nunca faz.
- Ressalvas mantidas mesmo quando o resultado é favorável (Bonferroni local e não global; 518 dias é amostra modesta; GLD não existia na janela).
- No VRP, invalidação **estrutural** separada da empírica, com a observação de que a estrutural pode disparar **enquanto se está ganhando** — que é a forma correta de pensar sobre venda de volatilidade.

Fraqueza real da spec do TREND, que vale registrar: ela admite que o alpha absoluto não se distingue do acaso (p = 0,35) e que a melhora de Sharpe do combo não passa em 5% (IC95% [−0,052; +0,409], P(≤0) = 0,062) — e mesmo assim a hipótese está no forward. Isso é defensável (crisis alpha em 9 de 10 piores meses, p = 0,011, em cinco crises distintas), mas significa que **o TREND está no forward por uma alegação estrutural de portfólio, não por edge**. Isso está escrito lá. Está honesto. Mas convém não esquecer disso quando os 6 sessões de forward do TREND (retorno +0,013%) forem interpretados.

E o `HIPOTESE_modulo_opcoes_greeks.md` **já respondeu à pergunta do Databento, contra o Databento**, na §12:

> "Exige dado tick com direção de agressor. Nós temos OHLCV diário — que tem ZERO informação de order flow. [...] É curto prazo e sensível a execução — OFI prevê preço em segundos-minutos; capturar isso encosta no **edge de velocidade**, onde somos fracos (HFT/co-location ganham)."

E aponta a alternativa: **cripto perpétuos**, onde tick + lado agressor vêm de graça pela corretora, 24/7, e o player pequeno consegue operar. O sistema já tem 7 pares de cripto via `ccxt.binance` no manifesto de dados.

### O confronto, sem suavizar

Você escreveu três specs falsificáveis excelentes. **Nenhuma delas precisa de dado intradiário.** A única hipótese do projeto que precisaria — order flow / footprint — foi analisada por você mesmo e concluída como estratégia perdedora para um player pequeno em ações, com a abertura correta apontando para cripto, que é dado que você **já tem de graça**.

Portanto: contratar Databento agora não desbloqueia nenhuma hipótese que exista escrita neste repositório.

O argumento estatístico é pior do que "não ajuda". Barra de 1 minuto multiplica o espaço de busca por aproximadamente 390 (minutos por sessão regular) sem trazer nenhum controle de seleção novo. O motor evolutivo já testou 299 hipóteses únicas na época corrente e 3.866 vereditos de épocas anteriores estão arquivados. O gargalo demonstrado não é falta de espaço para procurar — é que **nada do que foi encontrado sobreviveu ao controle de seleção**. Ampliar o espaço de busca sob controle de seleção constante move exatamente uma coisa: o número esperado de falsos positivos, para cima. O SYMB-1 é a prova disso dentro do próprio projeto — a família Bonferroni é local (300 estruturas desta máquina, não as ~12.000 da sessão), e a spec admite isso.

Há também um custo que não aparece na fatura: `bootstrap_shadow` + backfill + reference (US$ 299/mês, §3.4) é uma obra de infraestrutura de semanas — vintage de ajuste, entitlement, gate, isolamento — feita **antes** de existir a hipótese que a justifique. Isso é procrastinação produtiva na forma mais convincente que ela assume: trabalho difícil, verificável, de aparência institucional, sobre um problema que ainda não é o seu.

**O teste que eu proporia, e que é grátis:** o `HIPOTESE_modulo_opcoes_greeks.md` já identificou que cripto perpétuo dá tick com lado agressor de graça. Se você acredita que existe informação de fluxo explorável, escreva a spec falsificável para BTC/ETH perpétuo com os mesmos critérios de morte pré-registrados das outras três, e teste com dado que já está no `data_lake`. Se ela morrer, você economizou US$ 299/mês e semanas de infraestrutura. Se ela sobreviver, você terá pela primeira vez uma resposta escrita para a pergunta que o Databento deveria responder — e aí a contratação passa a ter alvo.

Enquanto essa resposta não existir por escrito, a decisão correta sobre o Databento é **não contratar**.

---

## 5. O que NÃO fazer

| Não faça | Por quê |
|---|---|
| **Contratar Databento agora** | Nenhuma hipótese escrita exige intradiário (§4). Custo recorrente de US$ 299/mês só de reference, mais metered de histórico, mais semanas de infraestrutura, para ampliar o espaço de busca sem melhorar o controle de seleção |
| **Aplicar todos os patches deste relatório de uma vez** | São 9 pendências em 6 módulos. Um único deploy grande num sistema que já teve 8 reinícios em 46 minutos torna impossível atribuir causa. Uma por vez, com o `code_hash` mudando entre cada uma |
| **Tratar `[WinError 5]` com mais retry** | O retry já falhou. A causa é externa ao Python (indexador/antivírus segurando handle). Rode `handle64.exe` ou exclua a pasta da indexação **antes** de escrever mais código |
| **Ignorar `RECONCILIACAO BLOCKED: unmarked_broker_position`** | É divergência real entre broker e motor de risco, provavelmente originada nos reinícios de 18/08. Precisa de inspeção manual, não de patch |
| **Reduzir o gate para "destravar" o event plane** | O gate de 20 sessões já é frouxo demais (§3.7) e pode ser aprovado sem exercitar o back-ajuste. Afrouxar mais transforma o gate em ritual |
| **Reescrever partições silver in-place (Opção B do vintage)** | Resolve convergência destruindo reprodutibilidade — o ativo que o contrato epistêmico existe para proteger |
| **Adicionar TTL ao cache de `_data_fingerprint`** | Faria o fingerprint mudar sem o dado mudar. Use a chave `mtime_ns + size + universo` (§3.9) |
| **Interpretar os 6 sessões de forward do SYMB-1 (+0,34%) como sinal** | Seis sessões. Ruído. `evidence.minimum_forward_sessions = 60` e `status = INSUFFICIENT_SAMPLE` estão corretos. Não os contradiga informalmente |
| **Julgar qualquer coisa pelo Sharpe de backtest** | Vale para o SYMB-1 apesar dos dois holdouts, das 20.000 permutações e do jackknife. Nada disso é forward |
| **Operar com dinheiro real** | `execution_readiness: BLOCKED`, `research_integrity: DEGRADED`, reconciliação com divergência de posição aberta, zero candidatas autorizadas, e 24 sessões de forward de um mínimo de 60. Nenhuma dessas condições é opcional |

---

## Fontes

- [Databento releases corporate actions dataset and reference data API](https://databento.com/blog/corporate-actions) — preço de entrada da Reference API (US$ 299/mês), gate de vendas
- [Security Master Data & APIs — Databento](https://databento.com/security-master) — cotas de símbolo por plano, erro de autorização ao exceder, US$ 125 de crédito para novos usuários
- [Adjustment factors: Data feed specifications](https://databento.com/docs/venues-and-datasets/adjustment-factors) — status codes `A`/`P`/`R`, rescisão anos após `ex_date`, procedimento de reversão por fator invertido, histórico desde 2018-05-01
- [Security master: Data feed specifications](https://databento.com/docs/venues-and-datasets/security-master) — histórico desde 2005, atualização semanal
- [Metered pricing — Databento Historical API](https://databento.com/docs/api-reference-historical/basics/metered-pricing) — modelo de crédito da API histórica, distinto do de reference
