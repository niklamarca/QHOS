# QHOS Terminal — entrega única: conexão, luzes, botões e fuso

Esta entrega reúne a conexão automática da tarefa 8 da etapa 0, seção 4.1 do plano QHOS V2, com os itens A–D de usabilidade e observação do terminal, estendendo a tarefa 9; o item F não foi incluído.

## Base e identidade

Base efetiva: **`a33e3ba78328664a`**, contendo a conexão automática e a faixa de luzes. O item D foi aplicado sobre ela, gerando o resultado intermediário `551e4bcbd12afc83`; esta entrega acrescenta a exigência de URL literal e o retorno ao fluxo manual quando a configuração é inválida. **Não foi reconstruído sobre `8e98e873`.** Essa versão antiga aparece somente como fixture histórica dos testes da conexão manual.

Há um único terminal final: `index.html`, com os mesmos 16 módulos embutidos em base64. `modulos/` contém a cópia legível exata desses módulos. `base/` e os HTMLs de QA são referências/testes separados; não são alternativas de terminal para instalar.

O resultado final tem seis módulos alterados e dez idênticos à base a33e. Em relação ao intermediário 551e, apenas `app.js` mudou. `qhos-bridge.js` e `contract-ui.js` continuam idênticos à base a33e; validação de endereço, protocolo, welcome, pipeline e fluxo manual foram preservados.

## Situação de cada item

| Item | Situação e comportamento entregue |
|---|---|
| Auto-conectar 1 | **Feito.** No load/F5, somente em `http://127.0.0.1:8766`, com última fonte Rithmic ou ausente: busca relativa de `/conexao` e uso de `contractManager.connect({url,token})`. Outras origens e portas permanecem manuais. |
| Auto-conectar 2 | **Feito.** Preferência Coinbase impede `/conexao`, conexão automática e troca de fonte. |
| Auto-conectar 3 | **Feito.** Uma tentativa por vez; repetição em 10 s quando a ponte não estiver tentando. A espera interna da ponte é respeitada. Ação manual de desconectar ou escolha de Coinbase interrompe o automático. O laço pausa após cinco minutos com a aba oculta e retoma quando ela volta. Configuração inválida encerra o automático nessa abertura e deixa o manual disponível. |
| Auto-conectar 4 | **Feito.** Durante tentativa, aparece “Conectando ao coletor”. HTTP 404/indisponibilidade mostram mensagem neutra e preservam a tela. URL/token/JSON inválidos mostram orientação para a conexão manual, sem alerta vermelho e sem cair automaticamente em dados Coinbase. |
| Auto-conectar 5 | **Feito.** Token automático não preenche campo, DOM, URL, título, cookie, storage ou logs. Não entra no snapshot do controlador. Variáveis transitórias são limpas; erros automáticos não reproduzem detalhes recebidos. A credencial persistente da conexão fica somente na memória da ponte. |
| A — verde | **Feito.** Ponte conectada e assinatura confirmada em `waiting`, `live` ou `stale`; a idade dos dados pertence às outras luzes. |
| A — amarelo ao conectar | **Feito.** Tentativa inicial em andamento, incluindo o laço automático. |
| A — amarelo ao reconectar | **Feito.** Reconexão e intervalo de espera, sem apagar a indicação entre tentativas; número de reconexões visível. |
| A — vermelho | **Feito.** Erro da ponte ou desconexão após tentativa sem nova tentativa agendada e sem laço ativo. |
| A — cinza | **Feito.** Nunca conectou ou desconexão manual. Resposta automática inválida antes de qualquer conexão permanece nesse caso. |
| B — faixa sempre visível | **Feito.** Na tela principal, faixa com Terminal↔Coletor e as seis camadas, na ordem recebida, com texto, foco, `aria-label`, tooltip e `role=status`. Falta de conexão ou percurso com mais de 5 s torna as seis camadas cinza. Ausência de informação nunca vira verde. |
| C — aparência desativada | **Feito, já presente na base a33e e preservado.** `buyButton`, `sellButton` e `newOrderButton` desativados têm opacidade `.42`, cores neutras, ausência de destaque verde/vermelho e cursor `not-allowed`. Regras e dicas atuais permanecem. |
| D — fuso | **Feito.** Brasília padrão, UTC ou Nova York nas configurações. Horários da faixa/tooltips, “desde” e “Atualizado” seguem o fuso selecionado, assim como as demais exibições. |
| F — buracos no gráfico | **Não incluído.** Nenhuma mudança de preenchimento, interpolação ou cálculo de candles/buracos. |

## Validação estrita de `/conexao`

Para conectar automaticamente, a resposta deve ser HTTP 200, JSON objeto com `url` exatamente **`ws://127.0.0.1:8766`** e `token` string que corresponda a **`^[A-Za-z0-9_-]{43}$`**. A igualdade é literal: barra final, espaços, maiúsculas, outro host, porta, caminho, credenciais, query ou fragmento são rejeitados. A normalização de URL continua pertencendo ao fluxo manual, sem alterar `validateBridgeURL`.

A chamada conserva `cache:'no-store'`, `credentials:'omit'`, `referrerPolicy:'no-referrer'` e recusa redirecionamento. O navegador envia os cabeçalhos Fetch Metadata; o terminal não os fabrica. Configuração inválida não agenda repetição e não inicia busca de candles Coinbase pelo timer de 60 s. A ação explícita de conectar manualmente ou selecionar Coinbase libera o fluxo correspondente. Uma nova abertura/F5 pode consultar a configuração novamente.

`manualOnly` é estado privado do controlador automático no navegador, exposto à apresentação local; não é campo do protocolo nem alteração do status da ponte. O campo `retrying` da ponte já fazia parte da base a33e.

HTTP 404 e falha de rede continuam no fluxo de indisponibilidade transitória, com mensagem neutra e repetição a cada 10 s, conforme o pedido original. Um erro de autenticação WebSocket permite nova descoberta quando a ponte tiver parado de tentar.

**Limite conhecido:** `Sec-Fetch-Site` informa a relação entre a origem iniciadora e a origem de destino; `Sec-Fetch-Mode` e `Sec-Fetch-Dest` informam o contexto da requisição. Eles **não autenticam a identidade do programa que ocupa a porta 8766**. Outro processo que sirva o mesmo HTML e endpoint nessa origem pode satisfazer os mesmos critérios. Porta fixa e URL literal delimitam a origem autorizada; não são prova de identidade do coletor. Fonte primária: [W3C Fetch Metadata, seções 2.1–2.3](https://www.w3.org/TR/fetch-metadata/#sec-fetch-site-header), consultada em 25/10/2026.

## Onde fuso e conexão se sobrepõem

**A sobreposição direta ocorre em `app.js`.** O controlador de conexão e os cálculos de estado das luzes continuam usando status, `Date.now()` e timestamps originais em UTC. `collectorLightPresentation` passa apenas a apresentação de `since` pelo formatador compartilhado; `renderStatus` e `applyDisplayTimeZone` atualizam o texto visual. A preferência de fuso não participa de timeouts, idade do pipeline, número de reconexões, seleção de fonte ou validação de credenciais.

`market.js` fornece `displayTime` para essa apresentação. `microstructure-ui.js` formata “desde” e “Atualizado” do Percurso pelo mesmo serviço; não contém o controlador automático. `qhos-bridge.js` e `contract-ui.js`, responsáveis pela ponte e pelo fluxo manual, não receberam alterações de fuso.

## Fuso sem alterar os dados

Configurações → Horário do terminal → Fuso de exibição aplica a escolha imediatamente e persiste apenas uma string em `qhos-v2-preferences.timeZone`. `Intl.DateTimeFormat` usa `America/Sao_Paulo`, `UTC` ou `America/New_York`; não há soma/subtração manual de horas.

| Instante interno | Brasília | UTC | Nova York |
|---|---|---|---|
| `2026-10-25T02:30:00.123Z` | 24/10/2026 23:30:00.123 BRT | 25/10/2026 02:30:00.123 UTC | 24/10/2026 22:30:00.123 EDT |

Eixo/crosshair, Time & Sales, captura, rodapés, sessões, execuções, pesquisa e tooltips usam o mesmo formato. Datas aparecem em dia/mês. A lista de alertas da base não tinha horário de disparo; nenhum timestamp foi inventado. Siglas de horário de verão vêm das regras de `Intl` para cada instante.

Trocar o fuso não chama `setData`, não busca dados e não reagrega candles. Timestamps, buckets, janelas, replay, gravações, JSON exportado, `ES_LIBERADO_EM` e cortes dos estudos ficam intactos. O PNG exportado também usa UTC, com restauração garantida do fuso da tela, inclusive se a exportação falhar. As regras do simulador e da trava ES permanecem iguais.

## Mudanças por arquivo e função

| Arquivo | Mudanças |
|---|---|
| `app.js` | `createCollectorAutoConnection`: comparação literal, rejeição neutra por `rejectConfiguration`, estado interno `manualOnly`, limpeza de credencial e liberação apenas por ação explícita. `loadChart`: impede busca automática após configuração rejeitada. `statusLabel`/`renderStatus`: orientação manual neutra. |
| `app.js` | Fuso: inicialização e `persistPrefs`; novos `displayStamp`, `timeMarkup`, `rangeMarkup`, `refreshDisplayTimes`, `expiryText`, `applyDisplayTimeZone`; `openSettings` acrescenta seletor. |
| `app.js` | Horários: `collectorLightPresentation`, `renderDetails`, `scopeNote`, `renderFlow`, `renderAnalysis`, `renderHealth`, `renderStatus`, `renderStorageStatus`, `renderSessionList`, `paintReplay`, `openChartContext` e inspeção do footprint. `sessionName` continua gravando UTC; `openSessions` converte só o texto exibido. |
| `market.js` | `displayInstant`, `displayFormatter`, `displayParts`, `displayFacade` e `displayTime`: formatação comum e três fusos permitidos; `forZone` separa exportação UTC; `sessionName` apresenta nomes automáticos sem alterar o registro original. |
| `chart.js` | `timeAxisLabel`, `drawTimeAxis`, `renderOverlay`: apenas rótulos/crosshair; `refreshTimeZone`: repintura; `exportImage`: UTC temporário e restauração em `finally`. |
| `flow-visuals.js` | `clock`, `dateClock`, `drawCVD`, `drawHeatmap`, `drawTimeAxis`, `showInspection` e `refreshTimeZone`: texto, eixo, acessibilidade e tooltips, inclusive fixados; dados existentes preservados. |
| `execution-ui.js` | `hover`, `renderInspector`, `mountTable`, `openList`, `renderHistorySummary` e `refreshTimeZone`: formatação, preservando histórico capturado, instante, seleção, filtros e paginação. |
| `microstructure-ui.js` | `dateTime`, `nsTime`, `expiryText`, cabeçalhos e renders de Percurso/cotações/integridade/MBO/fila/sessões/estudos/experimentos. `refreshTimeZone` reutiliza resultados; não executa estudos. |
| `index.html` | Incorpora os módulos e altera somente o rótulo inicial do relógio fora do importmap. Faixa de luzes e CSS dos botões já existentes permanecem. |
| Demais dez módulos | Idênticos byte a byte à base a33e. |

## Erro encontrado, consequência e correção

1. **Comparação de URL normalizada.** A base a33e aceitava variantes não literais do endereço. A prova diferencial reproduz isso e confirma a correção: agora apenas a string exata é aceita.
2. **Repetição após configuração inválida.** A versão anterior continuava a repetir; desligar apenas o laço poderia liberar o timer de candles Coinbase. Agora a rejeição encerra o automático e `manualOnly` bloqueia esse carregamento até uma ação explícita.
3. **Horários fixos em UTC e nomes automáticos de sessões.** A apresentação não seguia preferência comum; uma conversão ingênua alteraria arquivos ou poderia inferir ano errado. O formatador comum converte somente a apresentação, reconhece nomes por ativo e resolve a virada de ano com os instantes já registrados.
4. **PNG copiava o canvas no fuso da tela.** O exportador usa UTC temporariamente e restaura a apresentação no final.

## Imagens e evidências

As PNGs em `imagens/` mostram a faixa real do terminal, capturada contra HTTP+WebSocket falsos: `01-verde.png`, `02-amarelo-conectando.png`, `03-amarelo-reconectando.png`, `04-vermelho.png`, `05-cinza.png`. A sexta imagem mostra os botões desativados no ES. Os estados resultam de welcome atrasado, assinatura confirmada, queda do servidor, erro de autenticação e clique no botão Desconectar; nenhum estado ou cor foi injetado no código do terminal.

O tooltip verde mostra o “desde” em BRT; os testes de fuso também verificam UTC e Nova York. As imagens têm legenda de verificação isolada para não serem confundidas com operação real. Fixtures não entram no `index.html` final.

## Como verificar

Na pasta da entrega:

```text
python3 extrair_modulos.py --conferir
node tests/run-timezone.mjs
```

Na pasta `tests/regression`:

```text
node tests/run-all.mjs
python3 browser/build-browser-harness.py
node browser/browser-regression.mjs
```

O teste diferencial e a reprodução das PNGs têm instruções nos arquivos de testes correspondentes. Playwright/Chromium, Node e Python usados já existiam no ambiente; nenhuma dependência foi adicionada ao terminal. Os servidores falsos usam apenas portas isoladas 18025–18030 e são encerrados ao final. O coletor real não foi acessado ou alterado.

Manual: abra Configurações, alterne Brasília/UTC/Nova York e confira a tabela de instante fixo acima; confirme que o gráfico não recarrega. No coletor de teste, confira load/F5, queda/retorno, token novo, resposta inválida, escolha Coinbase e desconexão manual. O texto, as cores e o número de reconexões devem corresponder à matriz desta entrega.

Os resultados executados, hashes da página verificada e hashes dos 16 módulos seguem abaixo e em `SHA256.txt`.

## Resultados executados

`index.html`: SHA-256 **`2c03bcdbcf426acd`** (completo `2c03bcdbcf426acd4cb745e178c9f50f578477450346165c52ae234e3da88010`).

| Verificação | Resultado |
|---|---|
| Auto-conexão, A–C e contrato estrito | 126/126 testes |
| Mutações de conexão/origem/luzes/estilos/rejeição e bloqueio de fallback | 39/39 detectadas |
| Fuso D | 16/16 testes |
| Mutações de fuso: rótulo, soma manual de horas, zona errada | 3/3 detectadas |
| Navegador: conexão, F5, quedas, token novo, payload inválido por 61 s e manual | 33/33 |
| Navegador: fusos e preservação de dados | 102/102 |
| Auditorias de token da conexão | 5/5 limpas |
| Prova diferencial: base a33e e candidato | 5/5 |
| PNGs dos cinco estados e botões do ES | Capturadas e verificadas |
| Erros JavaScript dos navegadores | 0 |
| Conferência do arquivo único e módulos legíveis | 16/16 idênticos ao importmap |

Os casos de mutação já integram as contagens das suítes unitárias. Os 5 casos diferenciais são uma execução separada. Nenhum PASS depende de mercado ou coletor real.

Relatórios: `tests/timezone-results.json`, `tests/regression/tests/run-all-results.json`, `tests/regression/browser/browser-results.json`, `tests/regression/browser/server-results.json`, `tests/discovery-differential-results.json` e `tests/light-images/light-images-results.json`.

## SHA-256 dos 16 módulos

Comparação byte a byte com a base **a33e3ba78328664a**; primeiros 16 caracteres.

| Módulo | SHA-256 (16) | Comparação |
|---|---|---|
| `app.js` | `f4fc3b3bbae20fc9` | Alterado |
| `chart.js` | `57af564ba549582e` | Alterado |
| `contract-ui.js` | `3c9d6a607423f28a` | Idêntico |
| `execution-ui.js` | `5d3c68a6c9c76f38` | Alterado |
| `executions.js` | `a225ed43fd250b06` | Idêntico |
| `feed.js` | `8645abfabeb70c60` | Idêntico |
| `flow-visuals.js` | `d5960d78ba64b969` | Alterado |
| `flow.js` | `ad32b7bd22c29002` | Idêntico |
| `icons.js` | `95dd07d6095a4b2f` | Idêntico |
| `market.js` | `f6eae9688b988c09` | Alterado |
| `microstructure-engine.js` | `4c5edc2e085bca90` | Idêntico |
| `microstructure-ui.js` | `60fe0469f12b1326` | Alterado |
| `navigation.js` | `a5f0944701488e02` | Idêntico |
| `qhos-bridge.js` | `a09168a66c6e425f` | Idêntico |
| `risk.js` | `efe939b4ab82ae7e` | Idêntico |
| `sessions.js` | `8ca001d620e9b658` | Idêntico |
