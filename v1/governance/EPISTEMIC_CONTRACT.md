# Contrato epistemológico do QHOS

Este contrato é normativo. O estado de máquina autoritativo fica em
`data_lake/governance/CURRENT_STATE.json`; textos históricos não substituem
esse snapshot.

## Vocabulário permitido

| Termo | Significado exato |
|---|---|
| `PROCESS_ONLINE` | bootstrap terminou e o heartbeat responde; não afirma qualidade científica |
| `FINALIZED` | a visão entregue à pesquisa contém somente barras liquidadas |
| `PARTIAL` | a aquisição contém barra ainda mutável; ela não pode entrar na pesquisa |
| `FALLBACK` | regra constante/de contingência, não inferência de modelo |
| `BACKTESTED` | executado sobre histórico; não significa aprovado |
| `INTERNAL_TESTS_PASSED` | passou os gates internos nesta versão de código e dados |
| `SHADOW` | observado por sessão liquidada, sem ordem ou fill |
| `PAPER` | fill simulado; não é execução real |
| `LIVE_CAPITAL` | fill confirmado por broker real e identificado |
| `INSUFFICIENT_SAMPLE` | há observações, mas não há amostra mínima pré-definida |
| `FALSIFIED` | falhou em pelo menos um gate pré-definido |

## Termos reservados

- `edge` só pode ser usado como afirmação após forward líquido de custos,
  benchmark declarado, amostra mínima e controle de seleção/testes múltiplos.
- `aprendeu` exige mudança reproduzível de parâmetros/política e melhoria OOS
  num teste pré-definido. Registrar ou agrupar retornos é apenas observação.
- `real` é reservado a dados/fills confirmados por fonte real identificada.
- `validado` deve ser substituído por `aprovado nos testes internos`, com hash
  de código e dados.
- `operacional` descreve processo; nunca implica dados íntegros, pesquisa válida
  ou prontidão de execução.
- `certificado`, `comprovado` e `garantido` são proibidos como conclusão do sistema.

## Hierarquia de evidência

`GENERATED → BACKTESTED → INTERNAL_TESTS_PASSED → SHADOW → PAPER → LIVE_CAPITAL`

Cada seta exige artefato próprio. Nenhum estágio herda automaticamente o nome
do estágio seguinte. Reprovação leva a `FALSIFIED`; mudança de código, dados ou
DNA invalida o julgamento em cache.

## Limites dos dados atuais

Os provedores atuais entregam principalmente OHLCV diário público. Eles não
observam livro de ofertas histórico, fila, lado agressor confiável, spread
realizado, impacto ou fill real. Qualquer campo de “flow” no dashboard é proxy
descritivo e não pode ser apresentado como microestrutura observada.

