# QHOS Terminal — horário de Brasília e seletor de fuso

Esta entrega implementa o item D de usabilidade do terminal servido pelo coletor, como extensão da tarefa 9 (percurso e luzes), preservando a conexão automática da tarefa 8 da etapa 0, seção 4.1 do plano QHOS V2.

## Identidade e escopo

A base desta extensão é o resultado já entregue da conexão automática: `a33e3ba78328664a`. A base anterior daquela tarefa foi `8e98e87322ad147b`. O arquivo continua único, com os mesmos 16 módulos em base64 no importmap, sem dependência nova e sem script externo.

Somente seis módulos mudaram nesta extensão: `app.js`, `market.js`, `chart.js`, `flow-visuals.js`, `execution-ui.js` e `microstructure-ui.js`. Os outros dez são idênticos à base desta extensão, incluindo `qhos-bridge.js` e `contract-ui.js`. O HTML também ajusta o rótulo inicial do relógio. Os hashes completos e os primeiros 16 caracteres de cada arquivo estão em `SHA256.txt`.

## Uso

Abra Configurações → Horário do terminal → Fuso de exibição. As opções são Brasília (padrão), UTC e Nova York. A seleção aplica-se imediatamente e persiste em `qhos-v2-preferences.timeZone`, como uma das três strings permitidas. Reabrir a página mantém a escolha.

Para o instante `2026-10-25T02:30:00.123Z`, o resultado esperado é:

| Fuso | Exibição |
|---|---|
| Brasília | 24/10/2026 23:30:00.123 BRT |
| UTC | 25/10/2026 02:30:00.123 UTC |
| Nova York | 24/10/2026 22:30:00.123 EDT |

Os cabeçalhos sem instante específico indicam BRT/BRST ou EST/EDT; cada horário completo usa a sigla correspondente ao seu próprio instante. Datas de vencimento são datas de calendário: aparecem em DD/MM/AAAA, sem deslocamento de dia pelo fuso.

## Mudanças por função

| Arquivo | Funções e mudança |
|---|---|
| `market.js` | Novos `displayInstant`, `displayFormatter`, `displayParts`, `displayFacade` e `displayTime`: formatação compartilhada por `Intl.DateTimeFormat`, datas em dia/mês, intervalos, milissegundos, sigla obtida para cada instante, seleção restrita aos três fusos. `forZone('UTC')` cria um formatador independente para exportação. `sessionName` adapta apenas a apresentação de nomes automáticos reconhecidos e mantém o conteúdo original do registro. |
| `app.js` | Inicialização e `persistPrefs` incluem apenas a preferência de fuso. Novos `displayStamp`, `timeMarkup`, `rangeMarkup`, `refreshDisplayTimes`, `expiryText` e `applyDisplayTimeZone`; este último redesenha apresentações já existentes sem chamar carregamento, `setData` ou agregação. `openSettings` acrescenta o seletor. |
| `app.js` | `collectorLightPresentation`, `renderDetails`, `scopeNote`, `renderFlow`, `renderAnalysis`, `renderHealth`, `renderStatus`, `renderStorageStatus`, `renderSessionList`, `paintReplay`, `openChartContext` e a inspeção do footprint passam pelo formatador. `renderWatchlist` e `openDataMethod` mostram vencimento em dia/mês. |
| `app.js` | `sessionName` continua gerando o nome persistido em UTC. `openSessions` apresenta o nome automático convertido, mas conserva o nome original quando o usuário o aceita sem editar; renomeação e exclusão usam a mesma apresentação. `downloadSession`, gravação e timestamps dos registros não mudaram. |
| `chart.js` | `timeAxisLabel`, `drawTimeAxis` e `renderOverlay` formatam eixo e crosshair; o crosshair mede a largura do texto. Novo `refreshTimeZone` solicita apenas repintura. `exportImage` usa um formatador UTC local e restaura o da tela em `finally`, inclusive quando a exportação falha. |
| `flow-visuals.js` | `clock`, `dateClock`, `drawCVD`, `drawHeatmap`, `drawTimeAxis` e `showInspection` formatam texto, eixo, descrição acessível e tooltips. Novo `refreshTimeZone` conserva dados e inspeção fixada. |
| `execution-ui.js` | `hover`, `renderInspector`, `mountTable` e `openList` apresentam o horário selecionado. `renderHistorySummary` e `refreshTimeZone` mantêm instante da captura do histórico, eventos, filtros, seleção e paginação. |
| `microstructure-ui.js` | `dateTime`, `nsTime` e `expiryText` formatam somente a apresentação. `render`, `renderOverview`, `renderPipeline`, `renderRithmicPath`, `renderQuotes`, `renderMBOEvents`, `renderQueueResult`, `renderStudyResults`, `renderExperimentResults` e `renderSessionOptions` usam o fuso escolhido. `refreshTimeZone` reutiliza resultados guardados sem executar novamente os estudos. |
| `index.html` | Reincorpora os seis módulos e altera o rótulo inicial do relógio. Os estilos de botões desativados e a faixa de luzes permanecem preservados. |

Alertas foram inspecionados: a base não exibe horário e não registra timestamp de disparo nessa lista, apenas a condição e se ela foi atingida. Nenhum horário foi inventado ou acrescentado.

## Preservação de dados

Os timestamps recebidos, buckets, janelas de 60 segundos, cursor de replay, gravação de sessões e JSON exportado continuam em UTC. Os módulos de feed, fluxo, sessões, execuções, risco e motor de pesquisa são idênticos à base. `ES_LIBERADO_EM`, o predicado da trava do ES e os cortes do estudo permanecem iguais. O simulador continua bloqueado para Rithmic.

Na troca de fuso, os testes verificam zero chamadas a `chart.setData`, zero buscas de dados e igualdade dos candles, gravações, histórico de execuções e resultados retidos. O PNG exportado usa UTC; ao terminar, a tela continua no fuso escolhido.

O código não soma nem subtrai horas para converter fusos. A sigla BRT/BRST é uma tradução do nome/offset devolvido por `Intl` para aquele instante; não altera o timestamp. Referências consultadas: [Intl.DateTimeFormat](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/DateTimeFormat) e [formatToParts](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/DateTimeFormat/formatToParts).

## Erros encontrados e correções

1. **Horários fixados em UTC em vários módulos.** Consequência: telas não acompanhavam uma preferência comum. Correção: formatador compartilhado e atualização apenas dos elementos de apresentação.
2. **Nomes automáticos de sessão contêm horário UTC persistido.** Consequência de uma conversão ingênua: alterar dados exportados ou deixar a lista com horário incoerente. Correção: converter só o nome exibido e conservar o nome original ao salvar sem edição.
3. **Nomes antigos omitem o ano.** Consequência: uma sessão iniciada em dezembro e salva em janeiro poderia aparecer com ano errado. Correção: reconhecer o padrão pelo ativo e usar os instantes já registrados para resolver o ano; nomes livres permanecem livres.
4. **Exportação PNG copiava o canvas da tela.** Consequência: o arquivo passaria a conter o fuso selecionado. Correção: renderização temporária em UTC com restauração garantida.
5. **Vencimentos ainda apareciam em AAAA-MM-DD.** Correção: apresentação em DD/MM/AAAA, sem converter uma data de contrato em timestamp.

## Verificação manual

1. Abra o terminal em uma sessão de teste com eventos no instante fixo da tabela e selecione Brasília. Confira relógio, Time & Sales, eixo/crosshair, captura, percurso, tooltips das luzes, sessões, execuções e telas de pesquisa.
2. Alterne para UTC e Nova York. Os horários devem seguir a tabela; o número de candles e seus timestamps devem permanecer iguais. A troca não deve solicitar novamente os dados.
3. Volte a Brasília e pressione F5: a escolha deve permanecer. Na origem autorizada do coletor, a conexão automática continua funcionando.
4. Confira uma data de dezembro de 2018 em Brasília e datas de janeiro/julho em Nova York; os testes automatizados incluem as mudanças de sigla e hora desses instantes.
5. Exporte JSON e PNG: o JSON conserva os timestamps e campos em UTC; o eixo do PNG indica UTC. A tela conserva sua preferência após a exportação.
6. Verifique o ES antes da liberação: botões de ordem continuam desativados e as regras de MBO/estudo continuam aplicadas.

As fixtures usam somente um servidor HTTP+WebSocket falso em portas isoladas. Elas não acessam o coletor real nem acrescentam dados de exemplo ao `index.html` entregue. O relatório automatizado identifica o hash da página real que originou cada fixture, com a alteração de porta e a instrumentação de teste descritas.

## Resultado executado

Página verificada: SHA-256 `551e4bcbd12afc83390f989d49a8f4739afe1f45b872ac8c80a037719c01e0ed`.

| Verificação | Resultado |
|---|---|
| Fuso: testes Node | 16/16 |
| Fuso: Chromium com HTTP+WebSocket falso | 102/102 |
| Fuso: mutações reais de rótulo, soma manual de três horas e fuso errado | 3/3 detectadas |
| Regressão A–C: conexão, reconexão, luzes, origem e estilos | 91/91 |
| Regressão A–C: mutações | 35/35 detectadas |
| Regressão A–C: navegador, queda/retorno HTTP+WebSocket, F5 e token novo | 27/27 |
| Auditorias de token da regressão | 4/4 sem vazamento |
| Erros JavaScript nas duas verificações de navegador | 0 |

Os 16 testes de fuso e os 91 de regressão incluem os respectivos casos de mutação; as linhas de mutação detalham esses casos, não constituem testes adicionais para somar ao total.

Para reproduzir a verificação do arquivo único e a suíte de fuso na pasta da entrega:

```text
python3 extrair_modulos.py --conferir
node tests/run-timezone.mjs
```

Para a regressão anterior, entre em `tests/regression` e execute:

```text
node tests/run-all.mjs
python3 browser/build-browser-harness.py
node browser/browser-regression.mjs
```

O verificador usa Node, Python e Playwright/Chromium já existentes no ambiente; não instala dependências. Caminhos alternativos e portas estão documentados em `tests/VERIFICAR-FUSO.txt` e `tests/regression/README.txt`. A pasta `base` é apenas a referência imutável para os testes; o terminal entregue é o `index.html` na raiz. Os arquivos de QA e de mutação são deliberadamente separados do terminal e nunca integram seu importmap.

Evidências detalhadas: `tests/timezone-results.json`, `tests/browser-results.json`, `tests/mutation-results.json`, `tests/regression/tests/run-all-results.json`, `tests/regression/browser/browser-results.json` e `tests/regression/browser/server-results.json`. As capturas de tela das configurações e da tela principal estão em `tests/timezone-browser.png` e `tests/timezone-terminal.png`.
