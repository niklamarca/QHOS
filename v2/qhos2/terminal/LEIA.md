# QHOS Terminal

Tela de mercado do usuário. A decisão de 26/10 preserva também o Brain e sua própria interface,
agora distribuídos com a V2. O terminal não substitui os motores ou a interface do Brain.

Instalação independente e caminhos atuais: [README da V2](../README.md). As referências abaixo à
árvore antiga registram a origem histórica do terminal, não requisitos atuais de execução.

## Origem

- Copiado em 25/10/2026 de `dashboard/_hybrid.html` do checkout `~/qhos-linux-runtime/src`, onde estava alterado e
  fora do git (modificado às 01:56 de 25/10). Lá ele é servido pelo processo principal do V1 em
  `http://127.0.0.1:8010/#terminal`. Aquele arquivo do V1 não foi tocado.
- `index.html` é o arquivo como foi servido, com as mudanças da integração aplicadas por patch conferido.
- Desde a tarefa 8 (25/10), quem serve a página é o coletor da V2, em `http://127.0.0.1:8766/`; o V1 não é mais
  necessário para usar o terminal.

## Como está organizado

- `index.html`: a fonte. Um arquivo só; os 16 módulos JavaScript estão em base64 dentro do `importmap`.
- `modulos/`: cópia legível dos módulos, gerada por `extrair_modulos.py`. Não edite à mão. O teste
  `tests/test_terminal.py` falha se ela não bater com o `index.html`. Mudança no código entra no `index.html`
  (por patch conferido) e depois a cópia é regerada.

## O que ele fala

- Fonte Coinbase: dados públicos, usada só como teste.
- Fonte Rithmic: só pelo coletor QHOS (`src/qhos2/coletor.py`), protocolo `qhos.dashboard.v1`
  (`modulos/qhos-bridge.js`). O terminal não fala o protocolo da Rithmic: o coletor lê a captura gravada pela V2.
  Uso: `PYTHONPATH=src python -m qhos2.coletor --em-andamento` (ou `--captura PASTA`), abrir
  `http://127.0.0.1:8766/` e conectar `ws://127.0.0.1:8766` com o token de `~/.config/qhos2/coletor.token`.
- Conexão automática (tarefa 12): o coletor responde `GET /conexao` com o endereço e o token, mas só ao `fetch` da
  própria página (cabeçalhos `Sec-Fetch-Site: same-origin`, `Sec-Fetch-Mode: cors` ou `same-origin` e
  `Sec-Fetch-Dest: empty`, que script de outro site não consegue alterar: W3C, Fetch Metadata Request Headers).
  Qualquer outro pedido (barra de endereços, outro site, script, imagem, iframe) recebe o mesmo 404 de um endereço
  inexistente, e a resposta não tem cabeçalhos CORS. O terminal não guarda o token em lugar nenhum: a cada abertura
  da página, ele o busca de novo. Isso não protege contra um programa que rode no seu próprio usuário, que já lê o
  arquivo do token.
- Endereço do coletor: `wss://`, ou `ws://` só em `127.0.0.1` com porta explícita (decisão de 25/10). Sem TLS no
  loopback porque o tráfego não sai da máquina; a proteção contra outro site aberto no navegador é a chave de acesso
  e a conferência de origem no coletor.
- Ordens e conta virtual funcionam só para Coinbase; para Rithmic ficam desligadas no próprio código.
