# Calibração do Teste 8 do validador (V1)

Data: 2026-10-23. Script: `calibracao_teste8.py` (nesta pasta). Base de código: snapshot `aab7dad` do runtime do WSL.

## Pergunta

O Teste 8 do `validation/validator.py` exige que a candidata supere o buy&hold do próprio ativo e o SPY, com
nível de significância corrigido por Bonferroni: `alpha = 0,05 / max(n_test, 2)`. O número de reamostragens do
block bootstrap é `B = clip(ceil(5 / alpha), 4000, 100000)`. A pergunta é: com esse `alpha` e esse `B`, **é
possível aprovar alguém**, e a partir de quantas hipóteses deixa de ser?

## Método

- A regra de `alpha` e `B` é replicada em `regra_validador()`. A função de bootstrap é a **real**
  (`validation/block_bootstrap.py::paired_sharpe_draws`), carregada do código do V1 sem alterá-lo.
- O menor p-valor alcançável com `B` reamostragens é `1 / (B + 1)`. Se for maior que `alpha`, aprovar é impossível.
- Séries sintéticas normais i.i.d.: ativo com média 7% e volatilidade 16% ao ano, candidata = ativo + ruído de
  tracking error (3%, 6%, 10%) e **sem nenhuma vantagem** (controle negativo). Blocos de 21 dias.

## Resultados (reexecutados em 2026-10-23)

### A) Limite estrutural

| n_test | alpha | B | p mínimo | Aprovar é |
|---:|---:|---:|---:|---|
| 1 | 2,5e-02 | 4.000 | 2,5e-04 | possível |
| 100 | 5,0e-04 | 10.000 | 1,0e-04 | possível |
| 1.000 | 5,0e-05 | 100.000 | 1,0e-05 | possível |
| 4.836 | 1,03e-05 | 100.000 | 1,0e-05 | possível |
| 5.000 | 1,00e-05 | 100.000 | 1,0e-05 | possível (no limite) |
| **5.001** | 9,998e-06 | 100.000 | 1,0e-05 | **impossível** |
| 6.000 | 8,33e-06 | 100.000 | 1,0e-05 | impossível |
| 10.000 | 5,0e-06 | 100.000 | 1,0e-05 | impossível |

`B` tem teto de 100.000, então o p mínimo nunca desce de 1e-5. Com `n_test >= 5001`, `alpha` fica abaixo disso.

### B) Controle negativo

Sem vantagem alguma, `alpha` = 5%, `n_test` = 1, `B` = 4.000, 300 simulações: **10 falsos positivos (3,3%)**.
Coerente com o nível nominal. O teste não está quebrado, está caro.

### C) Menor vantagem detectável (diferença de Sharpe sobre o buy&hold)

| Anos | Tracking error | n_test = 1 | 100 | 1.000 | 4.836 |
|---:|---:|---:|---:|---:|---:|
| 4 | 3% | 0,15 | 0,30 | 0,35 | 0,38 |
| 4 | 6% | 0,29 | 0,57 | 0,68 | 0,74 |
| 4 | 10% | 0,43 | 0,87 | 1,03 | 1,13 |
| 6 | 3% | 0,12 | 0,24 | 0,29 | 0,32 |
| 6 | 6% | 0,22 | 0,45 | 0,53 | 0,58 |
| 6 | 10% | 0,37 | 0,74 | 0,87 | 0,96 |
| 10 | 3% | 0,10 | 0,20 | 0,23 | 0,26 |
| 10 | 6% | 0,19 | 0,38 | 0,45 | 0,49 |
| 10 | 10% | 0,29 | 0,58 | 0,69 | 0,76 |

## Conclusões

1. **Existe uma parede estrutural em `n_test = 5001`**: nesse valor nenhuma candidata pode ser aprovada, seja
   qual for o dado. No código (`validator.py`, linhas ~354-356), `n_test = max(hipóteses únicas históricas,
   tamanho do cache de julgamentos + 1, 2)`. O cache é limitado a 5000 entradas e, ao passar disso, descarta as
   1000 mais antigas (linhas ~628-630). Logo `n_test` oscila em dente de serra entre ~4001 e 5001 e toca o valor
   impossível quando o cache está cheio. Em 2026-10-23 (17:30 UTC) o cache tinha 4955 julgamentos (`n_test` ≈
   4956, 45 abaixo do topo); em 2026-10-22 o log mostrava 4031. As hipóteses únicas históricas eram 1004 e não
   mandam no máximo.
2. **Antes da parede a exigência já é alta**, e cresce com o número de tentativas. Com `n_test` perto de 4.800 e
   6 anos de dados, uma candidata com tracking error de 6% precisa de uma diferença de Sharpe de ~0,58 sobre o
   buy&hold. Mais tentativas significam exigir mais de cada uma.
3. **O zero de aprovadas do V1 é, em parte, consequência do desenho**: muitas tentativas com correção de
   Bonferroni. Isso é coerente com o que o `CURRENT_ARCHITECTURE.md` do V1 já diz: mais CPU aumenta tentativas,
   não conhecimento.

## Decisões recomendadas

- **Não editar `validator.py` nem `core/config.py`.** Eles fazem parte do hash de 12 arquivos da época
  científica; editar abre uma época nova e invalida a comparação com tudo o que já foi medido.
- **Aposentar o GA.** Com o cache em dente de serra o V1 vive entre `n_test` 4001 e 5001, onde a exigência é
  máxima, e aprovou 0 candidatas em 4955 julgamentos.
- **O V2 tem contador próprio:** cada hipótese pré-registrada conta como 1 teste, não como uma entre milhares.

## Limitações

- Dados sintéticos normais i.i.d.: dados reais têm caudas pesadas e dependência, o que pode piorar o poder do teste.
- A regra de `n_test` (`max(hipóteses únicas históricas, cache + 1)`) foi lida do código do snapshot `aab7dad`; o
  validador completo não foi executado.
- Como o cache descarta julgamentos antigos, `n_test` subconta o total real de hipóteses já testadas: a
  correção de Bonferroni do V1 é, no longo prazo, menos severa do que o histórico justificaria.
- 300 simulações no controle negativo: a taxa de 3,3% tem incerteza amostral de alguns pontos percentuais.
