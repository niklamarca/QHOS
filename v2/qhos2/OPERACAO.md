# Operacao independente da V2

O coletor, o Brain e a captura usam o pacote V2 e `QHOS2_DATA_ROOT`. Os servicos
de exemplo em `deploy/` apontam para `~/qhos-v2/.venv` e `~/qhos-v2-data`.
Eles nao instalam, param ou reiniciam o V1. Instalar um pacote nao migra dados,
modelos de voz, memoria do Brain ou configuracoes existentes automaticamente.

## Captura e credenciais

`python -m qhos2.captura` continua exigindo `--online --agreements-accepted` e
credenciais digitadas. Antes de pedi-las, valida o destino e a guarda local.
Antes de conectar, readquire a guarda e registra a tentativa de forma duravel.
Senha e usuario nao sao persistidos, nem mesmo como hash da conta.

A guarda em `~/.local/state/qhos2/operacao` permite **uma captura V2 por usuario
Linux**, mesmo com outro simbolo, endpoint ou raiz de dados. E uma restricao
conservadora: nao conta sessoes de R|Trader, outros aplicativos, outros usuarios
Linux ou outras maquinas. A exclusividade nesses casos exige verificacao do
operador. Mudar `--raiz` ou `QHOS2_DATA_ROOT` nao muda a guarda.

Recusa, timeout, logout forcado, cancelamento abrupto ou processo morto deixam a
tentativa bloqueada. Logout confirmado e sessao fechada liberam uma proxima
partida manual. Nao existe servico de captura com reinicio automatico, senha em
arquivo ou rotina que reconecte por conta propria.

Para consultar e liberar depois de investigar a causa e encerrar sessoes
concorrentes:

```sh
python -m qhos2.operacao estado
python -m qhos2.operacao liberar --geracao ID_EXATO_MOSTRADO --confirmo-sessoes-encerradas
```

Liberar grava um recibo e nao inicia captura. Um estado corrompido e recusado;
nao e tratado como ausencia de quarentena. Nao apague lock/estado para tentar
desbloquear. A guarda e cooperativa e nao pretende resistir a alteracao deliberada
pelo proprio dono dos arquivos ou por root.

SIGINT e SIGTERM pedem cancelamento das assinaturas e logout; um segundo sinal
aborta. Cada captura recebe uma geracao de conexao distinta no manifesto. Nenhum
livro e reutilizado em uma nova conexao. Isso nao implementa ressincronizacao
periodica durante uma sessao: falhas exigem parada, investigacao e nova captura.

## Disco, copia e recuperacao

O gravador valida destino absoluto fora de Git, limites e espaco livre. Faz
flush a cada quadro, fsync a cada mil quadros e ao fechar. O manifesto somente e
publicado no fechamento. Uma falha de fsync nao vira sucesso numa segunda chamada.
Uma queda de energia ainda pode perder a cauda nao sincronizada; os dados parciais
devem permanecer identificados como incompletos, sem fabricar um manifesto aprovado.

```sh
python -m qhos2.operacao diagnosticar
python -m qhos2.operacao copiar /dados/capturas/ID /backup/ID
python -m qhos2.operacao copiar /backup/ID /restauracao/ID
```

A copia exige integridade de uma captura encerrada, destino novo fora de Git e
hashes iguais antes/depois. Copia frames, manifesto e o cabecalho de identidade
quando referenciado pelo manifesto; nao copia credenciais
nem executa conteudo. Backup e restore usam a mesma verificacao. Uma copia de
captura com sessao falha preserva esse estado; hash nao qualifica livro nem pesquisa.
Nao ha exclusao automatica de capturas, rotacao ou restauracao sobre dados existentes.

## Relogio e calendario

O heartbeat estima desvio sob hipotese de simetria da rede. A menor latencia so e
usada numa janela de 30 segundos; a amostra mais nova tambem aciona o alerta.
Amostras antigas e saltos do relogio local aparecem como inadequados. Isso nao e
certificacao UTC, PTP, sincronizacao NTP ou medida de latencia unidirecional exata.

`--calendario arquivo.json` aceita janelas explicitas UTC, com fonte e validade:

```json
{"schema":"qhos2_calendario_v1","fonte":"referencia conferida pelo operador",
 "valido_de":"2026-10-26T00:00:00Z","valido_ate":"2026-10-27T00:00:00Z",
 "janelas":[{"inicio":"2026-10-26T10:00:00Z","fim":"2026-10-26T11:00:00Z"}]}
```

As datas acima sao um exemplo de formato, nao um calendario de bolsa. O inicio e
o fim previstos da captura devem caber na janela. O prazo inclui descoberta,
login, retrato, transmissao e logout; excede-lo invalida o encerramento e mantem
quarentena. Sem esse arquivo, a captura e
manual e o diagnostico declara calendario nao validado. Feriados e manutencoes
nao sao adivinhados. Uma captura nao e iniciada automaticamente quando a janela abre.

## Brain e terminal

Antes de ativar as unidades de usuario, prepare as pastas de escrita exigidas
pelo isolamento do systemd. Os modelos abaixo usam os caminhos padrao:

```sh
install -d -m 700 "$HOME/qhos-v2-data" "$HOME/.config/qhos2"
install -d -m 700 "$HOME/.config/systemd/user" "$HOME/qhos-v2-data/brain"
install -m 600 operacao/wsl/systemd-user/service.env.example "$HOME/qhos-v2-data/brain/service.env"
install -m 644 deploy/qhos2-coletor.service operacao/wsl/systemd-user/qhos2-brain.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
```

A unit do Brain que vale para instalar e `operacao/wsl/systemd-user/qhos2-brain.service`: ela le
`~/qhos-v2-data/brain/service.env` (`EnvironmentFile=`, sem `-`, entao o Brain nao sobe sem esse arquivo). Ajuste no
`service.env` os caminhos `<usuario>` do modelo antes de iniciar. `deploy/qhos2-brain.service` fica como referencia
sem o `EnvironmentFile`; a diferenca entre as duas e uma linha so, conferida por `tests/test_deploy_eol.py`.

Esses comandos somente preparam a instalacao. Confira quem ocupa as portas 8766
e 8091 antes de iniciar as unidades. Uma raiz ou ambiente de instalacao diferente
exige ajustar Environment, ExecStart, WorkingDirectory e ReadWritePaths juntos.
Nao ative unidades que concorram com o Brain ou o terminal ainda em uso.

O Brain preserva memoria de incidentes, consulta de fontes, hipoteses operacionais,
testes, UI, chat opcional e cliente de voz. Seu alvo padrao e o pacote `qhos2`, e
seus arquivos mutaveis ficam em `<data_root>/brain`. A UI e incluida no pacote.

O servico do terminal usa `qhos2.supervisao_coletor`: observa metadados limitados,
seleciona a unica captura recente ou a encerrada mais nova e controla somente
seu proprio processo de coletor. Ambiguidade deixa o terminal ocioso. Trocas
reiniciam o leitor com outro estado; nao iniciam captura, login ou ordens.
O systemd encerra o grupo do supervisor e filho em uma parada. Morte abrupta
fora desse servico nao oferece a mesma garantia de limpeza do processo filho.

O contrato `GET /api/qhos2/snapshot` do coletor alimenta uma observacao especifica
da V2. Origem sintetica permanece sintetica. Antes de gravar quadros, a captura publica
`capture_identity.json` com apenas sistema/contrato/bolsa, id da captura e
identidade do arquivo. O supervisor exige esse vinculo para seguir uma captura
em curso; sem ele fica ocioso. O manifesto final inclui o hash desse cabecalho.
A origem declarada nao comprova login, direito de acesso ou conexao ao vivo. Paper nao e rebatizado como Rithmic Test; arquivo recente nao e conexao ao
fornecedor. O Brain mostra uma secao V2 na interface e mantem a observacao legada
separada. As rotas HTTP locais recusam redirects e respostas excessivas.

O perfil `qhos2-operational-v1` executa oito testes embarcados e pinados em copia
com guardas Python. O smoke faz dez verificacoes de sintaxe/API. Nenhuma dessas
baterias substitui a suite completa nem constitui sandbox do sistema operacional.
Mudanca no arquivo de testes exige nova revisao do hash; um teste alterado nao e
executado automaticamente como se continuasse aprovado.

A versao 0.2.1 corrige a inicializacao dessa bateria no pacote instalado: o
diretorio de dependencias tambem contem o Brain, mas retirar todo esse diretorio
impedia importar pytest. O worker conserva as dependencias e bloqueia a leitura
dos pacotes vivos da aplicacao. Continua usando `-S`, sem plugins automaticos,
site de usuario ou PYTHONPATH herdado. Os testes de instalacao verificam o perfil
real e as recusas de leitura, escrita externa, rede e subprocessos.

A versao 0.2.2 acrescenta a captura com teto padrao de 12 GiB e a checagem de
espaco antes das credenciais (ver docs/RUNBOOK_CAPTURA.md). E a primeira versao
construida a partir da pasta canonica da V2, sem os arquivos do V1 na raiz.

A versao 0.2.3 acrescenta o agendador diario `qhos2.agenda` (opcao A da etapa 1): a
senha e digitada uma vez e fica so na memoria do processo; cada janela do calendario
explicito vira uma captura; login recusado ou logout forcado encerram sem nova
tentativa. Ver docs/RUNBOOK_CAPTURA.md.

A versao 0.2.4 corrige a conta de espaco livre (`qhos2.disco`): no WSL o disco do
Linux e um arquivo .vhdx dentro do C: do Windows e o `df` mostra o tamanho maximo
do vhdx, nao o que ele ainda pode crescer. A checagem antes da captura, a checagem
do gravador durante a captura e os diagnosticos passam a usar o menor entre o livre
do Linux e o livre do disco do Windows (`QHOS2_DISCO_HOSPEDEIRO`, padrao `/mnt/c`).
Se o disco do Windows nao responder, vale o livre do Linux.

A versao 0.2.5 fecha a conta da etapa 2 (livro certo): ao fim do retrato o livro
(`qhos2.livro`) mede o que a regra errada de antes de 25/10 (aplicar na hora o que
chega durante o retrato) teria deixado, e devolve em `reaplicadas` quantas
atualizacoes guardadas foram aplicadas, quantas ordens fantasmas existiriam e
quantas ficariam defasadas. Nao muda o livro nem o bruto gravado; so conta.

A versao 0.2.6 e a base do avaliador para a etapa 3 (auditoria de 28/10, passos 1 a 5
da secao 4.2 do plano): a calibracao exclui a faixa do FOMC; recuo do relogio de
recebimento e preso e contado em vez de perder o dia; dia com lacuna de entrega
(> 60 s tocando o pregao) vale ate a lacuna, na calibracao e no teste; o orcamento
de 3 testes, alfa 5 %/3 e prazo 30/04/2027 e imposto pelo pre-registro; e o veredito
exige o bootstrap em blocos E o criterio por pregao (permutacao por inversao de
sinal). Nao toca a captura nem o bruto gravado. Instalada em 29/10/2026 as 20h36 de
Brasilia a partir do commit `eb0e3c8` (a 0.2.5 so com os modulos da etapa 3; Brain
igual), por `operacao/wsl/instalar_release.sh`, com o `identidade_codigo` congelado
(`43a1de51...`) conferido no clone e nos arquivos instalados. No mesmo dia, as
21h08, a pedido do autor, reinstalada a partir de `f2c753c` (o mesmo `src/qhos2`
mais o Brain da correcao-1) e o Brain reiniciado. Supervisor e agendador nao foram
reiniciados e seguem com o codigo que carregaram; o coletor que o supervisor abre a
cada captura e a vigia ja nascem com a 0.2.6.

A mesma 0.2.6 traz `qhos2.vigia` (passo 9 da etapa 3, o minimo da etapa 5): um
processo so de leitura que segue a captura em andamento com o mesmo Leitor do
coletor, em prioridade baixa, e registra num diario proprio
(`<data_root>/vigia/<captura>.jsonl`) silencio de mercado com a sessao viva
(> 60 s sem quadro novo e sem manifesto), a lacuna quando os quadros voltam,
atraso crescente (mediana por minuto de chegada menos carimbo do servidor subindo
tres minutos seguidos, meio segundo cada, acima de 1 s) e o instante em que o livro
deixa de valer. Quando o manifesto aparece, rele o livro do zero, compara com o
seguido ao vivo e escreve `<captura>.fechamento.json` com lacunas, atraso por
minuto, sessao e `achados`. Com `--captura` numa captura encerrada faz so o
fechamento (ver docs/RUNBOOK_CAPTURA.md). Nao conecta, nao controla processo e
nao escreve na captura. Desde 29/10 roda todo dia pelo laco
`~/qhos-v2/bin/vigia_laco.sh`, no tmux `vigia` (copia em
`operacao/wsl/bin/vigia_laco.sh`); depois de o WSL reiniciar, ligar de novo com
`tmux new -d -s vigia ~/qhos-v2/bin/vigia_laco.sh`.

O relight antigo baseado em `QHOS_RESTART.bat` e recusado pela configuracao V2 e pelo executor padrao do supervisor.
Rotinas de reparo continuam preparando copias para revisao, sem aplicar patches.
Chat/modelos externos e voz exigem configuracao propria e nao sao usados pelos
testes desta migracao. Os pesos XTTS e sua referencia de voz nao estao no Git nem
no pacote; a sintese real requer esses recursos e dependencias opcionais.

O journal do Brain coleta somente unidades `qhos2-*`, com retencao de cem mil
registros. O registro de investigacoes automaticas conserva as cem mais recentes.
Limites de retenção nao comprovam disponibilidade continua: queda real de maquina,
feriados, acesso Rithmic e operacao prolongada permanecem ensaios de ambiente.

Capturas encerradas novas preservam o cabecalho no backup. O hash e os campos do
manifesto sao conferidos; o inode original nao e exigido na restauracao, pois
muda em outro arquivo/sistema de arquivos. Capturas antigas sem referencia ao
cabecalho continuam aceitas somente no caminho de capturas encerradas.

Se capturas diferentes possuem negocios no mesmo minuto, o historico recusa a
uniao dos OHLCVs: nao ha identidade suficiente para deduplicar ou somar volumes.
O terminal pode exibir somente o recorte da captura que o coletor esta seguindo,
com `coverage.kind=selected_capture_only`, motivo `overlapping_captures`,
`periodComplete=false` e `partial=true` em cada candle. A tela avisa que o
historico e parcial; isso tambem vale para 1D e nao comprova cobertura do dia
inteiro. Sem captura seguida valida ou sem negocios visiveis nesse recorte,
a resposta e `HISTORY_OVERLAP`, sem candles inventados. A API estrita de
agregacao continua recusando a sobreposicao; nenhum arquivo de captura e alterado.
