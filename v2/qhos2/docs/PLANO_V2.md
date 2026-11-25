# Plano da V2 (fonte única)

Atualizado em 2026-10-26. Toda mudança de plano entra aqui, com data. Instruções posteriores do usuário
prevalecem; divergências documentais devem ser corrigidas, sem presumir autorização operacional.

## Emenda de 26/10: independência com Brain e terminal preservados

O usuário autorizou revisar a herança, incorporar capacidades verificadas, corrigir defeitos, testar e
publicar na branch `v2`. Confirmou explicitamente que o Brain e o terminal continuam em uso e ambos
precisam pertencer à V2. A independência de instalação passa a ser trabalhada agora, sem esperar 20/11.
O [README atual](../README.md) descreve o pacote, as dependências próprias e os limites de validação.

O núcleo, o Brain e sua interface passam a ser distribuídos juntos. A raiz padrão é `~/qhos-v2-data`,
e o ambiente sugerido é `~/qhos-v2/.venv`. A instalação inclui dependências web do Brain; a antiga
expressão "só websockets e pytest" não descreve mais o conjunto completo. Os `.proto` permanecem
referências, sem leitura obrigatória em runtime. O Brain observa o contrato `qhos2.observation.v1`.
Não há exigência de reinstalar os antigos servidores Micro/NAV como condição de independência.

As correções incluem recusa de retrato inválido, identidade por quadro, falhas de sessão/relógio,
quarentena e exclusividade locais, persistência, instalação do terminal, origem dos dados e registro
de pesquisa. São mudanças autorizadas nesta data, inclusive nas partes de captura da regra 21.
Os marcos históricos abaixo permanecem como registro; testes offline não certificam uma semana de
operação, disponibilidade da corretora, qualidade do relógio UTC ou resultados financeiros.

## 1. Objetivo

Uma máquina que recebe, grava, reproduz e analisa eventos reais de mercado da Rithmic (sistema Paper) de forma
**verificável**, e só depois decide e simula. Nenhuma ordem real até os gates da seção 6. O V1 continua rodando
(experimentos forward em curso) e não é importado pela V2.

Pergunta central (25/10): responder, até 30/04/2027, com no máximo 3 testes pré-registrados e amostra
suficiente, se existe edge líquido de custos acessível a 1 contrato de ES com o dado chegando a ~75 ms em São
Paulo. "Não existe" é uma resposta válida.

## 2. Fluxograma (versão integrada, aprovada em 23/10)

Números entre colchetes são peças da seção 4. Este é o mapa-alvo: cada caixa só existe quando a peça dela está feita.

~~~
MERCADO (CME / COMEX via Rithmic Paper)
   │
   ▼
SESSÃO R|Protocol [4b] ── livro por ordem 160/116 [4c] + negócios 150 [4e]
   │
   ▼
BRUTO GRAVADO (bytes + received_ns, SHA-256) [4c → 5 contínuo, com saúde]
   │
   ▼
EVENT STORE verificado (catálogo) [3, 5] ───────────────────────────► C2
   │
   ▼
NORMALIZAÇÃO → eventos NEW / CHANGE / DELETE / TRADE [6a]  ◄── gerador sintético (gabarito) [6a]
   │
   ▼
FILA LIMITADA (event bus em memória) [6b]
   │
   ▼
LIVRO POR ORDEM [6b]  ═══ teste: replay produz o mesmo livro que o ao vivo
   │
   ▼
FEATURES [7]: desequilíbrio · liquidez · deslocamento · OFI · custo de cruzar ──► C3
   │
   ▼
SCORE DA HIPÓTESE pré-registrada [8]   ◄── C4 regime do V1 (filtro de contexto, nunca sinal direto)
   │
   ▼
SIMULADOR (custos e atraso medidos) [9] ─► SHADOW (decisão gravada, sem ordem) [10]
   │  [portão de promoção]
   ▼
RISCO (caminho próprio, kill switch, dado velho; única porta para ordens na Rithmic) [11]
   │        ▲ SAÚDE [5]   ▲ POSIÇÃO / P&L
   ▼
EXECUÇÃO paper (order plant da Rithmic) [12] ─► ORDENS ─► FILLS ─► POSIÇÃO / P&L ─► EVENT STORE

PESQUISA / BACKTEST lê só o EVENT STORE (nunca o caminho ao vivo)
   → NOVO MODELO → [pré-registro + fora da amostra + aprovação humana] → SHADOW

PUBLICAÇÃO (todos só leem): V2 ─► C1 estado_atual.json ─► visor da V2, painéis do V1, /api/status ─► Brain
~~~

Correções em relação ao desenho original: (1) o modelo novo passa por um **portão de promoção** e entra no sinal,
nunca volta ao dado de mercado; (2) o risco é alimentado por posição e P&L e tem **caminho próprio**, fora de
qualquer fila; (3) o **bruto é gravado antes** de normalizar, há **monitor de saúde** e há **shadow** antes da bolsa;
(4) o POV saiu; (5) os negócios (150) entraram, sem eles não há deslocamento nem fluxo agressor.

### 2.1 Integração com o V1 (por contratos de arquivo, sem importar código)

| Contrato | Escreve | Lê | Conteúdo |
|---|---|---|---|
| C1 `estado_atual.json` | V2 | visor da V2, painéis do V1, `/api/status` (Brain) | sessão, idade do dado, quadros, heartbeat, relógio, anomalias, `research_only`, `can_execute` |
| C2 event store + catálogo | V2 | pesquisa (V1 e V2) | bytes crus verificados por SHA-256 |
| C3 dados derivados | V2 (peça 7) | `DATA_INGEST` do V1 | barras, spread, profundidade e OFI agregados, com manifesto e linhagem |
| C4 `regime_atual.json` | V1 (`REGIME_PREDICT`) | V2, como filtro | regime atual e data do cálculo |

Regras: um escritor por contrato; gravação atômica; versão no nome do esquema; origem verdadeira ("Rithmic Paper
Trading", nunca "Rithmic Test" para passar pelas travas do V1); C3 só alimenta hipóteses pré-registradas, nunca o GA;
o V1 nunca envia ordem à Rithmic.

| Fase | O quê | Mexe no V1? |
|---|---|---|
| I | V2 escreve C1, C2 e C3 e tem visor próprio | Não |
| II | Ponte no V1: painéis e `/api/status` leem o C1; a captura "Rithmic Test" do V1 é aposentada | Sim (pacote pequeno, com tag, testes e volta atrás) |
| III | `DATA_INGEST` do V1 lê o C3; o V1 publica o C4 | Sim |
| IV | O risco da V2 é a única porta para a Rithmic | Não |

## 3. Níveis de "vivo"

| Nível | Significa | Peças |
|---|---|---|
| 1. Respirando | Serviço que sobrevive a queda e reinício, grava tudo verificado, mostra saúde | 4, 5 |
| 2. Entendendo | Reconstrói o livro em tempo real, igual ao replay | 6, 7 |
| 3. Decidindo | Gera sinais sem ordem (shadow) | 8, 9, 10 |
| 4. Executando em papel | Ordens simuladas na Rithmic, com risco e reconciliação | 11, 12 |
| 5. Capital real | Fora de escopo | |

## 4. As 10 etapas (aprovadas em 25/10)

Substituem a ordem de peças de 23/10. Cada etapa termina com um resultado medido, nunca com um documento, e tem
critério de saída e de abandono. Uma etapa por vez; a exceção é o que não altera o dado gravado, que pode andar em
paralelo e fica registrado aqui. Peças feitas até 25/10: 1 a 3b, 4a a 4e (falta o teste da 4e com mercado aberto),
6a, 6b e a parte da 7 que a hipótese usa.

| Etapa | O quê | Saída (medida) | Abandono | Peças | Estado |
|---|---|---|---|---|---|
| 1 Fonte e conta | Só a V2 entra no Paper; senha só digitada; fatos da Rithmic registrados; taxa e licença perguntadas | 5 pregões seguidos (28/10 a 3/11, pelo agendador diário ligado em 27/10) capturados e verificados, sem login recusado e sem sessão do V1 no Paper | Licença não permite uso automatizado para pessoa física, ou conta bloqueada e não reaberta | 1 a 4e | **Feita em 3/11**: 5 de 5 (28, 29, 30/10, 2/11 e 3/11), todos verificados pelo catálogo, sem login recusado; 30/10 interrompido por queda do cabo de rede (~5 s) e contado por decisão do autor. Medições na seção 7 |
| 2 Livro certo | Atualizações recebidas durante o retrato guardadas e aplicadas depois do fim (regra da Rithmic); leitura das capturas refeita; ordens fantasmas contadas | Zero anomalias depois do retrato em todas as capturas reais; nenhum quadro qualificado cruzado; teste sintético da corrida | Não se aplica (defeito a corrigir) | 6a, 6b | **Feita em 28/10** (0.2.5, `57388f3`): regra do retrato provada nas 11 capturas reais; 38 testes; contagem de fantasmas que a regra antiga teria deixado = **1** (`c4ff575e`, 23/10), confirmada pela inspeção independente; zero anomalias depois do retrato em 10 de 11; a de 28/10 é válida até 15:43:03 BRT e inválida depois por lacuna de 62 min não entregue pela origem (etapa 5). Tabela e provas em `docs/provas/etapa2_20261028/`. (O registro de 25/10, "zero fantasmas nas 3 capturas", olhava só anomalias e não podia ver o caso.) |
| 3 Pergunta respondível | Custo contra movimento em 10 s; menor efeito detectável; avaliador testado em dado sintético; P5/P95 congelados | Plano com tudo congelado antes de 9/11 | Acerto necessário acima de 65%, ou menor efeito detectável maior que o custo: encerra a microestrutura (etapa 10) | 7, 8 | Em curso desde 28/10 (seção 4.2): passos 0 a 10 feitos até 29/10 (auditoria da base do avaliador, checagens de custo e de poder, teste sintético, vigia, código congelado com `identidade_codigo` `43a1de51…7dcea`); falta o registro em 7 ou 8/11 |
| 4 Teste | Captura de 9 a 20/11 sem olhar; avaliação uma única vez | Veredito registrado | Falhou: volta à etapa 3 com a H2 (no máximo 3 testes até 30/04/2027) | 8 | |
| 5 Captura que se sustenta | Serviço, quarentena de login, senha só na memória, calendário da CME, manutenção da Rithmic, piso de disco, gerações por conexão, retrato periódico, endpoint reserva; desde 28/10: teto local de quadros/s medido a cada versão, detecção de atraso crescente e de silêncio de mercado com a sessão viva, ressincronização com retrato novo em vez de seguir com livro inválido | 10 pregões seguidos sem intervenção, zero lacunas não explicadas, diferença zero entre livro e retrato periódico | | 5 | Pode correr em paralelo à 4 (não muda o formato gravado); data de entrada registrada. Mínimo construído na etapa 3, passo 9 (29/10): `qhos2.vigia` detecta silêncio e atraso crescente com a sessão viva e relê o livro no fechamento; a ressincronização com retrato novo continua desta etapa |
| 6 O servidor enxerga a V2 | C1 no formato `qhos.rithmic.operational`; fase II no V1 (trava "só Rithmic Test" liberada, captura do Test aposentada) | Brain, Micro e NAV mostram o estado real da V2; ao vivo igual ao replay | | C1, V-1 | |
| 7 Sombra | Só se uma hipótese passar: orquestrador ao vivo, decisões gravadas sem ordem | 20 pregões com média dentro do intervalo do teste | Média da sombra abaixo do custo | 10 | |
| 8 Risco e ordens no Paper | Motor de risco antes de qualquer ordem; sessão de ordens; latência medida; avaliador rodado de novo com a latência real | 100% das ordens reconciliadas; hipótese ainda passa com a latência medida | Com a latência real o edge some | 9, 11, 12 | |
| 9 Dinheiro real mínimo | Corretora, taxa documentada, licença, 1 contrato, limite de perda escrito; nunca em semana de troca de contrato ou de FOMC | Resultado real dentro do intervalo da sombra | Limite de perda atingido ou real abaixo da sombra: parar no mesmo dia | | |
| 10 Laboratório e IA de apoio | PAIR só lê o C1 e o plano; governança como regra fixa; sem hipótese aprovada até 30/04/2027, a V2 fica como laboratório sem custo mensal | | | | |

O visor entra na etapa 6 só como leitura do C1 (V-1); heatmap, footprint e camadas próprias (V-2 a V-5) só depois
de uma hipótese aprovada (regra 18). Exceção: o terminal QHOS, que já existe, pela emenda de 25/10 à regra 18
(seção 4.1).

**Marco de independência (logo depois de 20/11, antes de a etapa 5 entrar no ar):** a V2 funciona com o V1
desligado. Critérios: pasta própria com o código vindo do GitHub; Python próprio só com `websockets` (versão
travada) e `pytest`; capturas numa pasta de dados própria, copiadas com SHA-256 conferido; `.gitignore` próprio
conferido pelo teste de higiene; cópia dos `.proto` de referência; e o teste do V1 ausente (a pasta do V1
renomeada, sem apagar, por uma semana, com a suíte, a captura, o livro e a calibração rodando sem ela). A V2 já
não importa código do V1; o que falta é infraestrutura.

### 4.1 Etapa 0: integração do terminal QHOS (25/10, em paralelo às etapas 1 a 4)

Nome dado pelo usuário em 25/10: "etapa 0" é só o terminal. Não vem antes das etapas 1 a 4 nem tira o lugar de
tarefa com data delas.

O terminal QHOS (`qhos2/terminal`, a tela do navegador) passa a mostrar os dados que a V2 grava, pelo caminho
captura → arquivo gravado → leitura ao vivo → coletor → terminal. Decisões do usuário em 25/10:

- **Em paralelo:** a integração nunca tira o lugar de uma tarefa com data das etapas 1 a 4 (capturas de 26 a
  30/10, checagens de 7 e 8/11, P5/P95 congelados antes de 9/11, teste de 9 a 20/11). Não roda em cima de uma
  captura, a não ser a tarefa 7, que só lê o arquivo, em outro processo.
- **Só lê o que a captura grava:** nenhuma tarefa toca no código da captura (regra 21) nem abre sessão na Rithmic
  (regra 16).
- **Endereço:** `ws://` só em `127.0.0.1`, com porta explícita; qualquer outro endereço exige `wss://`. O coletor
  confere Host, Origin e token; o token fica em `~/.config/qhos2/coletor.token` (0600, fora do repositório), nunca é
  impresso e não é a senha da Rithmic.
- **Regra 18, emenda estreita:** ver a regra 18. Painéis desligados para o ES até 21/11: estudo de eventos,
  carteira experimental (estratégias e curva de capital), MBO e consulta de fila, e o simulador de ordens. Os
  painéis que só descrevem o agora (gráfico, livro, footprint, CVD, heatmap, perfil, cotações, liquidez,
  integridade, percurso e estado microestrutural) ficam ligados.
- **De 9 a 20/11:** ao vivo liberado; o histórico desses dias fica escondido no terminal até 21/11.

| # | Tarefa | Estado |
|---|---|---|
| 1 | Terminal no GitHub, com checagem de privacidade | Feita (`5fee266`) |
| 2 | `ws://` só em `127.0.0.1` | Feita (`1217bd4`) |
| 3 | Leitura ao vivo que segue a gravação sem tocar na sessão (`leitura.py`) | Feita (`e38b4bf`); livro igual ao do replay em 2 capturas reais |
| 4 | Coletor `qhos.dashboard.v1` no 127.0.0.1 (`coletor.py`): hello, heartbeat, token, Origin, catálogo só com o ESZ6, troca de contrato recusada, sem histórico | Feita (`0ebd9b5`); ainda sem dados |
| 5 | Dados da V2 no terminal: livro (`ready` falso se não qualificado), negócio com lado agressor, cotação, lacuna, metadados do ESZ6; painéis da regra 18 desligados para o ES | Feita em 25/10: 5a (`6630ac1`) livro igual ao replay e todos os negócios na ponte do terminal, em captura sintética; 5b estudo de eventos e MBO recusados para o ES até 21/11, provado na tela. Falta o dado real (tarefa 7) |
| 6 | Prova: a assinatura do livro na tela igual à do replay | Livro feito em 25/10 (`2b8d5cf`, `coletor --conferir`): nas 3 capturas reais do ESZ6 (14043, 11630 e 1267 quadros), 40 níveis por lado e livro inteiro iguais ao replay, nenhuma mensagem fora do formato. Negócios: feitos em 25/10 com a captura `798f765e` (15 min na sessão noturna, 3151 negócios fora o negócio-retrato): depois da correção do quadro 150 só de volume (`04a950d`), o `--conferir` deu IGUAL, com 3151 negócios enviados e 3151 gravados, IDs únicos, 40 níveis por lado iguais ao replay, livro qualificado e nada fora do formato. Repetido na captura `8aa43f0a` (15 min, noturna): IGUAL, 2781 negócios. Falta repetir no pregão regular de 26/10 |
| 7 | Teste ao vivo durante uma captura | Feita em 25/10 na captura `8aa43f0a` (15 min, sessão noturna, Paper): o coletor `--em-andamento` seguiu a captura e o terminal, no navegador do Windows, mostrou "Ao vivo", o ES com preço, livro de 10 níveis por lado, negócios com lado agressor (131 na primeira leitura), perfil de volume, candles do histórico mais o candle vivo e o Percurso com as 6 camadas em OK; no fim a conferência deu IGUAL (2781 negócios). Incidente: o coletor da primeira captura ficou rodando com o código anterior à correção do quadro 150 e travou em `negocio_sem_preco:1301`; o Percurso apontou a causa. Regra de operação: encerrar o coletor antigo antes de subir outro, e reiniciá-lo depois de qualquer atualização do código. Falta comparar o preço com uma fonte externa e repetir no pregão regular de 26/10 |
| 8 | A V2 serve a página; o terminal deixa de depender do V1 | Feita em 25/10: o coletor serve `terminal/index.html` em `http://127.0.0.1:8766/` (Host conferido, sem cache, sem moldura); só a própria página conecta. No navegador, com carimbo atual: "ao vivo", livro, footprint e negócios na tela; botões de ordem desativados para o ES |
| 9 | Percurso Rithmic, uma bolinha por camada (lado do terminal: pedido ao construtor; lado do coletor) | Lado do coletor feito em 25/10: mensagem `pipeline` no welcome (capacidade `pipeline`) e a cada 1 s, com as 6 camadas (rithmic, sessao, gravacao, livro, leitura, coletor), só com o que o arquivo gravado mostra; nunca verde por falta de informação; o terminal atual a ignora sem erro. Lado do terminal feito em 25/10, sem o construtor: a ponte só aceita o `pipeline` com a capacidade anunciada, da mesma sessão, com camadas sem repetição e estados da lista (ok, atenção, falha, desconhecido), e o descarta ao desconectar ou ao receber um inválido; a tela "Percurso dos dados" mostra uma bolinha por camada; percurso sem atualização há mais de 5 s fica cinza |
| 10 | Candles de histórico a partir das capturas da V2; 9 a 20/11 escondidos até 21/11 | Feita em 25/10 (`historico.py`): candles dos negócios gravados (sem o negócio-retrato), das capturas encerradas do ESZ6 mais a captura seguida; cache de barras de 1 minuto em `<data_root>/derivados/candles_1m/` (fora do repositório), conferido pelo SHA-256 da captura; negócios de 9/11 a 21/11 00h de Nova York fora do histórico até 21/11 00h de Brasília (cortes conferidos por teste com os fusos). Os 6 períodos passam pela validação da ponte no navegador |
| 11 | Exportador do MBO da V2 para `qhos-mbo-research-v1` (janelas de minutos: 10 MB, 20.000 ordens, 50.000 eventos) | Adiada em 25/10 para depois de 21/11: o painel de MBO fica desligado para o ES até lá (regra 18), então o exportador não serviria a nada antes disso |
| 12 | Conexão automática do terminal ao coletor (sem colar o token a cada F5) | Pedida e autorizada pelo usuário em 25/10. Lado do coletor feito em 25/10: `GET /conexao` entrega endereço e token só ao `fetch` da própria página; provado no Chromium com um site de fora tentando (fetch, sem CORS, WebSocket, script, imagem, iframe, barra de endereços, outro nome do mesmo endereço): nada vazou. Lado do terminal (conectar sozinho ao abrir, reconectar depois de queda, luzes de conexão) com o construtor |

Adiados para a etapa 8: NAV real e ordens no Paper. Até lá o simulador do navegador fica desligado para o ES.
Cada tarefa feita atualiza esta tabela no mesmo commit.

### 4.2 Etapa 3: a pergunta tem que poder ser respondida (aberta em 28/10)

**Razão, escrita pelo autor em 28/10:**

> O V1 passou um ano procurando um "edge" sem nunca ter escrito a pergunta que queria responder. O GA testava
> milhares de combinações, o critério mudava depois de ver o resultado, e o preço usado no teste era o do dado, não o
> que a ordem pagaria de verdade. Assim qualquer coisa parece funcionar e nada é falsificável. A etapa 3 existe para
> que, antes de olhar um único dado da janela de teste, a pergunta esteja escrita de um jeito que admita a resposta
> "não".
>
> Para isso, quatro coisas têm de estar congeladas no plano antes de 9/11:
>
> 1. A pergunta, em uma frase e com número. "O desequilíbrio dos 3 melhores níveis prevê o movimento do meio em
>    10 s o suficiente para pagar o custo?" Se a resposta for não, acabou essa hipótese; não há "ajustar e tentar de
>    novo" sem gastar um dos 3 testes.
> 2. A conta de poder. Com a amostra que a captura vai ter (pregões de 9 a 20/11, só quadros qualificados, só pregão
>    regular), qual é o menor efeito que o teste consegue detectar com alfa de 1,67 %? Se esse efeito for maior que o
>    custo, o teste não distingue "funciona" de "sorte", e então não roda. Isso evita a falsa confiança das "60
>    sessões" do V1.
> 3. O preço de verdade. Preenchimento em t + 0,5 s + 150 ms, nunca no preço do instante do sinal; taxa pela regra B.
>    Um edge que só existe no preço que ninguém consegue pagar não é edge.
> 4. O que fica de fora, decidido antes. FOMC fora, por duas razões: o movimento é de outra natureza, e, como 28/10
>    mostrou, a própria origem do dado falha sob a carga desses dias (62 min sem entrega, com a sessão viva). P5/P95
>    recalculados pelo mesmo método na troca de contrato, sem virar hipótese nova.
>
> A etapa 3 depende da etapa 2 e aprende com ela: a calibração de P5/P95 e o teste só leem quadros em que o livro
> está qualificado, e um dia com lacuna de entrega conta só até a lacuna. Por isso, antes do teste, precisa existir o
> mínimo da etapa 5 que detecta silêncio e atraso crescente com a sessão viva e relê o livro no fechamento de cada
> dia; sem isso, o teste pode rodar sobre buracos que ninguém viu.
>
> O abandono é parte da razão, não um fracasso: se a hipótese exigir mais de 65 % de acerto, ou se o menor efeito
> detectável for maior que o custo, a microestrutura não é um caminho para esta conta e esta máquina, e a V2 vai
> para o laboratório (etapa 10) sem gastar mais um mês. Saber parar com data é o que o V1 não tinha.

**Passos (28/10).** Um commit por passo. Passo de código leva um teste que falha antes e passa depois, e muda o hash
de `identidade_codigo`; depois do passo 10 nenhum `.py` muda até o registro, porque o hash entra no pré-registro.
O código só aceita o pré-registro depois do fim da calibração (6/11) e antes do início do teste (9/11): o passo 11
acontece em 7 ou 8/11, sem folga. A data é a de UTC: o último instante aceito é 8/11 às 20h59 de Brasília. Os passos 1 a 5 vêm da auditoria de 28/10 do código `d70cf28`; os demais, da razão
acima e da tabela da seção 4.

| # | Passo | Origem | Estado |
|---|---|---|---|
| 0 | Razão da etapa, passos e decisões neste plano | saída "commit do plano" | Feito em 28/10 |
| 1 | C1: `calibrar()` exclui a faixa do FOMC (13h55 às 16h de Nova York em 28/10 e 9/12) e conta em `descartados["fomc"]` | emenda (b) de 25/10, que não estava no código | Feito em 28/10: faixa única em `desequilibrio.na_faixa_do_fomc`, usada pela calibração e pelo avaliador; datas em `desequilibrio.FOMC`; 8 testes novos |
| 2 | N5: recuo de `recebido_ns` preso ao último instante e contado; dia recusado só acima do limite declarado | auditoria | Feito em 28/10: `recuos_de_relogio` e `recuo_maior_ns` nas contagens; `RECUO_MAX_NS` 100 ms e `RECUOS_MAX_POR_PREGAO` 100 em `avaliador.py`; motivo `recuo_de_relogio_acima_do_limite`; 5 testes |
| 3 | N6: dia com lacuna de entrega conta até a lacuna, na calibração e no teste | razão do autor | Feito em 28/10: `LACUNA_MAX_NS` (60 s) em `desequilibrio.py`, usado pelos dois; calibração conta `depois_da_lacuna`; avaliador: motivo `completo_ate_lacuna` (conta para o mínimo de 7) ou `lacuna_antes_da_abertura`, `corte_ns` no dia, `quadros_depois_da_lacuna` e operação aberta descartada; `pregoes_usados` = completos + até a lacuna; 10 testes |
| 4 | N2: teto de 3 testes, alfa 5 %/3 e prazo 30/04/2027 impostos em `pesquisa.py`; H2 e H3 só depois do resultado da anterior | auditoria, regra 14 | Feito em 28/10: `ALFA_TOTAL`, `MAX_TESTES`, `ALFA_POR_TESTE` e `PRAZO_FINAL` em `pesquisa.py`; `max_hipoteses` e `alfa_familia` deixam de ser campos da proposta (o orçamento vai no registro); recusas `alfa_diferente_do_orcamento`, `teste_depois_do_prazo`, `prazo_dos_testes_encerrado`, `teste_anterior_sem_resultado`, `orcamento_de_testes_esgotado`; 4 testes novos |
| 5 | C3: critério por pregão (permutação por inversão de sinal) ao lado do bootstrap em blocos; os dois têm de passar | auditoria | Feito em 28/10: `avaliador.por_pregao` (exato até 2^n ≤ reamostras, senão sorteio com semente e correção (b+1)/(m+1)); `julgar` exige limite inferior > 0 E p ≤ alfa/2; relatório com `por_pregao` e `criterio`; versão 0.2.6; 5 testes. Consequência escrita: com 7 pregões o menor p é 1/128 = 0,78 %, logo só passa com todos os 7 positivos; com 10, 1/1024 |
| 6 | Checklist 1: custo de ida e volta (taxa pela regra B, atraso) contra o movimento do meio em 10 s medido na calibração sem condicionar a sinal; acerto necessário; abandono acima de 65 % | seção 4 | Feito em 28/10: `qhos2.custo` mede, nos mesmos quadros da calibração e uma janela por vez (sem sobreposição), o meio e o spread do livro vigente em t + 0,65 s e t + 10,65 s; devolve movimento médio absoluto, desvio, fração parada, custo médio de cruzar o spread, histograma e acumuladores por bloco de 5 min; `checklist(medicao, taxa_usd)` dá custo total em ticks, acerto necessário p* = ½ + c/(2m) e veredito (abandono acima de 65 %). A CLI sem `--taxa-usd` só mede e escreve que o checklist aguarda a taxa (regra B). 9 testes. A taxa ainda não existe: ver pendências |
| 7 | Checklist 2: menor efeito detectável com alfa 1,67 % e poder 80 %, para o bloco de 5 min e para o critério por pregão, com a dependência estimada só na calibração; abandono se maior que o custo | seção 4, regra 15 | Feito em 28/10: `qhos2.poder`: `mde_por_operacao` = (z(1−α/2)+z(0,8))·σ·√(deff/n); `efeito_de_desenho` por correlação intraclasse (ANOVA) dos blocos de 5 min da medição do custo, deff = 1+(b−1)·ρ com o maior ρ (com sinal ou absoluto); `mde_por_pregao` por simulação (somas diárias normais, mesmos sorteios, bissecção) sobre `avaliador.p_inversao_de_sinal` (agora exato por metades); `contar_sinais` conta os sinais que a regra daria só com D contra P5/P95 (rearme, uma posição, perto do fechamento); `checklist` = maior MDE dos dois critérios em ticks por operação contra o custo. 8 testes. Precisa de P5/P95 (7/11) para `ops_por_pregao` |
| 8 | Avaliador em dado sintético: sem edge, falso positivo na taxa do alfa; edge plantado do tamanho do menor efeito, poder perto de 80 %; atraso, taxa e FOMC fazem o que está escrito; mesma entrada, mesmo hash | seção 4 | Feito em 28/10: `qhos2.avaliador_sintetico`. Mecânica: pregão sintético de estados (passeio aleatório, spread 1 tick, sinais plantados, edge como salto do meio 2 s depois do sinal) por `simular`: sem edge média bruta −1 tick (o spread); com edge E, E − 1; entrada até 0,65 s depois do sinal e saída 10 s depois da entrada; FOMC não dispara das 13h55 às 16h; taxa sai do líquido; mesma entrada, mesmo SHA-256 do relatório. Decisão: pregões com operações normais nos blocos de 5 min e a decisão real (blocos E por pregão): sem edge, PASSOU ≤ 2,5 % (alfa/2 = 0,83 %); no MDE do `poder`, poder conjunto entre o produto e o mínimo dos dois critérios (medido, ver registro). 12 testes. CLI com os parâmetros reais em 7/11 |
| 9 | Mínimo da etapa 5 antes do teste: silêncio e atraso crescente detectados com a sessão viva; livro relido no fechamento de cada dia | razão do autor | Feito em 29/10 (madrugada): `qhos2.vigia`, processo só de leitura ao lado da captura (mesmo `Leitor` do coletor, prioridade baixa), diário próprio em `<data_root>/vigia/<captura>.jsonl`; nada muda na captura, na sessão, no gravador, no agendador nem no coletor (regra 21). Ao vivo: `silencio` (> 60 s sem quadro novo, sem manifesto e com a leitura em dia), `lacuna` quando os quadros voltam (ou `silencio_sem_lacuna` se o dado não mostra o buraco: a gravação é que parou), `atraso_crescente` (mediana por minuto de chegada − carimbo do servidor sobe 3 minutos seguidos, ≥ 0,5 s cada, e passa de 1 s), `livro_valido`/`livro_invalido` no instante exato (livro próprio, quadro a quadro). Fechamento (manifesto conferido, ou `--captura` numa captura encerrada): releitura independente do zero comparada com o livro seguido ao vivo (`igual_ao_vivo`), lacunas e atraso por minuto com a mesma regra, primeiro instante inválido, fração qualificada, sessão (heartbeats, logout, `encerrada_normalmente`), `silencio_final` do último quadro ao fim da sessão, e `achados` (vazio = dia limpo; código de saída 1). 19 testes |
| 10 | Emendas como texto conferido contra o código, suíte verde, hash final de `identidade_codigo` | seção 4 | Feito em 29/10: 23 regras (seção 10 com as emendas de 24/10 a 29/10, decisões desta seção, checklists e vigia) conferidas contra o código, nenhuma divergência e nenhuma mudança de código; 6 leituras do texto que o código adota (o autor pode vetar até o registro) e 6 condições para o registro e a avaliação, em `docs/provas/etapa3_20261029/conferencia_passo10.md`. Suíte em clone limpo verde (1326 testes, 2 pulados). **Código congelado: `qhos2/src/qhos2` de `14eb165`, `identidade_codigo` = `43a1de514b22461b2008a3b978d3c101d265b64d3962ee3ef6ff07a94ee7dcea`** (38 arquivos, Python 3.12.3; lista em `identidade_codigo.json` na mesma pasta). O hash só se reproduz de um clone limpo com o Python do venv: a pasta OneDrive (CRLF) e o pacote instalado (arquivos do `terminal` a mais) dão outros |
| 11 | 7 ou 8/11: calibrar 26/10 a 6/11, conferir os checklists com os números reais, pré-registrar (pergunta, refutação, P5, P95, taxa, alfa, datas do FOMC), commit e push | seção 4 | Roteiro pronto e ensaiado em 29/10 (`operacao/wsl/passo11.py`, roteiro em `docs/RUNBOOK_CAPTURA.md`); execução em 6/11 à noite (medir) e 7 ou 8/11 (registrar, depois da aprovação do autor) |

**Decisões do construtor em 28/10**, cobertas pelo "faça sozinho os passos 0 a 5" do autor; o autor pode vetá-las
até o registro de 7/11, e cada uma está escrita como emenda de 28/10 na seção 10:

- N5: recuo de `recebido_ns` é preso ao último instante visto e contado em `recuos_de_relogio`; o dia é recusado se
  um recuo passar de 100 ms ou se houver mais de 100 recuos no pregão (constantes de `avaliador.py`).
- N6: lacuna = intervalo maior que 60 s entre quadros recebidos que toque o pregão regular (o mesmo limiar de antes).
  Na calibração, os quadros depois da lacuna são contados em `depois_da_lacuna` e não entram no histograma. No teste,
  o dia vale até o último quadro antes da lacuna (`completo_ate_lacuna`), conta para o mínimo de 7 pregões, e a
  operação aberta na lacuna é descartada e contada.
- C3: soma líquida por pregão; teste de permutação por inversão de sinal sobre a média dessas somas, unilateral,
  com alfa/2 (0,833 %) para ficar na mesma severidade do intervalo bicaudal; enumeração exata quando 2^n cabe nas
  reamostras, senão 10.000 sorteios com a semente fixa. A hipótese só passa se o bootstrap em blocos e o critério
  por pregão passarem.
- N2: `ALFA_TOTAL` 5 %, `MAX_TESTES` 3 e `PRAZO_FINAL` 30/04/2027 como constantes; o pré-registro recusa o 4º
  teste, janela de teste depois do prazo, registro depois do prazo e hipótese nova enquanto a anterior não tiver
  avaliação registrada. Mudar qualquer parâmetro continua sendo hipótese nova.
- O C2 da auditoria (`livro.suspeito` nunca zerado depois de uma anomalia) não é desta etapa: a ressincronização com
  retrato novo está na tabela da etapa 5 desde 28/10, e o formato de captura não muda.
- Taxa pela regra B com teto declarado (decisão do autor em 28/10, "Teto declarado"): sem a tabela documentada até
  7/11, o registro e os checklists usam US$ 12,50 por ida e volta (1 tick); detalhes na seção 10, item 6.
- Vigia (passo 9, 29/10): limiares do silêncio = 60 s (o mesmo da lacuna N6, por construção, para que o aviso ao
  vivo e o corte do dia falem da mesma coisa) e do atraso crescente = mediana por minuto subindo 3 minutos seguidos,
  cada um ao menos 0,5 s acima do anterior e o último acima de 1 s (o limiar de `qhos2.atraso`); um desvio constante
  do relógio não sobe e não dispara. "Sessão viva" ao vivo é "a captura não foi encerrada" (os heartbeats não vão ao
  arquivo); o fechamento confirma pelo manifesto. A vigia não ressincroniza nem muda o avaliador: só registra.
- Silêncio final (decisão do autor em 29/10, "Vale até o último quadro"): se a entrega para antes das 16h e a
  sessão foi encerrada normalmente às 16h ou depois, o dia vale até o último quadro, como na lacuna N6
  (`completo_ate_silencio_final`, conta para o mínimo de 7); sessão parada antes das 16h, ou sem fim confiável no
  manifesto, continua perdendo o dia (`terminou_antes_do_fechamento`). Emenda (e) na seção 10.

## 5. Regras de projeto

1. O caminho de risco e kill switch **nunca fica atrás de uma fila**: thread ou processo próprio, chamada direta.
2. Pesquisa longa roda em processo separado, com limite de memória.
3. Uma fila por classe de trabalho; nada de "devolver" jobs incompatíveis.
4. Deduplicação que, na dúvida, **não** enfileira.
5. "Fresco" só é registrado na conclusão, com contrato explícito (o V1 faz isso certo para dados).
6. Toda tarefa criada tem referência guardada e é esperada no desligamento.
7. Checar RAM antes de admitir trabalho.
8. Gravar o **bruto primeiro**; normalizar depois.
9. Nada é usado antes de verificado (SHA-256, índice contínuo, catálogo).
10. Credenciais só digitadas no prompt, nunca em arquivo, log ou mensagem de erro.
11. Só existem codificadores para pedidos permitidos; o que não está na lista não sai.
12. Cada peça: código, testes (inclusive de mutação), hash conferido, um commit, gitleaks, push.
13. A V2 não importa o V1; baseline simples antes de modelo complexo.
14. Orçamento de hipóteses: no máximo 3 testes pré-registrados até 30/04/2027, com alfa total de 5% dividido
    entre eles (1,67% cada). O validador nunca é afrouxado; mudar um critério conta como teste novo.
15. Nenhum teste é registrado sem a conta do menor efeito detectável com a amostra disponível.
16. Uma consumidora da Rithmic Paper: só a V2 faz login. O V1 fica no Test e recebe o Paper pelo C1. R|Trader Pro
    fechado durante as capturas.
17. Senha nunca em arquivo. A captura contínua (peça 5) guarda a senha só na memória do processo; login recusado
    põe o processo em quarentena, sem nova tentativa; processo que morre não volta sozinho.
18. Congelamento de máquina: nenhum hardware, visor rico, modelo ou feature nova enquanto nenhuma hipótese tiver
    passado, exceto o que um teste registrado exige. Emenda de 25/10 (decisão do usuário), estreita: o terminal
    QHOS, que já existe, pode mostrar os dados da V2 ao vivo (seção 4.1); para o ES, até 21/11, ficam desligados
    os painéis que medem o que o preço fez depois de uma condição ou simulam estratégia. Nenhum visor novo além
    disso.
19. Dinheiro real só depois de: teste aprovado, 20 pregões de sombra, latência de ordens medida e taxa documentada.
    Começa com 1 contrato e limite de perda escrito.
20. Data de parada: 30/04/2027. Sem hipótese aprovada até lá, a assinatura é cancelada e a V2 fica como
    laboratório. Decisão do usuário: nesse caso, buscar outro caminho a partir dos erros registrados, como plano novo
    com orçamento próprio, nunca como testes extras deste.
21. Entre 26/10 e 20/11, mudança no código de captura é crítica: só correção de defeito, registrada aqui com data.
22. Protocolo de tarefa (25/10), para quem constrói, pessoa ou IA: (a) antes de começar, dizer em uma frase a
    tarefa e a etapa ou tarefa da seção 4.1 a que ela serve; o que não serve a nenhuma vira pergunta ao usuário,
    não código; (b) fato externo só com fonte, ou marcado como não verificado; fato do código, só depois de ler o
    código; (c) entregar só depois de rodar os testes e a suíte inteira, com teste de mutação e o hash de cada
    arquivo; a suíte é conferida pelo código de saída, não pela tela; (d) depois do push, conferir o commit no
    GitHub; (e) erro encontrado é dito, com a consequência e a correção. Conversa resumida perde detalhe: o rumo
    é este arquivo.

## 6. Gates

| Gate | Critério |
|---|---|
| 1. Dados | Integridade, livro reconstruído igual no replay, lacunas e desconexões detectadas |
| 2. Alpha | Fora da amostra, custos reais, sem vazamento de futuro, estável entre regimes |
| 3. Execução simulada | Fills, slippage e latência modelados, expectativa líquida positiva em hipótese conservadora |
| 4. Shadow | Sinais em tempo real sem ordem, comparados com as condições reais |
| 5. Execução real | Só após os anteriores, em pequena escala, com kill switch e conformância |

## 7. Fatos medidos

- COMEX/GCZ6 funciona no Paper (captura `9c083748`, verificada).
- Atraso do dado (relógio local menos carimbo remoto, atualizações de livro): p50 de 71 a 80 ms, p99 de ~78 a 101 ms,
  máximo 135 ms, zero negativos. O piso de ~70 ms é compatível com o trânsito São Paulo ↔ EUA (hipótese). A latência
  do caminho de ordens **não foi medida**. Horizonte de decisão sensato: piso de 1 s, alvo de 5 a 10 s.
- Ritmo: ES ~480 quadros/s (60 s); GC ~42/s. São quadros, não atualizações: um quadro 160 pode trazer várias. O pico geral do catálogo é dominado pela rajada do retrato inicial:
  usar `pico_por_template`.
- Pregão regular do ES: 10h30 às 17h de Brasília até 30/10; 11h30 às 18h a partir de 2/11 (fim do horário de
  verão nos EUA). Um pregão inteiro passa de 1 GiB de captura: desde 27/10 o teto padrão é 12 GiB (`--max-gb`), com
  aviso abaixo de 24 GiB livres e recusa abaixo de 12,5 GiB.
- Livro das capturas reais: ordens no fim iguais à contagem independente da inspeção (13664); spread de 1 tick no
  pregão; 4 estados cruzados, todos no meio de quadros com várias atualizações e nenhum no fim de um quadro.
- Não é possível garantir: completude (sem `sequence_number` confiável), alinhamento retrato × atualizações (o MBO não
  tem `is_snapshot`), correspondência com a bolsa (precisa de testemunha externa).

- V1 (retrato `aab7dad`): o orchestrator não toca o dado da Rithmic; o EventBus do V1 só avisa jobs; a cadeia de
  observação e os painéis aceitam só "Rithmic Test" (~10 pontos, `data/rithmic_operational.py:28`); as features de
  microestrutura do V1 são descritivas e não estão ligadas a dado ao vivo.

- Rithmic Paper, fatos por data e canal (conferência de 28/10, etapa 1). `[CONFERIDO]` = lido no `.eml` original em
  `arquivo_local/emails/originais/` (fora do git, só o nome do arquivo entra aqui); `[RELATO]` = veio de captura de
  tela e o original ainda não foi visto. Nenhum dado pessoal nem credencial é copiado para o plano.
  - 02/10, Sales `[CONFERIDO, citado em sales_20261008b_resposta.eml]`: tabela de taxas da Rithmic, por mês-calendário
    ou fração, pagas adiantado (software proporcional no primeiro mês): paper trading por R|Trader, R|Trader Pro ou
    app aprovado US$ 49,99; por aplicação própria com a API US$ 99,99; top of book US$ 2,55 por bolsa CME ou US$ 8,65
    o pacote das 4; profundidade US$ 14,00 por bolsa ou US$ 42,00 o pacote das 4.
  - 07/10, Sales `[CONFERIDO, sales_20261006_resposta.eml]`: Paper pela própria aplicação exige conformidade com o
    time de API; a profundidade é MBO; o user ID existente é convertido em pago, separado do Test e sem afetá-lo;
    sessão de dados padrão = 1, a segunda dobra só a taxa de dados; software proporcional, dados sempre cheios.
  - 08/10, Sales `[CONFERIDO, sales_20261008b_resposta.eml]`: à pergunta sobre uso automatizado sem tela (non-display)
    e sobre gravar e guardar o dado: a classificação não-profissional é autocertificação, a equipe "não é treinada em
    compliance" e a pergunta deve ir à bolsa (CME); a gravação local foi remetida ao time de API. Ou seja, a Rithmic
    não vai responder a licença; a resposta tem de vir da CME ou da autocertificação do autor.
  - 09/10, Sales `[RELATO]`: um login recebe dados por vez; sem cartão (ACH, wire, PayPal, Wise, Zelle); US$ 141,99
    com 1 sessão e US$ 183,99 com 2 (coerente com a tabela de 02/10: 99,99 + 2 × 42,00); segunda sessão sem pró-rata.
  - 12/10, Sales `[RELATO]`: ativação confirmada após o pagamento; user ID habilitado para o pacote CME de
    profundidade com 2 sessões; histórico incluído desde dez/2011.
  - 12/10, RAPI `[CONFERIDO, rapi_20261012_respostas_paper.eml]`: nome do sistema "Rithmic Paper Trading", mesmas
    URIs recebidas na conformidade; a conformidade do nila:QHOS cobre o Paper.
  - 13/10, RAPI `[CONFERIDO, rapi_20261013_sessoes_plants.eml]`: limite por user ID de até 2 sessões simultâneas no
    ticker plant **e** 2 no history plant; Test e Paper não contam juntos; histórico pelo History Plant (SampleBar);
    todos os plants habilitados.
  - 13/10, perfil no R|Trader Pro `[RELATO]`: login expira em 31/10/2026 às 18h29; Market Data máx. 2; Orders máx. 5;
    FCM "Rithmic".
  - 14/10, Sales `[CONFERIDO, sales_20261014_1700_respostas_email2.eml]`: a fatura sai poucos dias antes do fim do mês
    e, paga antes, o acesso é estendido sem vencer; Test e Paper são credenciais separadas (o user ID pode coincidir,
    as senhas não).
  - 14/10, pedido do autor `[CONFERIDO, rapi_20261014_1228_encaminhou_sales.eml]`: confirmação do reset da senha
    Paper. Sem resposta até 28/10.
  - 15/10, Sales `[CONFERIDO, sales_20261015_1414_paper_habilitado.eml]`: user ID Paper "fully enabled" para
    profundidade nas 4 bolsas CME.
  - 16/10, RAPI `[CONFERIDO, rapi_20261016_0958_respostas_7pontos.eml]`: app_name aprovado obrigatório; risco em 3
    níveis (FCM, IB, Trader; o trader define o seu); manutenção de fim de semana varia, login esperado por volta das
    12h ET de domingo; Test sem expiração; sem whitelist; sessões configuradas por Max Session Count de Orders e de
    Market Data por user ID. A manutenção em dia útil (17h20–17h45 ET) estava na pergunta do autor; a RAPI respondeu
    só sobre o fim de semana.
  - 16/10, Sales `[CONFERIDO, sales_20261016_1819_depth_mbo_sessoes.eml]`: profundidade (inclui MBO) nas 4 bolsas
    CME (CBOT, CME, COMEX, NYMEX), sem taxa extra; Max Session Count de dados = 2 (a adicional foi paga); ordens = 5
    (padrão); Paper e Test "duas credenciais totalmente diferentes, separadas em todos os aspectos".
  - 21/10, RAPI `[RELATO]`: o limite de 2 sessões de dados vale somando todos os plants; 5 de ordens; MBO nas 4
    bolsas; manutenção em dia útil ~17h20–17h45 ET (logar depois das 17h45); domingo ~12h ET. **Conflita** com o
    original de 13/10 (2 por plant). Até ver o original de 21/10 vale a leitura restritiva: 2 no total, e o R|Trader
    Pro conta como uma.
- 28/10, varredura final do V1 com a janela de captura aberta (anexos `varredura_v1_20261028_wsl.txt` e
  `varredura_v1_20261028_windows.txt`): o único processo ligado à Rithmic era o agendador (`qhos2.agenda`, PID 4242);
  no WSL só `qhos2-coletor` e `qhos2-brain` habilitados, sem cron nem timers do V1; no Windows as 5 tarefas do V1
  desabilitadas, nenhum R|Trader Pro aberto, cofre de credenciais apagado. Resíduo fora da quarentena, sem credencial
  da Rithmic dentro (conferido por nome de chave): `.env` do Brain V1 em `/etc/qhos`, `/var/lib/qhos-brain` e
  `/opt/qhos` (root) e `C:\ProgramData\QHOS` (admin). Itens 2 e 3 da etapa 1 fechados.
- Etapa 1, pregões capturados pelo agendador diário (`qhos2.agenda` 0.2.3, PID 4242), lidos em `agenda/provas.jsonl`
  e no `manifest.json` de cada captura; "verificada" = `catalogo.verificar_captura` aprovou a captura inteira.
  - Pregão 1 de 5, 28/10, captura `01f4476a`: janela 13:00Z a 20:15Z (10h00 às 17h15 de Brasília); primeiro quadro
    13:00:01Z, último 20:13:02Z (saída 120 s antes do fim, como desenhado); resultado `capturado`, verificada,
    fechamento normal, erro nulo. 7.421.889 quadros: 1.207 do retrato (116), 1.054.914 negócios (150) e 6.365.768
    atualizações de livro (160). Arquivo `market_frames.jsonl` com 2.110.420.721 bytes (1,97 GiB). Login, assinatura,
    retrato completo, cancelamento e logout confirmados; 2.597 heartbeats a 10 s; nenhuma anomalia; nenhum login
    recusado; nenhuma sessão do V1 (varredura acima). Relógio: desvio médio -3,6 ms, ida e volta 5,1 ms, sem alerta.
    Agendador voltou a AGUARDANDO às 20:23Z; próxima janela 29/10 13:00Z.
  - Pregão 2 de 5, 29/10, captura `aeeeeea3` (agendador 0.2.5): janela 13:00Z a 20:15Z; primeiro quadro 13:00:01Z,
    último 20:13:01Z; resultado `capturado`, verificada, fechamento normal, erro nulo. 12.801.038 quadros: 1.152 do
    retrato (116), 1.645.655 negócios (150) e 11.154.231 atualizações de livro (160). Arquivo com 3.636.781.978 bytes
    (3,39 GiB). Login, retrato completo e logout confirmados; 2.597 heartbeats; nenhuma anomalia; nenhum login recusado.
  - Pregão 3 de 5, 30/10, captura `f6644149` (agendador 0.2.5): janela 13:00Z a 20:15Z; gravou de 13:00:01Z a
    17:41:20Z e parou com resultado `interrompido`, erro `conexao_perdida`, tipo de falha `TRANSIENT_TRANSPORT`,
    fechamento não normal (logout não confirmado, transporte não fechado normalmente). A parte gravada está verificada
    pelo catálogo. 9.798.155 quadros: 1.212 do retrato (116), 1.173.971 negócios (150) e 8.622.972 atualizações de
    livro (160). Arquivo com 2.777.572.783 bytes (2,59 GiB). Login e retrato completo confirmados; 1.687 heartbeats;
    nenhuma anomalia; nenhum login recusado. Causa: queda do cabo de rede por cerca de 5 s (relato do autor). Nenhuma
    outra captura do ES em 30/10: o ES ficou sem gravação de 17:41:20Z até o fim da janela. A coleta multi (sessão
    separada, sem o ES) caiu no mesmo segundo e gravou uma segunda parte até 20:13Z.
  - Pregão 4 de 5, 2/11, captura `4c2c210e` (agendador 0.2.6), primeiro pregão depois do fim do horário de verão
    nos EUA: janela 14:00Z a 21:15Z (11h00 às 18h15 de Brasília); primeiro quadro 14:00:01Z, último 21:13:01Z;
    resultado `capturado`, verificada, fechamento normal, erro nulo. 13.364.678 quadros: 1.220 do retrato (116),
    1.696.242 negócios (150) e 11.667.216 atualizações de livro (160). Arquivo com 3.797.582.486 bytes (3,54 GiB).
    Login, retrato completo e logout confirmados; 2.597 heartbeats; nenhuma anomalia; nenhum login recusado. Relógio:
    desvio -0,9 ms, ida e volta 4,3 ms, sem alerta. Cópia publicada no DataSet (`2026-11-02/CME_ESZ6.tar.xz`, sha256
    `414ec219…a35c20f1`, igual ao `SHA256SUMS.txt`, conferido pela sessão QuestionsAndAnswers em 3/11).
  - Pregão 5 de 5, 3/11, captura `c9d4c8e7` (agendador 0.2.6): janela 14:00Z a 21:15Z; primeiro quadro 14:00:01Z,
    último 21:13:02Z; prova registrada às 21:13:35Z; resultado `capturado`, verificada, fechamento normal, erro nulo.
    10.448.394 quadros: 1.276 do retrato (116), 1.250.767 negócios (150) e 9.196.351 atualizações de livro (160).
    Arquivo `market_frames.jsonl` com 2.962.617.942 bytes (2,76 GiB), medido no servidor e igual ao `bytes_brutos` do
    `INDEX.json` do DataSet. Login, retrato completo e logout confirmados; 2.597 heartbeats; nenhuma anomalia; nenhum
    login recusado. Relógio: desvio -3,3 ms, ida e volta 4,5 ms, sem alerta. Cópia publicada no DataSet
    (`2026-11-03/CME_ESZ6.tar.xz`, 322.377.572 bytes, sha256 `d48cf7ea…dc33575f`, recalculado em 3/11 e igual ao
    `sha256_xz` do `INDEX.json` e ao `SHA256SUMS.txt`). Com ele a etapa 1 fecha em 5 de 5.
- 25/10: o coletor externo `qhos-v2-observability` (projeto separado, fora desta V2) mantinha uma sessão Paper
  aberta desde 23/10 sem gravar nada desde 23/10 às 18h00. Foi desligado e desabilitado, junto com `qhos-v2-quant`.
  A captura do V1 segue no Test, com reconexão a cada 60 s e sem a quarentena P9c (construída só em cópia).
- Dependências reais da V2 hoje: biblioteca `websockets` (só a captura ao vivo), certificados TLS e base de fusos do
  sistema; dados em `~/qhos-linux-runtime/data_lake/v2`; Python do `venv-rithmic`; `.gitignore` da raiz; `.proto`
  em `vendor/`. A separação do V1 fica para depois de 20/11.

## 8. Ideias avaliadas

| Ideia | Decisão | Motivo | Reavaliar quando |
|---|---|---|---|
| FPGA, DPDK, 100 milhões de eventos/s | Recusada | A fonte é WebSocket/TCP via Rithmic; não há pacote para acelerar | Houver feed direto da bolsa em colocation |
| Diamond API, colocation Rithmic | Adiada | Exige conta real, produção e histórico; a escada começa por servidor perto de Chicago | O edge existir a 75 ms e crescer com menos atraso |
| POV Engine | Cortada | Com poucos contratos a participação é ~0% | Tamanho de ordem relevante |
| Multi-mercado (GC, NQ, ES) e motor cross-market | Adiada | Cada instrumento multiplica carga e hipóteses | Uma hipótese validada em 1 instrumento |
| Transformer, estado latente Z_t (últimos 10.000 eventos) | Adiada | Ver abaixo | Gate 2 passado e baseline simples superado |
| Databento como segunda fonte | Aceita como testemunha externa | Única forma de checar correspondência com a bolsa | Custo verificado |
| Eventos macro | Aceita como filtro de risco (calendário) | Evitar janelas de notícia, sem virar entrada do modelo | Peça 11 |
| Ciclo observar → medir → decidir → aprender | Aceita, com portão de promoção | Sem portão vira o GA do V1 ligado à execução | |
| Tela própria estilo TradingView (DOM, Times and Sales, heatmap, footprint) | Aceita como V-1 a V-5 | Uso pessoal; replay primeiro; sem ordens; só a biblioteca aberta Lightweight Charts, com a atribuição exigida | Peças 4e e 6b feitas |
| Plataforma pronta (Bookmap, ATAS, Sierra Chart) para olhar o mercado | Em aberto | Faz as telas hoje; ocupa mais uma sessão de dados | Decisão do usuário |
| Dados derivados da V2 como genes do GA | Recusada | Explode o número de testes; o GA já testou 4955 e aprovou 0 | |

**Transformer / Z_t.** (1) Dados: hoje há ~40 mil eventos gravados; um modelo de atenção precisa de ordens de
grandeza mais. (2) Acurácia estatística não é sinal operável, e modelos simples podem igualar redes profundas
(crítica em https://arxiv.org/abs/2403.09267v1; comparação com regressão logística e XGBoost em
https://arxiv.org/html/2506.05764v2; baseado em resumos de busca, os textos completos não foram lidos). Não achei,
nas buscas, retorno líquido de custos e latência de uma rede profunda. (3) Arquitetura e hiperparâmetros são hipóteses:
contam no n_test. (4) Latência. Regra: baseline simples
primeiro (regressão logística em OFI, desequilíbrio e microprice); rede profunda só se superar o baseline fora da
amostra, após custos. O Z_t conceitual já existe como `MarketState` (peça 7).

## 9. Pendências externas

- Sales: sessões (total ou por planta), licença para segundo host, licença dos `.proto` versionados, conformância
  para produção, custo do R\|API+ e do Diamond, confirmação escrita de Paper com dado ao vivo via R\|Protocol,
  alinhamento retrato × atualizações do MBO.
- Licença CME: fechada em 28/10 por decisão do autor. O uso continua como não-profissional, pela autocertificação
  feita na criação do user ID (a Sales, em 08/10, confirma a autocertificação e os acordos assinados). Nenhuma
  consulta à CME.
- Taxa real por contrato (ida e volta) no ES: é o critério de sucesso da hipótese (seção 10). Fato externo, não
  parâmetro ajustável. Pela regra B de 25/10 (seção 10, item 6), é registrada antes de qualquer avaliação. Desde
  28/10, sem documento até 7/11 vale o teto declarado de US$ 12,50 (1 tick); a tabela documentada continua pendente
  para as etapas 7 a 9.
- Renovação do Paper até 30/10 (vence em 31/10 às 18h29; ~US$ 183,99 por mês, relato); depois, todo dia 25.
- E-mail das 4 confirmações à Rithmic (API, MBO de GC, MGC e ES, gravação local, licença da CME). Em 28/10: MBO
  nas 4 bolsas já confirmado (16/10); a licença sai desta lista (vai à CME); ficam gravação local (time de API),
  reset da senha Paper e a fatura de novembro.
- Cópia das evidências em pen drive (a comprar): capturas da V2, dados e documentos do V1, `.eml`, pastas do
  root em `qhos-maintenance`. O código vivo do V1 já está no GitHub (ramo `v1-vivo-20261025`).
- Lições do V1 destiladas em `qhos2/docs/heranca_v1/` (rascunho em andamento).
- V1: `^VIX` em quarentena; aposentar o GA (ver `INVENTARIO_V1_V2.md`).

## 10. Hipótese pré-registrada (aceita em 23/10)

> No ESZ6, no pregão regular, quando o desequilíbrio dos 3 melhores níveis ficar acima do percentil 95 (ou abaixo
> do percentil 5) — percentis calculados uma única vez nos pregões de 26/10 a 6/11 —, comprar (vender) a mercado
> 0,5 s depois do sinal e zerar a mercado 10 s depois dá resultado médio por contrato maior que a taxa de ida e
> volta, com o intervalo de confiança de 95% inteiro acima dela, medido só nos pregões de 9 a 20/11.

Definições, fixas a partir de agora (mudar qualquer uma é hipótese nova e conta no n_test):

1. **Livro e janela:** livro por ordem da peça 6b; pregão regular = 9h30 às 16h de Nova York. Só conta quadro
   que termina com o livro qualificado (fora do intervalo do retrato, sem anomalia aberta e não cruzado).
   Em dia de decisão do FOMC, os quadros das 13h55 às 16h de Nova York não contam (emenda de 25/10).
2. **Desequilíbrio:** D = (Qc - Qv) / (Qc + Qv), com Qc e Qv a soma das quantidades nos 3 melhores níveis de preço
   de compra e de venda, avaliado ao fim de cada quadro recebido (esclarecimento de 24/10, abaixo).
3. **Percentis:** P5 e P95 de D sobre todos os quadros que contam (item 1) dos pregões de 26/10 a 6/11, pelo
   método do posto mais próximo, com D arredondado a 4 casas e cada quadro pesando 1 (`desequilibrio.py`).
   Calculados uma vez e congelados antes de 9/11. Nessa etapa só a distribuição de D é olhada, nunca o resultado;
   a exceção é o movimento do preço médio em 10 s sem condicionar a nenhum sinal, medido para as checagens de
   custo e de poder da etapa 3 (definição de 25/10). Dia incompleto também conta na calibração.
   Na troca de contrato (ESZ6 para ESH7), P5 e P95 são recalculados pelo mesmo método nos 10 primeiros pregões
   do novo contrato; isso é parte da regra, não hipótese nova (emenda de 25/10).
4. **Sinal:** D > P95 compra; D < P5 vende. Uma posição por vez: sinal que aparece com operação aberta é
   ignorado e contado. No início do pregão e depois de cada saída, o sinal só vale quando D cruza o limiar vindo
   de dentro de [P5, P95] (rearme). Quadro que não conta (item 1) não dispara sinal nem rearma.
5. **Execução simulada, 1 contrato:** entrada 0,5 s depois do sinal, no melhor preço de venda (compra) ou de
   compra (venda) do livro 150 ms depois desse instante; saída 10 s depois da entrada, no melhor preço do lado
   oposto, também 150 ms depois. Os 150 ms (o dobro do atraso medido do dado) são o piso da ida da ordem até a
   bolsa; quando a latência real das ordens for medida, vale a maior (emenda de 25/10). "Livro vigente" num
   instante é o estado do último quadro recebido até aquele instante. Entrada cujo livro vigente não está
   qualificado é cancelada e contada; saída cujo livro vigente não está qualificado é feita no primeiro livro
   qualificado seguinte e contada; sinal cuja saída passaria das 16h é ignorado e contado; operação que não termina
   antes do fim da captura é descartada e contada (definições de 25/10).
6. **Resultado:** em ticks por operação, menos a taxa de ida e volta convertida em ticks (US$ 12,50 por tick).
   Taxa: a da tabela documentada da corretora onde a conta real seria aberta, registrada aqui antes de qualquer
   avaliação. Sem documento, a avaliação espera; a captura de 9 a 20/11 segue sem ninguém olhar (regra B, 25/10).
   **Emenda de 28/10 (teto declarado, decisão do autor):** se a tabela documentada não existir até o registro de
   7 ou 8/11, o pré-registro usa o teto declarado de **US$ 12,50 por ida e volta por contrato (1 tick)**, valor
   proposto pelo construtor e aceito pelo autor em 28/10. Ordem de grandeza, de fontes secundárias e não verificada
   contra a tabela da CME: taxa de bolsa do ES para não membros por volta de US$ 1,38 por lado (páginas de taxas da
   TradeStation e da Interactive Brokers) e custo tudo incluído de US$ 4 a 6 por ida e volta em corretoras de
   varejo; o teto fica acima do dobro disso. O teto é mais severo que qualquer taxa real: se a hipótese passar com
   ele, passa com a taxa documentada. A taxa documentada, quando chegar, não substitui o teto no teste já
   registrado (seria parâmetro novo) e vale só para as etapas 7 a 9; se ela vier acima do teto, a decisão volta ao
   autor antes da etapa 7. Os checklists 1 e 2 da etapa 3 usam o mesmo teto enquanto não houver documento.
7. **Critério:** média líquida > 0 com o intervalo de confiança inteiro acima de 0, por bootstrap em blocos
   de 5 minutos. Pela regra 14, o alfa bicaudal é 1,67% (intervalo de 98,33%), não 5% (definição de 25/10).
   Blocos por dia e pela hora da entrada; 10.000 reamostragens com semente fixa 20261109; percentil pelo posto
   mais próximo (`avaliador.py`).
8. **Amostra:** pregões de 9 a 20/11, não olhados antes do teste; no mínimo 7 pregões completos. Se faltar,
   o teste é adiado, nunca refeito com outros parâmetros. Pregão completo: uma única captura que começa antes
   das 9h30 e termina depois das 16h00 de Nova York, sem intervalo maior que 60 s entre quadros recebidos que
   toque esse período, e com o livro qualificado em pelo menos 95% desses quadros; em dia de FOMC, a faixa
   excluída não torna o dia incompleto. Dia com mais de uma captura não é completo (definição de 25/10).
9. **Medida secundária, que não decide:** acerto de direção do preço médio em 10 s, para separar "sem sinal" de
   "sinal sem lucro". Compara o preço médio no sinal com o do livro vigente 10 s depois; empate fica de fora.
10. **Se falhar:** a V2 segue como laboratório de medição e execução; para o investimento em microestrutura de
    alta velocidade.

Expectativa registrada: direção com algum sinal (Gould e Bonart, 2016, acharam relação forte entre desequilíbrio
da fila e direção do próximo movimento, mais forte em ativos de tick grande), lucro provavelmente consumido por
spread e taxa.

**Esclarecimento de 24/10, aceito pelo usuário antes de qualquer dado de calibração ou de teste:** "avaliado a
cada evento" passou a "avaliado ao fim de cada quadro recebido". Motivo: um quadro pode trazer várias
atualizações, e no meio dele o livro passa por estados que não existiram no mercado; na captura real de 23/10
houve 4 estados cruzados, todos no meio de quadros. Avaliar ali geraria sinais de estados falsos. Nenhum
resultado foi olhado; a mudança corrige a medição, não o critério.

**Complemento de 24/10, aceito pelo usuário antes de qualquer dado de calibração ou de teste:** (a) o item 3
dizia "eventos" e passou a "quadros que contam", para ficar igual ao esclarecimento; o método do percentil foi
fixado. (b) O item 4 ganhou o rearme. Motivo: com o limiar em P5/P95, cerca de 10% dos quadros ficam além dele
(~19 por segundo no minuto real de 23/10); sem rearme, a saída de uma operação seria seguida na hora por outra
entrada no mesmo desequilíbrio, e o teste contaria várias vezes um único episódio. Esta é uma mudança de regra,
não só de redação; vale sem contar no n_test porque nenhum dado da janela de calibração ou de teste existia.

**Emendas de 25/10, aceitas pelo usuário antes de qualquer cálculo sobre dados de calibração (a primeira captura de
calibração é de 26/10):** (a) entrada e saída usam o livro 150 ms depois do instante da ordem (item 5); (b) quadros
de dia de FOMC, das 13h55 às 16h de Nova York, não contam (item 1; na calibração, 28/10); (c) na troca de contrato,
P5 e P95 são recalculados pelo mesmo método (item 3); (d) taxa pela regra B (item 6). Motivo: a análise adversária de
24 e 25/10 mostrou que o teste preenchia ordens num preço que, na vida real, já não existe quando a ordem chega, e
que uma tarde de FOMC deformaria o limiar. Nenhum dado foi olhado.

**Emendas de 28/10, antes de qualquer cálculo sobre dados de calibração ou de teste (a calibração só roda em 7/11):**
(a) item 8 e item 3: dia com lacuna de entrega (intervalo maior que 60 s entre quadros recebidos que toque o pregão)
conta até o último quadro antes da lacuna, em vez de ser descartado; na calibração os quadros seguintes não entram;
(b) item 7: além do bootstrap em blocos, critério por pregão (permutação por inversão de sinal sobre a soma líquida
diária, unilateral, alfa/2); a hipótese só passa se os dois passarem; (c) item 8: recuo do relógio de recebimento é
preso ao último instante e contado; o dia é recusado acima de 100 ms num recuo ou de 100 recuos; (d) regra 14 imposta
no código do pré-registro (3 testes, 5 %/3, 30/04/2027, hipótese nova só com a anterior avaliada). Motivo: a captura
de 28/10 (62 min sem entrega com a sessão viva) mostrou que "qualquer lacuna perde o dia" jogaria fora 5 h de livro
perfeito e que um único recuo de relógio perderia um dia inteiro; a auditoria de 28/10 apontou que o bootstrap em
blocos de 5 min não cobre a dependência dentro do pregão e que o orçamento de testes era autodeclarado. Nenhum
resultado foi olhado. A exclusão do FOMC na calibração já constava da emenda (b) de 25/10 e não estava no código:
corrigida em 28/10 (C1), antes de qualquer calibração.

**Emenda de 29/10 (decisão do autor), antes de qualquer cálculo sobre dados de calibração ou de teste:** (e) item 8:
se o último quadro de um pregão chega antes das 16h de Nova York e a sessão foi encerrada normalmente às 16h ou
depois (o manifesto registra `transport_closed_normally` e o instante `criado_em`, escrito depois do logout), o
intervalo do último quadro ao fim da sessão é tratado como a lacuna da emenda (a): o dia conta até o último quadro
(`completo_ate_silencio_final`), a operação aberta é descartada e contada, e os 95 % de livro qualificado valem até
ali. Sessão parada antes das 16h, ou sem fim confiável no manifesto, continua perdendo o dia. Motivo: é o mesmo
fenômeno da lacuna de 28/10 (a origem para de entregar com a sessão viva), só que sem quadro depois; sem a emenda,
uma entrega que parasse às 15h43 perderia 5 h de livro perfeito, e a mesma parada às 15h43 com um quadro às 16h01
contaria. A calibração já contava esses quadros (não exige o fechamento). Nenhum resultado foi olhado.

## 11. Registro de decisões

- 2026-10-23: renovar a Rithmic por 1 mês; V1 permanece ligado; V2 isolada do V1.
- 2026-10-23: COMEX confirmado; atraso medido; horizonte mínimo de 1 s.
- 2026-10-23: fluxograma corrigido (portão de promoção, risco independente, bruto primeiro, shadow).
- 2026-10-23: início da peça 4 pela ordem de prioridade; contrato do fio verificado contra a biblioteca oficial.
- 2026-10-23: peça 4b feita: sessão completa contra servidor falso; cada heartbeat mede o desvio do relógio.
- 2026-10-23: peça 4c, código pronto: conector WebSocket/TLS, gravação verificável pelo catálogo e CLI com parada segura; falta o teste real de 60 s.
- 2026-10-23: peça 4c feita. Teste real de 60 s no Paper (CME/ESZ6): login, referência, assinatura, retrato, cancelamento e logout confirmados; 11630 quadros (1333 do retrato e 10297 atualizações); atraso p50 75,7 ms, p99 84,7 ms, máximo 89,4 ms, nenhum negativo; ida e volta do heartbeat 5,3 ms.
- 2026-10-23: observações abertas do teste real, para a peça 6: nenhum quadro 161 (fim de evento) em 60 s e os 1333 quadros do retrato vieram sem carimbo de tempo.
- 2026-10-23: fluxograma integrado aprovado (seção 2), com contratos C1 a C4 e fases I a IV; peças novas 4d, 4e, 6a, 6b e visor V-1 a V-5; o visor depende das peças 4e e 6b, não da 7.
- 2026-10-23: aposentar a captura "Rithmic Test" do V1 na fase II; os dados derivados da V2 não entram no GA, só em hipóteses pré-registradas.
- 2026-10-23: peça 4d, código pronto: decodificação do conteúdo de 116, 160 e 161 conferida contra a biblioteca oficial e inspeção só de contagens (sem preço, tamanho ou id). Pelo esquema oficial, o 160 traz listas repetidas (várias atualizações por quadro) e o 116 não tem campo de tempo: o retrato "sem carimbo" é do desenho da mensagem, não falha.
- 2026-10-23: peça 4d feita. Inspeção da captura real de 60 s (CME/ESZ6): id da ordem e prioridade em 100% das 11112 atualizações; nenhum número de sequência (116 e 160) e nenhum 161; 98,4% dos quadros com uma atualização, máximo 34; retrato com 1332 níveis e 13659 ordens. Depois do retrato: zero alteração ou remoção de ordem desconhecida e zero NEW repetido; a conta fecha (13659 + 4954 - 4949 = 13664 vivas). Durante o retrato: 1 remoção de ordem desconhecida. Conclusão: livro por ordem viável; completude vigiada pelo ciclo de vida e por retrato periódico.
- 2026-10-23: peça 4e, código pronto: pedido 100 só com o bit de último negócio (assinar e cancelar), resposta 101 e quadros 150 gravados; a inspeção liga cada negócio à ordem passiva do livro (id já visto e vivo no instante do negócio). Falta o teste real com mercado aberto.
- 2026-10-23: teste real da 4e com o mercado fechado (15 min, CME/ESZ6): negócios assinados e cancelados, 89 heartbeats, ida e volta 4,7 ms; só o negócio-retrato chegou (nenhum 160). Ele revelou dois defeitos, corrigidos: o atraso media o carimbo do negócio-retrato (hora do negócio original, ~51 min antes) e disparava alerta falso de relógio; e o catálogo quebrava se o relógio local voltasse no tempo dentro de uma captura. Falta repetir com o mercado aberto.
- 2026-10-23: peça 6a feita: eventos normalizados em ticks inteiros, com origem e assinatura determinística, e mercado sintético com o livro verdadeiro a cada passo. Validada nas duas capturas reais (24772 e 6565 eventos, nenhuma recusa, contagens iguais às da inspeção, assinatura igual em duas leituras). A CLI ganhou `--max-gb`. Corrigido: o pregão regular é 10h30 às 17h de Brasília, não 13h30 às 20h (aquilo era UTC).
- 2026-10-23: hipótese pré-registrada (seção 10), versão revisada aceita pelo usuário: percentis em vez de limiar fixo, preços executáveis em vez do preço médio, entrada 0,5 s depois do sinal e bootstrap em blocos. Falta só a taxa real por contrato.
- 2026-10-24: peça 6b feita: livro por ordem provado contra o gabarito a cada passo e validado nas duas capturas reais. O cruzamento passou a ser julgado ao fim de cada quadro, porque no meio de um quadro o livro passa por estados que não existem no mercado (confirmado nos dados: 4 casos, todos no meio). A fila limitada e o retrato periódico pedido pela sessão foram para a peça 5, onde vive o caminho ao vivo.
- 2026-10-24: esclarecimento da hipótese aceito pelo usuário: o desequilíbrio é avaliado ao fim de cada quadro, não a cada evento, e só conta quadro que termina com o livro qualificado. Registrado antes de qualquer dado de calibração ou de teste.
- 2026-10-24: peça 7, parte da hipótese, feita: desequilíbrio dos 3 melhores níveis ao fim de cada quadro e calibração que lê só os quadros qualificados do pregão regular dentro de 26/10 a 6/11 e devolve só P5, P95 e contagens; captura recusada no meio não deixa nenhum quadro na calibração. Validada nos dados reais: com a janela oficial, 12897 de 12897 quadros descartados como fora da janela; no minuto de 23/10 (janela de validação, não de calibração), 10297 quadros usados e 1333 não qualificados (retrato).
- 2026-10-24: complemento da hipótese aceito pelo usuário (seção 10): uma posição por vez, com rearme, e percentil pelo posto mais próximo sobre os quadros que contam. Taxa real por contrato ainda desconhecida.
- 2026-10-25: autópsia do V1 e plano reescrito: 10 etapas (seção 4) e regras 14 a 21 (seção 5), aprovadas pelo usuário, com os números de parada (3 testes, 30/04/2027, 65% de acerto, 20 pregões de sombra).
- 2026-10-25: emendas da hipótese (seção 10) aprovadas antes de qualquer cálculo sobre dados de calibração; taxa pela regra B.
- 2026-10-25: só a V2 entra no Paper; coletor externo `qhos-v2-observability` (sessão-zumbi desde 23/10) e `qhos-v2-quant` desligados e desabilitados; o V1 fica no Test; ensaio de banca falsa do V1 cancelado.
- 2026-10-25: V1 vivo que estava fora do git preservado no ramo `v1-vivo-20261025` (437 arquivos, sem `.env`, credenciais, dados ou `.eml`; gitleaks limpo). A tag `v1-running-20261022` já existia.
- 2026-10-25: correção: a captura ao vivo da V2 depende da biblioteca `websockets` (agora declarada no `pyproject.toml`); "só biblioteca padrão" vale para o resto.
- 2026-10-25: defeito encontrado na 6b: a Rithmic orienta guardar as atualizações recebidas durante o retrato e aplicá-las depois do fim; a 6b aplica na hora, o que pode deixar ordem fantasma. Correção na etapa 2, antes de calcular P5/P95; o bruto gravado não muda.
- 2026-10-25: definições da hipótese fixadas ao construir o avaliador (seção 10, itens 3, 5, 7, 8 e 9), aprovadas pelo usuário antes de qualquer dado de calibração: alfa de 1,67% pela regra 14, casos de borda da execução, detalhes do bootstrap, pregão completo, medição do movimento sem sinal na calibração.
- 2026-10-25: marco de independência da V2 registrado (seção 4).
- 2026-10-25: etapa 2 concluída. Livro com as atualizações recebidas antes do fim do retrato guardadas e aplicadas depois; nas 3 capturas reais, nenhuma anomalia depois do retrato, nenhum quadro qualificado terminando cruzado, nenhuma ordem fantasma (a remoção desconhecida durante o retrato de 23/10 continua desconhecida após a reaplicação, logo não era fantasma).
- 2026-10-25: avaliador da hipótese (`avaliador.py`) construído e testado só em dado montado à mão; a CLI recusa rodar antes de 21/11.
- 2026-10-25: integração do terminal QHOS registrada (seção 4.1): em paralelo às etapas, só lendo o que a captura grava; tarefas 1 a 4 feitas (terminal versionado, `ws://` só em 127.0.0.1, leitura ao vivo igual ao replay em 2 capturas reais, coletor com Host, Origin e token).
- 2026-10-25: regra 18 emendada de forma estreita (decisão do usuário): o terminal pode mostrar os dados da V2; para o ES, até 21/11, estudo de eventos, carteira experimental, MBO e consulta de fila e simulador de ordens desligados; de 9 a 20/11, ao vivo liberado e histórico escondido até 21/11.
- 2026-10-25: regra 22, protocolo de tarefa.
- 2026-10-25: tarefa 5 da integração feita: o coletor segue a captura e manda livro, negócios e cotação ao terminal (livro igual ao do replay no navegador); para o ES, estudo de eventos e MBO recusados até 21/11 (regra 18).
- 2026-10-25: integração do terminal passa a se chamar etapa 0 (seção 4.1), em paralelo às etapas 1 a 4.
- 2026-10-25: tarefa 6 da etapa 0: livro igual ao replay nas 3 capturas reais; negócios ficam para a conferência da captura de 26/10.
- 2026-10-25: tarefa 8 da etapa 0 feita: o coletor serve o terminal em `http://127.0.0.1:8766/`; o terminal não depende mais do V1.
- 2026-10-25: confirmado que a captura do V1 (`adapters.rithmic --soak`, sem `--system paper`) está no Test; o Paper fica só para a V2 (regra 16).
- 2026-10-25: primeiro teste da etapa 0 na máquina do usuário: o navegador do Windows abriu o terminal servido pela V2 e conectou no coletor (ESZ6 com as especificações certas, "dados atrasados" com captura de outro dia). Achado: a legenda das médias guarda valores do ativo anterior ao trocar de contrato (defeito do terminal original); correção pedida ao construtor.
- 2026-10-25: tarefa 9 da etapa 0, lado do coletor feito: mensagem `pipeline` com uma bolinha por camada.
- 2026-10-25: tarefa 10 da etapa 0 feita: histórico de candles a partir das capturas da V2, com a janela de teste escondida até 21/11. Erro pego antes da entrega: os cortes tinham sido escritos com o ano de 2025; agora um teste os recalcula pelos fusos.
- 2026-10-25: correção da legenda do construtor conferida e aplicada no terminal: só o módulo `app.js` mudou (`loadChart` limpa EMA e OHLC ao trocar contrato, fonte ou período; `activateInstrument` atualiza cotação, lista e cartão na hora); os outros 15 módulos ficaram idênticos byte a byte. No navegador, a versão anterior mostrava as médias do ESZ6 já com o BTC na tela e as do período anterior ao trocar de período; a nova limpa na hora. O lado do terminal da tarefa 9 (Percurso) continua com o construtor.
- 2026-10-25: tarefa 9 da etapa 0 feita do lado do terminal, sem o construtor (ponte e tela do Percurso). No navegador, com uma captura sendo gravada: a bolinha da Rithmic foi de OK para atenção com o arquivo parado há mais de 5 s, voltou para OK e ficou desconhecida com a captura encerrada; sem coletor, "Sem percurso". Tarefa 11 adiada para depois de 21/11.
- 2026-10-25: defeito achado na primeira captura real com negócios (`798f765e`, 15 min na sessão noturna, Paper, verificada): 1523 dos 4675 quadros 150 só traziam volume (`presence_bits` 8, sem o bit LAST_TRADE do `last_trade.proto` da Rithmic), e a leitura dos eventos recusava a captura inteira (`negocio_sem_preco`). Isso pararia o livro, a conferência, o histórico, o coletor ao vivo e a calibração da hipótese. Corrigido na peça 6a (`eventos.py`): 150 sem o bit LAST_TRADE não vira evento; 150 com o bit e sem preço continua parando a leitura. A gravação não mudou (regra 21). Na mesma captura, o atraso p99 dos negócios foi 300 ms (antes ~100 ms); a conferir no pregão regular.
- 2026-10-25: tarefa 6 da etapa 0 fechada com dados reais: `--conferir` IGUAL na captura `798f765e` (livro e 3151 negócios), depois da correção do quadro 150.
- 2026-10-25: tarefa 7 da etapa 0 feita com mercado real (captura `8aa43f0a`); conferência IGUAL com 2781 negócios. Atraso dos negócios nessa captura: p50 75 ms, p99 85 ms, máximo 90 ms; o p99 de 300 ms da captura anterior não se repetiu (a confirmar no pregão regular). Defeito conhecido do Percurso, a corrigir: quando a leitura falha, a camada Gravação aparece em vermelho, mas a falha é da leitura; a gravação deveria ficar em cinza. Pedido do usuário, ainda sem decisão: conexão automática do terminal depois do F5 e coletor sempre ligado.
- 2026-10-25: a captura `e6946587` (19:43 a 19:51 Brasília, 24.760 quadros) foi parada com saída segura (cancelamento e logout confirmados, estado FECHADA), e a `798f765e` começou logo depois; não houve login duplicado. Regra de operação: antes de iniciar uma captura, conferir com `ps` que não há outra rodando.
- 2026-10-25: tarefa 12 aberta pelo usuário (conexão automática). Decisões: o token não fica guardado no navegador, vem de `GET /conexao` com checagem de `Sec-Fetch-*`; o terminal só conecta sozinho se a última fonte usada foi a Rithmic; coletor sempre ligado fica para depois do pregão inteiro de 26/10, fora do horário de captura.
- 2026-10-28: licença da CME não será consultada (decisão do autor); uso não-profissional por autocertificação; o critério de abandono da etapa 1 por licença é considerado não aplicável. Item 6 da etapa 1 fechado quanto à licença; a tabela de taxas da Rithmic já consta na seção 7 (02/10).
- 2026-10-28: pregão 1 de 5 da etapa 1 capturado e verificado pelo agendador (captura `01f4476a`, 7.421.889 quadros, 1,97 GiB, sem login recusado, sem sessão do V1); medições na seção 7. Faltam os pregões de 29/10, 30/10, 2/11 e 3/11.
- 2026-10-28: etapa 2 refeita e fechada com a 0.2.5 (`57388f3`). O livro já guardava as atualizações recebidas durante o retrato e as aplicava depois (desde 25/10); faltava a conta que o plano pede: `reaplicadas` mede, ao fim do retrato, o que a regra errada (aplicar na hora) teria deixado: aplicadas, fantasmas e defasadas. Releitura das 11 capturas reais (23 a 28/10, de 0,3 s a 7 h): retrato íntegro em todas; **1 fantasma** que existiria (`c4ff575e`, 23/10), confirmado por uma ferramenta independente (`qhos2.inspecao`, que aplica na hora, termina com 13.673 ordens contra 13.672 do livro certo); nenhuma defasada; zero anomalias depois do retrato em 10 de 11; nenhum quadro qualificado termina cruzado (por construção; `88e31bf5` e `de39279b` tiveram 46 e 24 quadros cruzados no fim, não qualificados nesses instantes). 38 testes do livro; suíte completa verde em clone limpo. Tabela e saídas brutas em `docs/provas/etapa2_20261028/`.
- 2026-10-28: achado na primeira captura do agendador (`01f4476a`, FOMC): o livro ficou perfeito das 10:00:02 às 15:43:03 BRT e inválido a partir de 16:45:39, porque **nenhum quadro de mercado chegou por 62 min** (3.756 s) com a sessão viva (2.597 heartbeats, um a cada 10 s, sem anomalia, sem retrato novo). Antes do silêncio o atraso de chegada subiu em linha reta de 80 ms (15:30) para 132 s (15:43); depois dele voltou a 72 ms, com carimbo da origem de 16:45:39: a hora entre 15:40:50 e 16:45:39 não foi entregue. Teto local medido no mesmo dia: 59.440 quadros/s pelo caminho da sessão (gravador 163 mil/s), contra ~1.300/s que chegavam na rampa; o gargalo e o silêncio estão fora desta máquina. Decisão do autor: a captura vale até 15:43:03 BRT; o caso vai à etapa 5 (três itens novos na tabela) e à Rithmic (pergunta sobre política de consumidor lento e lacuna na sessão). Erro de operação do dia, registrado: suítes pesadas rodaram no WSL durante a janela de captura; regra: nada pesado entre 10:00 e 17:15 BRT em dia de pregão.
- 2026-10-28: etapa 3 aberta com a razão escrita pelo autor e 12 passos (seção 4.2). Auditoria da base do avaliador (C1, N5, N6, N2, C3) autorizada pelo autor ("faça sozinho os passos 0 a 5"); decisões do construtor na seção 4.2 e emendas de 28/10 na seção 10. Achado de leitura: o código só aceita o pré-registro em 7 ou 8/11.
- 2026-10-28: passos 0 a 5 da etapa 3 feitos, um commit por passo (`2ee5762` plano, `8610873` C1, `0f0f12f` N5, `ded0dc4` N6, `ec29575` N2, `3131bb1` C3 e versão 0.2.6), suíte inteira verde em clone limpo (2 pulados: sem `cryptography`/Node no WSL). `identidade_codigo` passou de `114f9016…9bc1b2` (b734f71) para `0903a28f…3c04c` (3131bb1); diff em `avaliador.py`, `desequilibrio.py`, `pesquisa.py` e `__init__.py`. A 0.2.6 não está instalada: os serviços seguem na 0.2.5 até a troca combinada. Passos 6 a 11 pendentes.
- 2026-10-28: passos 6 e 7 da etapa 3 feitos (`a65ed29` `qhos2.custo`, `ed825dd` `qhos2.poder` e `avaliador.p_inversao_de_sinal` por metades); suíte em clone limpo verde (1285 testes, 2 pulados); `identidade_codigo` = `aaef94fa…e80b`. **Ensaio, não oficial, do `qhos2.custo` no pregão real de 28/10** (captura `01f4476a`, 211 s, 28 MB, uma janela por vez, sem taxa): 4.437.077 quadros que contam, **1.488 amostras**, movimento médio absoluto do meio em 10 s **1,82 ticks**, média −0,02, desvio 2,62, **19 % parado**, custo médio de cruzar o spread **1,02 ticks**; descartados: FOMC 1.604.944, depois da lacuna 941.229, fora do pregão 150.549, não qualificado 26; 53 blocos de 5 min. Leitura: só com o spread, sem nenhuma taxa, o acerto necessário seria ½ + 1,02/(2·1,82) = **78 %**, acima dos 65 % de abandono; com US$ 4,50 de ida e volta seria 88 %. É um dia só, de FOMC e cortado às 13h55 de Nova York; o checklist oficial é o de 7/11 sobre 26/10 a 6/11, com a taxa pela regra B. O movimento é o não condicionado (como o plano manda); um sinal que selecione movimentos maiores não pode ser olhado na calibração. A taxa ainda não existe e bloqueia o registro de 7/11.
- 2026-10-28: decisão do autor ("Teto declarado"): sem a tabela documentada da corretora até o registro de 7 ou 8/11, a taxa de ida e volta do pré-registro e dos checklists é o teto de US$ 12,50 por contrato (1 tick), proposto pelo construtor; emenda na seção 10, item 6, e na seção 4.2. Com o teto, o ensaio de 28/10 daria acerto necessário de ½ + 2,02/(2·1,82) = 105 %: nenhum acerto paga; o número oficial é o de 7/11.
- 2026-10-28: passo 8 da etapa 3 feito (`7a19091` `qhos2.avaliador_sintetico`, `aef815a` correção: o ensaio usa o teste por pregão exato como o avaliador real); suíte em clone limpo verde (1297 testes, 2 pulados); `identidade_codigo` = `512fc57e…22b0`. **Ensaio de decisão com parâmetros perto do real** (desvio 2,62 ticks do pregão de 28/10, 200 operações por pregão como ordem de grandeza, 10 pregões, alfa 1,67 %, taxa pelo teto, 100 corridas, 300 reamostras no bootstrap): MDE por operação 0,19 tick, por pregão 0,23 tick por operação, logo **MDE = 0,23 tick líquido por operação**; sem edge, 0 de 100 PASSOU (falso positivo na taxa do alfa); com edge igual ao MDE, **82 % PASSOU** (bloco 94 %, por pregão 83 %): a conta de poder do `qhos2.poder` confere com a decisão real, e o critério por pregão é o que manda. Lição registrada: com reamostras reduzidas o teste por pregão sorteado é mais fraco que o exato (63 % em vez de 82 %), por isso o avaliador real e o ensaio o mantêm exato até 13 pregões.
- 2026-10-29 (madrugada): passo 9 da etapa 3 feito (`39a63c0` `qhos2.vigia`, 19 testes; OPERACAO.md e RUNBOOK atualizados); suíte inteira em clone limpo verde (1316 testes, 2 pulados); `identidade_codigo` = `c33fa5a4…c0458` (38 arquivos). Nenhum arquivo do caminho ao vivo mudou (`captura`, `sessao`, `gravador`, `agenda`, `coletor`, `leitura`): diff só em `vigia.py` novo. A vigia só roda ao vivo depois de a 0.2.6 ser instalada (troca combinada com o autor; a 0.2.6 não muda a captura); até lá o fechamento de cada dia gravado pode ser feito com `--captura`. Limiares e ponto aberto (`silencio_final`) na seção 4.2. **Ensaio, não oficial, do fechamento no pregão real de 28/10** (captura `01f4476a`, só leitura, 203 s, 34 MB de memória, relatório fora da raiz de dados): verificada; sessão FECHADA, logout confirmado, 2.597 heartbeats (esperados ~2.598), `encerrada_normalmente`; **lacuna 18:43:03Z a 19:45:39Z (3.756 s), sessão viva** (a mesma hora não entregue que a etapa 2 achou); **atraso crescente detectado uma vez**, minutos 18:32Z a 18:35Z com medianas 0,36 s → 6,6 s → 38,9 s → 65,5 s (o aviso teria saído às 18:36Z = 15:36 BRT, 7 min antes do silêncio; o pico foi 132,7 s às 18:43Z; mediana do dia 81,7 ms; depois da lacuna 75–80 ms); livro: suspeito desde o primeiro quadro depois da lacuna (19:45:39Z), primeiro quadro não qualificado às 13:41:06Z (cruzado passageiro, não suspeito), 6.191.092 qualificados de 7.132.359 depois do retrato (86,8 % do dia inteiro, não do pregão até a lacuna); `silencio_final` nulo; `achados` = lacuna, atraso_crescente, livro_invalido. A regra detecta, no dado real, o caso que a motivou.
- 2026-10-28: agendador reiniciado às 18:53 BRT com a 0.2.5 (PID novo, `AGUARDANDO` a janela de 29/10 13:00Z). Lição do reinício: o `Ctrl+C` depois de "credenciais só na memória" encerra o agendador; para sair do tmux sem parar, `Ctrl+B` e `D`, ou `tmux detach-client -s agenda` de outro terminal. O lançamento por `tmux send-keys` seguido de `tmux attach` só para a senha funcionou.
- 2026-10-29: decisão do autor "Vale até o último quadro" (ponto aberto do passo 9): emenda (e) na seção 10 e `14eb165` (avaliador: `fim_da_sessao_ns` lê o manifesto, motivo `completo_ate_silencio_final`, relatório `pregoes_ate_silencio_final`; 10 testes). Testado primeiro no Python do Windows, porque a captura do dia estava em curso; a suíte inteira em clone limpo rodou às 17:20 BRT, com o agendador já em `AGUARDANDO`: verde, 1326 testes, 2 pulados. `identidade_codigo` = `43a1de51…7dcea` (38 arquivos). A outra sessão que trabalha na mesma pasta (arquivamento de capturas, em `operacao/windows/`) fica fora do pacote e não entra no hash.
- 2026-10-29: passo 10 da etapa 3 feito, autorizado pelo autor ("Sim, passo 10"). Conferência das emendas contra o código em `docs/provas/etapa3_20261029/conferencia_passo10.md`: 23 regras conferem, nenhuma mudança de código; 6 leituras do texto ficam abertas a veto do autor até o registro (D sem arredondar no sinal; saída pode cair na faixa do FOMC, sem efeito de 9 a 20/11; livro negociável exige os dois lados; saída contada da ordem de entrada; vale o intervalo de 98,33 % do item 7; quadro às 16h00 já conta). Suíte em clone limpo de `f15f4c5` verde (1326, 2 pulados); `qhos2/src` igual ao de `14eb165`. **Congelado: `identidade_codigo` = `43a1de514b22461b2008a3b978d3c101d265b64d3962ee3ef6ff07a94ee7dcea`** (38 arquivos, Python 3.12.3 CPython). Achado do passo: o hash depende de onde o código é lido. Da pasta OneDrive dá `550f572a…`, porque 33 dos 38 arquivos estão com CRLF; o pacote instalado (0.2.5) tem 8 arquivos com CRLF e 2 `.py` do `terminal` a mais. Daí a condição R4: registro e avaliação rodam de um clone limpo com `PYTHONPATH` e o Python do venv. Também registrado: o registro aceita até 8/11 às 20h59 de Brasília (data UTC), o alfa tem de ser exatamente 0,05/3, e uma captura com sessão com falha recusa a avaliação inteira (a lista de capturas segue a regra R5). A partir daqui nenhum `.py` de `qhos2/src/qhos2` muda até a avaliação.
- 2026-10-29 (noite): instalação da 0.2.6 e laço da vigia preparados, autorizados pelo autor ("Sim, preparar"); nada foi instalado. `operacao/wsl/instalar_release.sh` segue o caminho da 0.2.5 (wheel de clone limpo, `pip --no-deps --force-reinstall`, `releases/<commit>`, `RELEASE.json`) com duas travas novas: o `identidade_codigo` do clone tem de ser `43a1de51…` antes do wheel, e o dos arquivos instalados (sem `terminal/`) depois. `operacao/wsl/bin/vigia_laco.sh` espera cada captura, roda a vigia até o fechamento, parte no máximo 3 vezes por captura e faz o fechamento tardio se a captura encerrar sem ele. Roteiro em `docs/RUNBOOK_CAPTURA.md`. Ensaio fora da instalação viva (prefixo em `/tmp` partindo da 0.2.5 com o mesmo wheel e o mesmo `RELEASE.json` do vivo): instalação de `4955b60` com o hash do clone e o dos instalados iguais a `43a1de51…` (38 arquivos); a trava recusou `3131bb1`, que também é 0.2.6 mas tem o código do passo 5 (`0903a28f…`); o laço seguiu ao vivo uma captura sintética e a fechou sem achados e `igual_ao_vivo`; numa captura com linha estragada parou depois de 3 partidas e fez o fechamento tardio quando ela encerrou. O agendador não precisa ser reiniciado: captura, sessão, gravador, agenda e conector da 0.2.6 são idênticos aos da 0.2.5. O commit instalado decide o que entra além do pacote `qhos2`, porque o wheel também leva `brain` e `brain_service`: `eb0e3c8` é a 0.2.5 só com os módulos da etapa 3 (fora de `src/qhos2`, só a linha da versão muda); a ponta da `v2` traz também 37 arquivos do Brain da correcao-1. `operacao/SHA256SUMS.txt`: somas dos dois scripts acrescentadas e a do `README.md` refeita (já não batia na `v2` desde a edição do arquivador; as somas dos arquivos do arquivador ficam para o commit da instalação dele, como o README dele diz).
- 2026-10-29, 20h36 de Brasília: **0.2.6 instalada**, autorizada pelo autor ("Sim, hoje"), a partir de `eb0e3c8` (a 0.2.5 só com os módulos da etapa 3; Brain igual), com o agendador em `AGUARDANDO`. Scripts tirados do git e conferidos pelo `SHA256SUMS.txt` antes de rodar; o instalador conferiu `identidade_codigo` = `43a1de51…` no clone e nos 38 arquivos instalados; wheel `8f5539b8…`; `pip check` limpo; diagnóstico `ok`; `RELEASE.json` com `previous_commit` 57388f3 e o rollback em `releases/eb0e3c8…/previous_release.json`. Laço da vigia instalado em `~/qhos-v2/bin/vigia_laco.sh` (`729cff37…`, igual ao do git) e ligado no tmux `vigia`, esperando a captura de 30/10. Supervisor, Brain e agendador não foram reiniciados (o agendador segue com o PID de 28/10; os manifestos dizem `versao_qhos2` 0.2.5 até o próximo reinício dele). O heartbeat continua com o aviso "há código novo no git ainda não instalado", que é verdade: as correções do Brain da correcao-1 não foram instaladas. Depois de um reinício do WSL a vigia não volta sozinha.
- 2026-10-29, 21h08 de Brasília: Brain da correcao-1 instalado, a pedido do autor ("vamos entrar com o código do brain no git"): `instalar_release.sh` com a ponta da `v2` (`f2c753c`: o mesmo `qhos2/src/qhos2` congelado, `identidade_codigo` `43a1de51…` conferido no clone e nos instalados, mais 37 arquivos de `brain`/`brain_service` de 47 commits da correcao-1; `pyproject` e dependências iguais); wheel `c67fdd77…`; `RELEASE.json` com `previous_commit` `eb0e3c8`. O laço da vigia ficou parado só durante o `pip` e foi religado. Só o Brain foi reiniciado (rodava desde 27/10 com o código carregado então): voltou ativo, `GET /api/brain/authority` 200, sem erro no journal. Agendador, supervisor e coletor não foram tocados. O autor prepara em outra conversa a tarefa agendada que religa a vigia depois de reinício do WSL.
- 2026-10-29 (noite): roteiro do passo 11 preparado e ensaiado, a pedido do autor ("Sim, preparar"), sem olhar dado de calibração. `operacao/wsl/passo11.py`, em duas fases. `medir` (recusa antes de 6/11 às 18h20 de Brasília): seleciona as capturas por regra escrita (Rithmic Paper, ESZ6, CME, primeiro quadro na janela de calibração; as demais listadas com o motivo), calibra e mede o custo em paralelo, roda o checklist 1 com o teto; se seguir, conta os sinais, roda o checklist 2 e o ensaio sintético com os números reais e escreve `proposta.json` com o SHA-256; captura recusada ou checklist de abandono param tudo (achados AUD-234 e AUD-235 viram parada, não decisão do roteiro). `registrar`: só a proposta cujo SHA-256 o autor aprovou, sem recalcular nada, pelo `pesquisa.registrar_plano`. Ensaio em dado sintético (estados de `avaliador_sintetico.gerar_estados`, sete pregões, 18 s): antes do fim da calibração não lê nada; opções de ensaio fora do ensaio recusadas; só capturas que não são do Paper param sem captura; com o teto, abandono no checklist 1 sem proposta; captura recusada para; sem taxa, os dois checklists seguem e a proposta sai; registro com SHA errado recusado, com o certo em 7/11 registra, repetido devolve o mesmo plano, em 8/11 às 21h30 recusado (`registro_fora_da_janela_pre_teste`); a faixa do FOMC sai da calibração de 28/10. O mesmo ensaio virou `tests/test_passo11.py` (8 testes). Nenhum `.py` de `src/qhos2` mudou. No servidor ainda não existe registro de pesquisa: o de 7/11 será o primeiro dos 3 testes.
- 2026-10-30: pregão 3 de 5 da etapa 1 (captura `f6644149`) interrompido às 17:41:20Z por `conexao_perdida` (queda do cabo de rede de cerca de 5 s, relato do autor), com a parte gravada verificada e sem login recusado. Decisão do autor: conta como 3 de 5, embora o critério de saída peça pregões capturados inteiros; o ES ficou sem gravação do fim da queda até o fim da janela. Pregão 2 (29/10, captura `aeeeeea3`) registrado junto, capturado e verificado. Faltam 2/11 e 3/11 (janela 14:00Z a 21:15Z depois do fim do horário de verão nos EUA).
- 2026-11-03: pregão 4 de 5 da etapa 1 (2/11, captura `4c2c210e`) capturado e verificado, 13.364.678 quadros, 3,54 GiB, sem login recusado; medições na seção 7. Falta o pregão 5 (3/11, em captura).
- 2026-11-03: pregão 5 de 5 da etapa 1 (3/11, captura `c9d4c8e7`) capturado e verificado, 10.448.394 quadros, 2,76 GiB, sem login recusado; medições na seção 7. **Etapa 1 feita**: 5 de 5 (28/10, 29/10, 30/10, 2/11 e 3/11). O de 30/10 foi interrompido por queda do cabo de rede (~5 s) e conta por decisão do autor, embora o critério de saída peça pregões capturados inteiros.
