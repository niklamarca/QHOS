# Configuração (`qhos2.config.Settings`): o que cada campo faz e quem o lê

Registro de 28/10/2026 (QHOS-AUD-207), conferido contra o código em `36f0e1f` e protegido por
`tests/test_config_consumidores.py` (se um consumidor aparecer, o teste avisa para atualizar esta página).

## Como a configuração entra

- `Settings.from_environ()` (`config.py:105-113`) aceita **só duas** sobrescritas do ambiente: `QHOS2_DATA_ROOT` e
  `QHOS2_ENDPOINT`. Qualquer outro campo só muda por código (construtor), e o pacote não constrói `Settings` com
  argumentos fora de `config.py` e dos testes.
- Todo campo é validado em `__post_init__` (`config.py:82-102`) e exportado por `como_dict()` (`config.py:115-128`),
  que é a forma segura para log e manifesto.

## Campos e consumidores

| Campo | Padrão | Validação (`config.py`) | Quem lê fora de `config.py` | Efeito em vigor |
|---|---|---|---|---|
| `system_name` | `Rithmic Paper Trading` | só esse valor (`so_rithmic_paper_trading`) | `sessao.py:126, 175, 187`; `captura.py:59, 187` | Identidade do login e da captura; a sessão recusa resposta sem esse sistema |
| `app_name` | prefixo `nila:` | prefixo obrigatório (`app_name_precisa_do_prefixo_nila`) | `sessao.py:187`; `captura.py:187` | Vai no login |
| `endpoint` | `DEFAULT_ENDPOINT` | `validar_endpoint` | `captura.py:187, 212`; `agenda.py:331` | Para onde o conector abre o WSS |
| `heartbeat_seconds` | `HEARTBEAT_MAX_SECONDS` | 1 a 10 (`heartbeat_entre_1_e_10_segundos`) | `sessao.py:103, 191` | Período do heartbeat da sessão |
| `data_root` | `~/qhos-v2-data` (`data_root_padrao`) | `validar_data_root` (absoluto, fora de repositório git) | `captura.py:180`; `agenda.py:303, 312`; `diagnostico.py:18`; `operacao.py:348`; `catalogo.py:143`; `coletor.py:151`; `leitura.py:265`; `pesquisa.py:451`; `supervisao_coletor.py:247` | Raiz de capturas, catálogo, agenda, pesquisa e derivados (CLIs sem `--raiz` explícita) |
| `market_data_session_limit` | `2` | inteiro >= 2 (`limite_de_sessoes_minimo_2`) | **ninguém** | **Nenhum.** Fato da Rithmic registrado (2 sessões de dados somando plants, `PLANO_V2.md` seção 7); a exclusividade real é a guarda `GuardaCaptura` (`operacao.py`), por usuário Linux |
| `ticker_sessions` | `1` | 1 <= n <= limite − 1 (`ticker_sessions_deve_deixar_uma_vaga_livre`) | **ninguém** | **Nenhum.** A sessão abre uma conexão por captura; o número não é consultado |
| `reconnect_backoff_seconds` | `(5, 10, 20, 40, 60)` | tupla crescente de inteiros 1–300 (`backoff_invalido`) | **ninguém** | **Nenhum.** Não existe rotina de reconexão: login recusado, logout forçado ou queda encerram a captura e a guarda fica em quarentena (`OPERACAO.md`, "Captura e credenciais"; `agenda.py` docstring). O comentário em `config.py:77` ("Politica nossa...") descreve uma política declarada, não um ajuste em vigor |
| `credential_env_prefix` | `RITHMIC_PAPER` | `[A-Z][A-Z0-9_]*` (`prefixo_de_credencial_invalido`) | **ninguém** | **Nenhum.** Credenciais vêm só do prompt (`captura.obter_credenciais`, `getpass`); nenhuma variável `RITHMIC_PAPER_*` é lida em lugar algum do pacote, do Brain, do terminal ou dos scripts de operação |

## O que isso significa para quem opera

- Definir `RITHMIC_PAPER_USER`/`RITHMIC_PAPER_PASSWORD` no ambiente **não tem efeito**: a senha continua sendo
  digitada (regra 10 do plano).
- Mudar `reconnect_backoff_seconds`, `market_data_session_limit` ou `ticker_sessions` por código só altera a
  validação e o que aparece em `como_dict()`; nenhum comportamento de sessão muda.
- Os quatro campos continuam existindo porque removê-los mudaria a API de `Settings` e as chaves conferidas em
  `tests/test_config.py` (decisão fora da janela J0; `config.py` está congelado). A anotação proposta para o
  comentário de `config.py` está em `EVIDENCIAS/QHOS-AUD-207/patch_proposto.diff` da auditoria.
