# QHOS — Guia Operacional

> **STATUS: GUIA HISTÓRICO EM REVISÃO.** Procedimentos de inicialização podem
> ser úteis, mas nomes e estados científicos antigos não são autoritativos.
> Consulte `CURRENT_ARCHITECTURE.md` e `governance/EPISTEMIC_CONTRACT.md`.

*Como operar o sistema. Escrito para ser lido daqui a seis meses, quando ninguém lembrar dos detalhes.*

---

## 1. Ligar e desligar

> **MUDOU EM 14/08/2026:** o servidor agora sobe **sozinho ao ligar o Windows**, sem precisar de login. Depois de um reinício ele **já está no ar** — não rode `QHOS_RESTART.bat`, isso mataria o que subiu para subir outro. Use `QHOS.bat`, que apenas anexa a janela.

| Ação | O quê |
|---|---|
| Depois de reiniciar o PC | **nada** — o servidor já subiu (tarefa `QHOS-Server`, 90 s após o boot). Só abra a janela com `QHOS.bat` |
| **Mudou código** | **`QHOS_RESTART.bat`** — encerra o antigo, sobe o novo, abre o MT5 e o terminal |
| Só abrir o terminal | `QHOS.bat` (se o servidor já estiver no ar, apenas anexa) |
| Sem janelas pretas | `QHOS_silencioso.bat` |
| **Desligar de verdade** | Ctrl+C na janela do servidor (a que mostra o log) |
| Mudou código? | **sempre** `QHOS_RESTART.bat` — o servidor carrega código só no boot |
| Mudou só o visual? | **F5** na janela do terminal (o HTML recarrega por request) |

**Regra:** fechar a janela do terminal **não** para o sistema. O servidor continua rodando (os braços seguem gravando).

---

## 2. A interface (8 seções)

`Ctrl+K` abre a busca (seções, ativos, ações). Teclas `1`–`8` navegam direto.

1. **Dashboard** — veredito honesto, KPIs, curva dos 5 braços, universo, notícias, macro
2. **Gráficos** — candles com EMA9/EMA21, volume, crosshair. Arrastar = mover · roda = zoom · arrastar na régua direita = escala · duplo-clique = reset
3. **Pesquisa** — Hall of Fame (⚠ Sharpe de backtest é ficção), hipóteses, correlação 63d, aprendizado por contexto
4. **Braços** — desempenho comparativo e **portões de morte** pré-registrados
5. **Macro** — juros/dólar/petróleo/ouro, fluxo (proxy), eventos do Fed, notícias
6. **Risco** — drawdown, P&L, exposição, políticas e eventos de risco
7. **Sistema** — log ao vivo, CPU/RAM/uptime, saúde dos 10 componentes
8. **Governança** — modo de autonomia, audit trail, journal

---

## 3. Os cinco braços (o experimento)

Todos correm no **mesmo relógio forward**, em paper. O SPY é o **nulo** — a coisa a bater.

> **São QUATRO relógios, não cinco.** O braço MANUAL foi aposentado: ele existia para medir a única fonte de informação deste sistema que não está em paper publicado — o julgamento do operador. Sem trading manual, não há entrada. **Consequência estratégica: tudo que o sistema mede hoje vem de dado público analisado com método público.** Nenhuma vantagem informacional, por construção.

| Braço | Alegação | Ledger |
|---|---|---|
| **Sistema GA** | regras técnicas evoluídas | `data_lake/track_record/nav.csv` |
| **TREND** | diversificação + crisis alpha | `trend_nav.csv` |
| **SYMB-1** | timing que bate o índice | `symb1_nav.csv` |
| **CARRY** | funding de perpétuos, delta-neutro — **SHADOW desde 19/08/2026** (observa funding liquidado, sem fill; relógio próprio de dia UTC, 24/7) | `crypto_carry_nav.csv` |
| ~~**MANUAL**~~ | ~~seu trading no MT5~~ — **APOSENTADO em 14/08/2026**: o operador não opera manualmente, então não há decisão humana para medir. Desligado em `config.manual_arm_enabled=False`. Reativar só com conta **real** que tenha histórico de operações próprias | — |
| **SPY** | "só segure o índice" | coluna `spy_close` |

**Portões de morte** (≥6 meses de forward, pré-registrados — não podem ser movidos):

- **TREND** morre se: correlação c/ SPY > 0,3 sustentada · MDD > 20% · perder junto num crash de equity · combo não melhorar o Sharpe do SPY.
- **SYMB-1** morre se: não bater o SPY ajustado a risco **e** não melhorar o combo · MDD > 25% · Sharpe forward < 0 sustentado · correlação > 0,85.
- **CARRY** (shadow, ≥60 sessões UTC): morre pelos critérios de §D.1/§E da `HIPOTESE_CRYPTO_spec_falsificavel.md` — funding 90d ≤ 0 sustentado desliga por regra; graduação a paper reavalia base de capital, risco de exchange e concentração BTC/ETH, com aprovação humana.
- **Proibido** ajustar parâmetros durante o forward. Qualquer mudança = novo teste, relógio zera.

---

## 4. Autonomia (aba Governança)

| Modo | O que faz |
|---|---|
| MANUAL | nada é executado automaticamente |
| COPILOT | bots sugerem, humano decide |
| **SUPERVISED** *(atual)* | bots executam dentro do Risk Engine |
| AUTONOMOUS | operação autônoma nos limites de risco |

O gate roda **antes** do Risk Engine, dentro do `ExecutionEngine.submit()`. Toda tentativa bloqueada vai para o audit trail.

---

## 5. Monitoramento remoto (Discord)

Vigia independente (`remote_monitor.py`), sobe sozinha no login via tarefa `QuantHPC-RemoteMonitor`.

- Status a cada **3 horas** · alerta 🔴 se o servidor cair (>3 min sem heartbeat) · 🟢 quando voltar · 🔁 em restart.
- **Silêncio prolongado = PC desligado.** É o alarme de queda de energia.
- Reiniciar a vigia: `powershell -File kill_server.ps1` não serve aqui — use `schtasks /Run /TN "QuantHPC-RemoteMonitor"` (e se o lock travar, zere `data_lake/remote_monitor.lock`).

---

## 5b. Horário de mercado (portão)

O sistema é de **barra diária**. Em 12/08/2026 descobriu-se que ele ingeria e agia com o pregão aberto, gravando no ledger uma vela **inacabada** — o mesmo dia foi registrado com fechamento 772,687 (durante o pregão) e 772,490 (liquidado). Ledger forward contaminado.

`core/market_clock.py` (sem dependências externas: feriados NYSE por regra, DST por regra) define:

| Estado | Ingestão | Execução |
|---|---|---|
| PRE (antes das 9h30 ET) | ✅ a partir de 9h15 | ❌ |
| OPEN (9h30–16h ET) | ✅ | ❌ *a vela ainda vai mudar* |
| SETTLING (16h00–16h30 ET) | ✅ | ❌ |
| **POST (16h30 ET → meia-noite)** | ✅ até 19h30 | ✅ **única janela** |
| Fim de semana / feriado | ❌ | ❌ |

**Hot path gateado:** ingestão, features, sinais, rebalance, passo dos braços.
**Cold path segue 24/7:** evolução, regime, risco — é pesquisa sobre histórico, não toca no mercado. Desligar isso não protegeria nada e só desperdiçaria as horas ociosas.

Dupla proteção: além do agendador, `_prices()` de cada braço descarta qualquer barra não liquidada — assim um restart no meio do pregão não reintroduz o problema.

**Limitação conhecida:** fechamentos extraordinários (luto nacional, furacão) não estão no calendário por regra. Nesses dias o provider simplesmente não entrega barra nova e o braço não avança — falha segura, mas o log vai dizer "pregão" indevidamente.

---

## 5c. Sobreviver a reinício (o incidente de 13/08/2026)

**O que aconteceu:** o Windows Update reiniciou a máquina às **08:41:03** (Evento 1074, `MoUsoCoreWorker.exe`, "service pack planejado"). A última linha do log é de 08:41:02.507 — meio segundo antes. O sistema ficou **10h30 fora do ar**, porque as tarefas agendadas disparavam *ao fazer login* e ninguém logou até 19:12.

Diagnósticos errados que gastaram tempo antes da resposta certa: hibernação (suspensão estava em "Nunca") e falta de memória (o processo usa ~2,5 GB com 17 GB livres). **O Visualizador de Eventos resolveu em 10 segundos o que a especulação não resolveu em meia hora.**

### As três camadas de defesa, hoje

| Camada | O quê | Onde |
|---|---|---|
| 1. Voltar sozinho | tarefas `QHOS-Server` (+90 s) e `QHOS-RemoteMonitor` (+150 s) com gatilho **ao ligar o Windows**, `LogonType S4U` (sem senha armazenada, sem login) | `setup_autostart.ps1` |
| 2. Reiniciar na madrugada | Horário ativo do Windows Update **06:00 → 21:00** (configurado em 14/08). Reboot só entre 21h e 6h. *Nota: 21:00–01:00 ainda cai dentro da janela em que os braços gravam (pós-fechamento). Com o autostart isso custa ~90 s e os braços são idempotentes — perda nula no ledger, só o estado em memória da evolução se perde. Se quiser blindar, o máximo permitido é 18 h: 06:00 → 00:00.* | Configurações |
| 3. Avisar | vigia no Discord + telemetria de memória a cada 30 s (`data_lake/memoria.jsonl`), alerta em 90 % de RAM | `remote_monitor.py`, `main.py` |

Custo de um reinício hoje: **~90 segundos**. Antes: até 10 horas.

**Limite que continua de pé:** a vigia detecta o *servidor* caindo, não o *computador* parando. Se a máquina desliga, ela desliga junto e o silêncio é indistinguível de normalidade. Vigilância real contra a máquina parar exige um serviço externo esperando um sinal periódico.

### Comandos de verificação

```powershell
Get-ScheduledTask -TaskName "QHOS-*" | Select TaskName, State
(Get-ScheduledTask -TaskName "QHOS-Server").Triggers.CimClass.CimClassName   # deve ser MSFT_TaskBootTrigger
Get-ScheduledTaskInfo -TaskName "QHOS-Server" | Select LastRunTime, LastTaskResult   # 0 = subiu
```

Para investigar qualquer sumiço futuro, **antes de teorizar**:

```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; StartTime='AAAA-MM-DD HH:MM'} |
  Where-Object {$_.Id -in 41,42,107,1074,6008,2004} | Select TimeCreated,Id,Message | Format-List
```

`42/107` = suspendeu/acordou · `41`/`6008` = desligou sem avisar · `2004` = memória esgotada · **`1074` = reinício comandado (Windows Update)**.

---

## 5d. O banco em RAM — a falha silenciosa que durou 2 meses

Até 13/08/2026, `data/storage.py` desistia de abrir o DuckDB após **5 s** e caía para `:memory:` com um simples `WARNING`. Consequência medida no histórico: **20 dos 102 boots** rodaram sem persistência — uma em cada cinco sessões descartava tudo que "aprendia" ao fechar. Era isso que fazia a evolução parecer que recomeçava sozinha e o warm start achar o banco vazio.

Causa raiz: `kill_server.ps1` matava apenas quem **escutava a porta 8000**. Um processo travado para de escutar mas continua segurando o arquivo do banco — sobrevivia ao kill. (E o fallback via `netstat` usava `$pid`, variável automática somente-leitura no PowerShell: aquele ramo **nunca executou**.)

**Hoje:** o `kill_server.ps1` varre `Win32_Process` e mata qualquer `python.exe` do projeto, escutando ou não, e só devolve o controle quando consegue **abrir o arquivo do banco em modo exclusivo**. Se não conseguir, avisa em caixa alta. O `storage.py` tenta 3× com 45 s, loga em `ERROR` e **manda alerta no Discord**.

Ao subir, confira no console: `banco LIVRE - pode subir (persistencia garantida)`.

---

## 5e. Ativação segura do Databento

Execute `DATABENTO_SETUP.bat` uma única vez e cole a chave quando solicitado.
O instalador restringe o `.env` ao usuário atual/SYSTEM/Administradores e
restaura o arquivo anterior se o diagnóstico falhar. Depois, aplique
`QHOS_RESTART.bat`.

O diagnóstico só declara sucesso se todos estes gates passarem:

1. autenticação, dataset e schema disponíveis;
2. custo do pedido abaixo de US$ 0,25 e gasto estimado diário abaixo de US$ 1;
3. DBN final arquivado com checksum;
4. todos os símbolos presentes e limite de registros não atingido;
5. cobertura da sessão regular e fechamento reconciliado com a fonte diária;
6. adjustment factors e security master point-in-time sincronizados.

Depois do probe, o servidor acumula uma janela shadow de 20 sessões, no máximo
uma sessão por ciclo. O Databento permanece isolado da pesquisa até completar a
janela e todos os relatórios estarem reconciliados. `READY_FOR_PROMOTION` não é
autorização automática: `DATABENTO_PROMOTE_TO_RESEARCH` continua falso.

Se uma queda ocorrer durante uma compra, o sistema reutiliza o DBN final. Se não
for possível provar que o arquivo terminou, grava `RECOVERY_REQUIRED` e bloqueia
nova cobrança. Nunca apague manualmente `events/_catalog/acquisitions` para
"destravar": primeiro confirme a cobrança e o arquivo no portal Databento.

Os dados pagos (`events`, `events_native`, `events_silver` e `events_reference`)
entram no backup verificado diário. Ao atingir 200 GB ou restarem menos de 50 GB
no disco, novas compras são bloqueadas; bronze pago não é apagado sozinho.

---

## 6. Ferramentas offline (pasta `lab/`)

Rodam com `python lab/<arquivo>.py`. Não tocam no sistema vivo.

| Arquivo | O que faz |
|---|---|
| `permutation_engine.py` | teste de permutação — a hipótese é distinguível do acaso? |
| `symbolic_engine.py` | motor de regressão simbólica (1 geração por execução) |
| `candidate_autopsy.py` | autópsia por ativo/período/custo/jackknife |
| `holdout_burn.py` / `holdout_pre2004.py` | queima de holdout (uso único e deliberado) |
| `vrp_measurement.py` / `vrp_gated.py` / `vrp_sizing.py` | bateria do prêmio de volatilidade |
| `trend_test.py` / `trend_robust.py` | teste do trend-following |
| `symb1_source_sensitivity.py` | a estrutura depende da fonte/ajuste? (decidiu o Databento sem comprar) |
| `crypto_edge_measurement.py` | bateria da HIPOTESE_CRYPTO: funding carry (sobreviveu) e imbalance de agressor (falsificado) |
| `basis_carry_measurement.py` | cash-and-carry de trimestrais (falsificado em B-3: beta de BTC) |
| `funding_dispersion_measurement.py` | spread de funding Binance/Bybit/OKX (falsificado em D-2) |
| `funding_extreme_measurement.py` | short em extremo de funding (falsificado: sem IC) |
| `monte_carlo_lab*.py` | laboratório de contas com barreiras |

Fora do `lab/`: `consolidation_scorecard.py` (DSR/PSR formal), `monitor_learning.py` (raio-x rápido), `check_mt5.py` (diagnóstico do braço MANUAL).

---

## 7. Solução de problemas

| Sintoma | Causa provável / o quê fazer |
|---|---|
| Painéis "carregando" eternos | servidor antigo em memória → `QHOS_RESTART.bat` |
| MANUAL dormente | esperado — braço aposentado (`manual_arm_enabled=False`) |
| Discord mudo | vigia caiu → `schtasks /Run /TN "QHOS-RemoteMonitor"` |
| `porta 8000 ocupada` | servidor antigo vivo → o `QHOS_RESTART.bat` mata via `kill_server.ps1` |
| Erro de crédito LLM no log | inofensivo — agentes LLM são opcionais |
| Dados de um ativo estranhos | ingestão detecta drift de ajuste e re-baixa sozinha; ver log `[DADOS]` |

---

## 8. As regras que não se negociam

1. **Backtest é ficção.** Já foi provado neste sistema: Sharpe 5,25 no catálogo → expectativa **negativa** por trade quando re-simulado honestamente.
2. **Só o forward conta** — e ainda precisa bater o SPY líquido de custo *e* sobreviver ao Deflated Sharpe.
3. **Nada de mexer no sistema vivo** durante o forward. Ideia nova vira hipótese no papel ou pulso no motor simbólico offline.
4. **Portões pré-registrados não se movem** depois de ver o resultado.
5. **Lucro garantido não existe.** O objetivo do sistema é medir a verdade, não produzir esperança.
6. **O nulo é "segurar a cesta", nunca "acaso".** Embaralhar retornos cria um nulo de drift zero — qualquer estratégia majoritariamente comprada o bate por construção. Foi o erro que inflou o SYMB-1 (`p=5×10⁻⁵` contra o nulo errado; margem real sobre buy&hold `t=+0,55`). Toda estrutura nova enfrenta três nulos: moeda de mesmo viés, buy&hold, e o **piso de seleção** √(2·ln n) — se a margem é menor que o que a própria busca produziria por acaso, não é descoberta.
7. **Barra parcial não é observação.** Só se age sobre vela liquidada (§5b). Em 12/08/2026 o mesmo dia foi gravado com dois fechamentos diferentes.

**Estado em 2026-08-14:** o sistema sobrevive a reinício sozinho; a máquina de medir foi consertada (janela de dados completa, regime preditivo, custo de giro no fitness). Nada disso produziu edge — só tornou confiável o que o sistema afirma ter aprendido. *Estado anterior (2026-08-12):* forward de lado, perdendo do SPY, sem edge demonstrado. TREND e SYMB-1 com 1 dia de forward válido (o dia 12/08 foi revertido por contaminação de barra parcial e será reprocessado com dado liquidado). Os portões abrem em ~6 meses.
