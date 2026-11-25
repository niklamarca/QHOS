# QHOS V2

Captura e pesquisa locais com terminal e Brain incluidos no mesmo pacote. O codigo
nao precisa da instalacao V1. Dados, memoria, registros de pesquisa e credenciais
nao fazem parte do repositorio ou do pacote distribuido.

## Instalacao independente (Linux / WSL)

A partir desta pasta `qhos2`, execute:

```sh
bash deploy/install.sh "$HOME/qhos-v2"
"$HOME/qhos-v2/.venv/bin/python" -m qhos2.diagnostico
```

O destino precisa ser novo. O instalador cria um ambiente proprio, instala o pacote
com o terminal e o Brain e verifica as dependencias. Nao inicia servicos, captura,
modelos pagos ou ordens. O Python de sistema continua sendo necessario; o ambiente
Python do V1 nao e utilizado.

A raiz padrao dos dados e `~/qhos-v2-data`. Para outra raiz, configure
`QHOS2_DATA_ROOT` com caminho absoluto fora de qualquer repositorio Git. Todas as
ferramentas devem receber a mesma raiz. As capturas destinadas ao terminal devem
ficar em `<raiz>/capturas/<id>`, e os derivados em `<raiz>/derivados`.

## Terminal e Brain

```sh
"$HOME/qhos-v2/.venv/bin/python" -m qhos2.supervisao_coletor
"$HOME/qhos-v2/.venv/bin/python" -m brain_service
```

O terminal fica em `http://127.0.0.1:8766`; o Brain, em
`http://127.0.0.1:8091`. Execute em terminais separados, ou instale as unidades de
usuario documentadas em [OPERACAO.md](OPERACAO.md). Nao substitua unidades antigas
sem identificar os processos e liberar as portas correspondentes.

O supervisor acompanha a selecao de captura e reinicia apenas seu proprio leitor.
Ele nunca inicia uma captura nem faz login. Sem uma captura inequivoca o terminal
fica disponivel com estado desconhecido. Para selecionar manualmente:

```sh
"$HOME/qhos-v2/.venv/bin/python" -m qhos2.coletor --captura "$HOME/qhos-v2-data/capturas/ID"
```

O Brain consulta o contrato `GET /api/qhos2/snapshot` do coletor. Preserva sua UI,
memoria, investigacao, Discovery, FABLE e diagnosticos locais. O supervisor antigo
que reiniciava o V1 fica bloqueado. Bibliotecas/canais opcionais de voz, Discord,
Codex e modelos pagos precisam de configuracao propria; nao sao necessarios para
o Brain e o terminal funcionarem localmente. Nao foram ativados pela migracao.

## Captura e dados

`python -m qhos2.captura --help` descreve a captura manual. Sem as autorizacoes
explicitas de conexao a ferramenta so mostra o plano. Usuario/senha sao digitados,
nao persistidos. A guarda em `~/.local/state/qhos2/operacao` impede duas capturas
V2 do mesmo usuario e conserva a quarentena apos encerramento incerto. Ela nao
controla outros usuarios, computadores ou aplicativos da corretora.

Use `python -m qhos2.operacao --help` para consulta, liberacao manual, copia e
restauracao verificadas. Copias nao sobrescrevem o destino. Integridade de bytes
nao significa livro valido; retrato recusado, contrato divergente ou sessao
fracassada sao recusados pela leitura e pela pesquisa.

O ambiente declarado na captura distingue Paper, Test, sintetico e desconhecido.
Sem metadados de origem o sistema conserva desconhecido. Um arquivo recente nao
comprova conexao da corretora, negociacao real ou atraso de rede corretamente
medido. O contrato de observacao nunca autoriza ordens.

## Pesquisa e heranca

O registro de pesquisa vincula hipotese, parametros, codigo, janela e evidencias;
resultados e reproducoes ficam registrados. Textos preservam origem e hash, mas
nao ganham veracidade automatica por terem sido armazenados. Os datasets separam
atributos conhecidos na decisao do alvo futuro e usam particao temporal.

`qhos2.microestrutura` fornece medidas observacionais de livro/fluxo;
`qhos2.hipoteses`, modelos quantitativos de referencia; `qhos2.laboratorio`,
simulacao L1 e conciliacao offline. As premissas e limites ficam nos respectivos
modulos e em [HERANCA_PESQUISA.md](HERANCA_PESQUISA.md).

Nao ha modelo de trading treinado e aprovado, retorno economico demonstrado,
motor de ordens ou computador/algoritmo quantico implementado nesta heranca.
Computacao quantitativa e computacao quantica sao temas diferentes. A preparacao
de dados para ML nao equivale a treinamento ou validacao de um modelo.

## Verificacao

```sh
python -m pip install pytest cryptography
python -m pytest
python terminal/extrair_modulos.py --conferir
```

Use um HOME e uma raiz de dados temporarios para os testes. Node e necessario para
os testes JavaScript; cryptography fornece certificados locais do ensaio TLS.
Nenhum teste deve usar credenciais reais. A independencia do pacote e testada com
instalacao limpa, captura sintetica, HTTP do terminal e observacao real pelo Brain.
Disponibilidade prolongada, login real, relogio UTC e resultado financeiro exigem
evidencias operacionais separadas; a suite nao os certifica.

O plano historico permanece em `docs/PLANO_V2.md`; a decisao de 26/10/2026 passou a
incluir explicitamente a preservacao do Brain junto do terminal V2.
