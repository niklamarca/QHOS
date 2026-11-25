# Herança de pesquisa, Brain e dados — revisão de 26/10/2026

Esta entrega mantém o núcleo funcional do Brain dentro da V2 e acrescenta ferramentas de pesquisa independentes. Criar uma hipótese, calcular uma fórmula ou passar em um teste de software não comprova rentabilidade. Nenhum módulo desta frente envia ordens.

## Origem e alcance da inspeção

A referência preservada é `v1-vivo-20261025`, commit `05cd288cf845c3d7945eab08c2cd7db2ba9f4857`. Os 101 arquivos Python do núcleo instalado `/opt/qhos/brain/brain` foram comparados por SHA-256 com esse retrato: os bytes coincidiam antes das adaptações. Foram incorporados em `src/brain`, incluindo `discovery`, `fable` e `strategies`. `state.json`, credenciais e memória real não foram copiados.

A frente de pesquisa inventariou **213 arquivos**, incluindo **150 Python** e **7 imagens**, nos três grupos: núcleo Brain, antigo `qhos-v2-quant` e antigo `qhos-v2-observability`. Todos receberam hash, classificação e inspeção estática; a leitura semântica aprofundada foi dirigida aos caminhos de estado, observação, hipóteses, evidências, matemática e testes. Isso **não é uma alegação de leitura manual exaustiva de cada linha**, nem cobertura integral de ramos. As sete imagens foram vistas como registros históricos, sem inferir que aqueles serviços ou mercados continuam ativos.

O manifesto por arquivo e os registros de execução ficam no dossiê externo da revisão, em `research_evidence/coverage_manifest.json`. Ele distingue leitura dirigida, inspeção automatizada, importação, associação a suítes e decisão de migração. Os documentos em `src/brain/docs` são referências históricas; números de testes, revisões e estados contidos neles não são novas aprovações da V2.

## O que foi aproveitado

| Origem | Capacidade preservada/adaptada | Limite de interpretação |
| --- | --- | --- |
| Brain `runtime`, `memory`, `executive`, `health`, `hypothesis`, `verification` | Ciclos de observação, incidentes, memória persistente, hipóteses com evidências, orquestração limitada e verificações | Confiança é uma pontuação heurística; não é probabilidade calibrada de acertar trades |
| Brain `discovery` | Agrupamento por causa candidata, controle de ruído, replay, budgets, proveniência, matrizes de falhas | Agrupamento/correlação não estabelece causalidade econômica |
| Brain `fable` e `strategies` | Investigação, contexto, memória, verificadores e estratégias cognitivas sob os limites existentes | Disponibilidade de código não comprova vantagem de um LLM; modo determinístico é o padrão |
| Quant `models.py` | `qhos2.hipoteses`: VWAP, desvio da média, rompimento e médias móveis | Referências matemáticas, com `trade_eligible=false` e `profitability_verified=false` |
| Quant `simulator.py` | `qhos2.laboratorio.simulador`: cenário POV sobre BBO/L1 explícito | Fills hipotéticos; não conhece fila, impacto próprio nem probabilidade real de execução |
| Quant `reconciliation.py` | `qhos2.laboratorio.reconciliacao`: valor monetário por contrato e junção temporal causal | Não soma volumes/preços de contratos diferentes; igualdade de horários não prova comparabilidade econômica |
| Quant `research_validation.py` | `qhos2.laboratorio.validacao`: cenários adversos e invariantes de causalidade/custo | `INSUFFICIENT_EVIDENCE` permanece; perturbações de cenário não são execuções observadas |
| Princípios de governança V1 | `qhos2.pesquisa`: plano, texto, fontes, código, dados e reproduções registrados | Registro local; não é carimbo externo nem prova contra administrador com acesso ao banco |
| Microestrutura histórica | `qhos2.microestrutura`, adaptado ao protocolo atual | Microprice, CVD e vida observada são experimentais; sem comprovação de sequência da bolsa/UTC |

Os servidores antigos de 8020/8021 não foram copiados: usam o contrato L1/SQLite e um decoder externo fixado por caminho/hash. A V2 atual possui seu próprio protocolo de captura/livro e seu terminal. Reusar esses servidores sem adaptar os contratos recriaria dependências e confundiria L1 com MBO.

Os inventários antigos de instrumentos e qualidade histórica continuam sendo evidência de seus conjuntos de dados e datas. Não são um catálogo atual de direitos da conta. CSV/BBO, MBO, funding e barras diárias não podem virar um dataset homogêneo por concatenação. Os cálculos migrados recebem entradas explícitas; a ingestão automática de todo aquele acervo exige política de instrumento, sessão, frequência, rolagem e qualidade por fonte, ainda não implantada aqui.

## Correções concretas no Brain

- Estado, memória, avaliações, scratch e logs agora usam `QHOS2_DATA_ROOT/brain`, sem `QUANT_ROOT.txt`, importação de `core` ou caminho padrão para V1. A observação por HTTP aponta explicitamente para V2 em loopback; respostas externas, redirects e respostas excessivas são recusados.
- `read_only=True` não cria diretórios. Gravações de estado são atômicas e sincronizadas; JSONL recupera a separação de uma última linha interrompida. A leitura de cauda tem orçamento de memória. Isso não substitui backup nem torna o histórico completo disponível em uma consulta de cauda.
- Valores não finitos e pesos fora de faixa são recusados. A mesma identidade de evidência não altera novamente a confiança, inclusive quando reapresentada com polaridade contrária.
- IDs e nomes de scratch não escapam por `../`. O identificador do inventário usa SHA-256, em vez do `hash()` variável entre processos.
- Instalações sem Git mantêm identidade dos fontes. `repo_sha` pode ficar vazio; não é preenchido com um hash de outra natureza. A atestação registra arquivos em disco no início, não prova todos os objetos de bytecode carregados dinamicamente.

O serviço/UI do Brain tem sua migração documentada na frente operacional. Dois testes herdados de um dashboard ainda mais antigo exigem painéis de revisão/agrupamento que **já não existiam na UI atualmente usada**. Eles continuam marcados como `xfail`, agora verificando a UI realmente migrada — desde QHOS-AUD-271 sem `strict`, para que implementar o painel não derrube a suíte; a metade positiva (nenhum controle de reinício, fonte ou nível 3 na UI) virou teste comum. Isso torna a diferença visível; não declara equivalência com aquela tela histórica. Um teste de replay histórico é pulado porque seu artefato de revisão não foi importado.

## Registro de pesquisa e afirmações

`qhos2.pesquisa` usa transações SQLite e JSON canônico sob a raiz de dados. O pré-registro exige pergunta, refutação, família, parâmetros (com o alfa igual ao do orçamento), janelas separadas e referências locais de texto. Ele ocorre depois da calibração e antes do teste. O código e a versão do Python ficam identificados. A avaliação depois da janela verifica código, hashes e qualificação positiva da captura, e registra o resultado em uma cadeia de hashes.

Reexecutar a mesma entrada é permitido e marcado como `REPRODUCAO`. Trocar dados, código ou resultado depois da primeira avaliação daquele plano é recusado. Uma reprodução divergente não é contabilizada como novo sucesso. O avaliador antigo continua disponível, explicitamente como exploração sem pré-registro.

O orçamento é fixo no código desde 28/10 (regra 14 do plano): `ALFA_TOTAL` 5 %, `MAX_TESTES` 3 e `PRAZO_FINAL` 30/04/2027; cada teste usa alfa 5 %/3, o registro recusa o 4º plano, janela ou registro depois do prazo e hipótese nova enquanto a anterior não tiver avaliação registrada. A desigualdade de Bonferroni fundamenta essa divisão; o controle efetivo ainda depende da validade de cada teste/intervalo. Ela não conserta automaticamente um bootstrap inadequado, pouca amostra, dependência entre sessões, experimentos ocultos ou escolha posterior do conjunto de capturas. [NIST: método de Bonferroni](https://www.itl.nist.gov/div898/handbook/prc/section4/prc473.htm).

Textos recebem origem declarada, conteúdo, classe, data local e hash; seu estado é `NAO_VERIFICADO`. Uma URL escrita na origem não foi buscada automaticamente. O texto é dado e não vira comando, autorização ou recomendação aprovada. O mecanismo não afirma veracidade de uma frase de trading. A origem acompanha os hashes; dados sintéticos ficam explicitamente sintéticos, e a avaliação registrada recusa mistura de ambientes.

Limites que permanecem: um administrador pode substituir o banco inteiro, apagar o final do histórico ou alterar o relógio local; o registro não descobre famílias renomeadas ou sessões omitidas. Para validação científica externa, ainda são necessários protocolo de seleção do universo, custos calibrados, dados representativos, dimensionamento de amostra e revisão independente.

## Dados para aprendizado de máquina

`amostras_da_captura` valida a captura inteira antes de devolver amostras. Cada amostra separa:

- atributos disponíveis na recepção: desequilíbrio, quantidades, spread, microprice racional, CVD/volume conhecido e desconhecido, contagens de remoções e vida observada;
- alvo posterior: mudança observada do meio em ticks após o horizonte, com tolerância declarada;
- índice do quadro, horários de disponibilidade/alvo, hashes de dados e código, instrumento e metodologia.

O alvo futuro não entra nos atributos. Estado inválido interrompe janelas pendentes; timestamp repetido não cria linhas duplicadas. Ausência de observação perto do horizonte descarta a amostra, em vez de inventar preço. Quantidade de linhas e estado pendente são limitados.

`particionar_temporal` exige ordem temporal estrita, recusa não finitos e atributos indisponíveis no momento da decisão, purga rótulos que atravessam o fim do treino e deixa um intervalo até o teste. Não embaralha nem ajusta normalização usando o teste. Isso segue a necessidade de separar passado/futuro e pré-processamento, mas não é uma implementação de treinamento ou garantia de generalização. [Scikit-learn: divisão temporal](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html), [vazamento entre treino e teste](https://scikit-learn.org/stable/common_pitfalls.html).

**Não há modelo de ML treinado nesta entrega.** O alvo é movimento do meio, não lucro, fill ou retorno líquido. A recepção local não representa relógio UTC certificado. A vida NEW→DELETE não distingue cancelamento de execução e não conhece o nascimento de ordens vindas do retrato. Esses limites acompanham os dados exportados.

## Fórmulas quantitativas e computação quântica

As hipóteses de referência usam barras fechadas de **30 segundos**. A entrada própria `barras_da_captura` deriva essas barras das negociações qualificadas da V2, em ordem de recepção; não reutiliza silenciosamente candles de outra duração. Primeiro e último períodos são parciais, intervalos vazios não são preenchidos e snapshot do último negócio não soma volume. A barra só fica disponível no quadro posterior que permite fechá-la. Preço × volume usa ticks inteiros.

VWAP é benchmark, não alpha. Os outros cálculos usam janelas anteriores e não incluem o fechamento de decisão no limiar histórico. O teste de +0,5 tick em uma captura sintética demonstra a conta e a separação temporal; não demonstra previsibilidade do mercado. No ES, ponto de US$50 e tick de 0,25/US$12,50 constam no contrato da CME; outras referências precisam ser fornecidas separadamente. [CME, capítulo 358](https://www.cmegroup.com/rulebook/CME/IV/350/358/358.pdf).

O laboratório POV exige eventos explícitos `reference`, `bbo`, `trade`, com sessão, identidade, recepção e timestamp do provedor. Não aceita bytes Rithmic nem interpreta MBO como BBO. As quantidades exibidas são capacidade hipotética, com custos e latência como parâmetros de cenário. `LOCAL_SIMULATION` e `can_execute=false` acompanham o resultado. Não há ligação automática entre hipótese e ordem.

A inspeção dos grupos desta frente não encontrou implementação de circuitos/qubits, backend quântico, QAOA, Qiskit/Cirq ou treinamento quântico. Os cálculos acima são **quantitativos clássicos**. Não foi criado simulador de enfeite para chamar a entrega de quântica. Uma frente quântica real precisa de problema formal, codificação, baseline clássico, recursos/ruído e medição de resultado; não pode ser apresentada como vantagem de trading já validada.

## Uso offline

Depois da instalação independente, sem conexão com corretora:

```text
python -m qhos2.hipoteses /dados/captura --captura
python -m qhos2.pesquisa amostras /dados/captura --max-amostras 100000
python -m qhos2.pesquisa texto afirmacao.txt --origem "fonte declarada"
python -m qhos2.pesquisa registrar plano.json
python -m qhos2.pesquisa avaliar ID_DO_PLANO /dados/captura
python -m qhos2.laboratorio simular cenario.json
python -m qhos2.laboratorio alinhar observacoes.json
```

A captura do protocolo de pesquisa formal permanece ESZ6/CME, com referência de tick 0,25. A CLI de pré-registro recusa execução fora da janela; registrar um plano de novembro em outubro não substitui a calibração concluída. Para obter uma avaliação formal não basta trocar datas no JSON: a pergunta, população e seleção dos dados precisam corresponder ao estudo pretendido.

`simular` recebe `{"events": [...], "config": {...}}`; `alinhar`, `{"observations": {"IDENTIDADE_DO_INSTRUMENTO": [...]}, "at_ns": ..., "config": {...}}`. Os testes em `tests/test_laboratorio_*` contêm exemplos sintéticos completos. As funções não buscam preços, credenciais ou registros do V1.

## Evidência de execução desta frente

- Legado Quant: **90 testes offline aprovados** nos módulos selecionados, incluindo mercado, modelos, POV, reconciliação e tentativas adversariais. O teste que depende do banco real antigo não foi executado.
- Legado Observability: **85 testes offline aprovados**, incluindo protocolo, fila/persistência, adaptador e HTTP local efêmero. A primeira coleta falhou por dependência `protobuf` ausente no ambiente de revisão; foi instalada apenas nesse ambiente e a bateria repetida. Isso não foi tratado como defeito do aplicativo.
- Rodada final desta frente, reunindo núcleo Brain, pesquisa, avaliador, hipóteses e laboratório: **439 aprovados, 1 pulado e 2 xfail em 22,93 s**. As rodadas intermediárias de 395 e 99 testes têm sobreposição e não devem ser somadas.
- Uma ampliação separada de testes antigos de Fable/gateway/core teve **74 aprovados e 12 incompatibilidades/harness**. Sete dependem do catálogo `tests/brain_scenarios/heldout.py`, ausente tanto no arquivo histórico quanto no checkout consultado. Outros esperam caminhos do antigo dashboard/`core.llm_client`; um espera score numérico onde a implementação atual declara evidência incompleta com `None`. O teste cognitivo adicional nem chegou a coletar pelo mesmo catálogo ausente. Esses casos **não foram contados como aprovados**; a integração de avaliação com aquele catálogo histórico permanece não reproduzida.
- Os testes novos executam importação dos 101 módulos, ciclo e persistência/restauração do Brain, leitura sem escrita, linha JSONL interrompida, escrita atômica, evidência repetida/não finita, caminhos de scratch, limites de rede local, governança e causalidade dos dados.

O harness de avaliação paga antigo foi preservado somente como apoio de teste: funções puras de classificação foram mantidas; acesso a provedores e leitura de candidatos reais foram desativados explicitamente. Não foi chamado LLM externo. Nenhum login Rithmic, execução de ordens ou promoção operacional foi realizado por esta frente.

A revisão cruzada reproduziu e corrigiu quatro lacunas antes da publicação: representação de janela por tupla contornava quota; dados oficiais divergentes eram recusados somente depois do cálculo; FIFO podia bloquear leitura; somas de números finitos extremos podiam transbordar. O registro agora compara uma cópia JSON canônica, mantém reserva transacional durante a avaliação, recusa arquivos não regulares antes da leitura e verifica agregados numéricos. Reproduções concorrentes divergentes também são recusadas antes do avaliador.
