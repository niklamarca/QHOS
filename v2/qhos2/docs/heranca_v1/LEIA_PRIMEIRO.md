# Herança do V1: leia primeiro

Revisado em 25/10/2026. Os cinco arquivos desta pasta foram escritos a partir de 11 documentos do V1 (relatos
das etapas, runbook do P03, estudo do reinício em laço, LEIAME do P9c, condições C1, painel do ensaio, minuta das 7
frases, pendências). Cada item cita a fonte e a data. Nenhum `.eml`, `.env` ou cofre foi aberto. Os originais ficam
fora do GitHub (pen drive).

Todos os fatos sobre a Rithmic são RELATO: nenhum documento lido prova a conferência do e-mail original. Eles valem
como hipótese de trabalho até a resposta escrita da Rithmic chegar.

## Correções e complementos da revisão

1. **Manutenção e limites (FATOS_RITHMIC, LOGIN_E_QUARENTENA):** a minuta das 7 frases é um pedido de
   confirmação, não uma resposta. Mas a conversa do coordenador do V1 registrou, em 21/10, uma resposta do suporte da
   API que confirma em substância as frases 1, 2, 4, 5, 6 e 7 (2 sessões de dados no total, 5 de ordens, MBO no
   grupo CME, logins separados, manutenção das 17h20 às 17h45 ET, domingo por volta das 12h ET). A frase 3 (sessões
   de PnL contam no limite de ordens) ficou sem resposta. Estado: relato de conversa, original não conferido.
2. **Templates 75 e 77:** conferidos nos `.proto` oficiais (`vendor/rithmic/0.89.0.0`): 75 é `Reject` e 77 é
   `ForcedLogout`. A V2 já trata os dois como erro sem nova tentativa (`sessao.py`).
3. **Ordem da assinatura (LIVRO_E_RETRATO):** a V2 já segue a sequência descrita: assina as atualizações (117/118)
   antes de pedir o retrato (115/116). Por isso a regra de guardar as atualizações que chegam durante o retrato vale
   para ela (etapa 2 do plano).
4. **INVOCATION_ID e reinício no mesmo segundo (SERVICO_E_RELOGIO):** não estão nos documentos lidos, mas aparecem
   nas conversas da etapa 9 do V1 (defeito E9V-12: `systemctl restart` que para e liga no mesmo segundo faz o ciclo
   seguinte parecer parada manual, e a captura morre em silêncio; correção proposta: identificar cada execução pelo
   `INVOCATION_ID`). Estado: relato de conversa.
5. **Condições C1 (LIVRO_E_RETRATO, A1 a A12):** são o processo de autorização do V1 (guarda, ZIP selado, geração 3
   do cofre, captura publicada no Test). Não se aplicam à V2. Ficam como registro histórico; o que serve é a regra de
   fundo: uma autorização vale uma corrida, e qualquer dúvida torna a condição falsa.

## O que cada arquivo muda na V2

| Arquivo | Onde entra | O que a V2 precisa fazer |
|---|---|---|
| LOGIN_E_QUARENTENA | Etapas 1 e 5 | Login não confirmado (template 10 enviado sem 11 com rp_code 0, ou silêncio depois do envio) = quarentena, nunca nova tentativa. Contar logins realmente enviados, inclusive reinícios. Quarentena por ambiente, gravada em arquivo (sem segredo), que sobrevive a reinícios e bloqueia a partida se existir ou estiver ilegível. Liberação só manual. Hoje a V2 cumpre isso porque cada captura é iniciada à mão e não repete login. |
| SERVICO_E_RELOGIO | Etapa 5 | Não confiar em espera entre reinícios (`StartLimitIntervalSec=0` não limita nada). No systemd 255, `ExecCondition` pulado dispara `OnSuccess`: não usar religador por `OnSuccess`. Códigos de saída do aplicativo (`RestartPreventExitStatus`) separados dos códigos do protocolo. Portão de relógio antes de começar, além da medição por heartbeat que a V2 já faz. Medir o disco real por pregão (o V1 estimava ~12 GB/dia para captura contínua; a V2 mede a partir de 26/10). |
| LIVRO_E_RETRATO | Etapa 2 | Guardar as atualizações recebidas durante o retrato e aplicá-las depois do fim, na ordem; limites de tempo e de memória para o retrato; recusar e recomeçar em vez de descartar em silêncio; geração por conexão; ressincronizar com retrato novo. Os documentos do V1 não especificam esses limites: a V2 define os seus e testa. |
| TELAS_E_ESTADOS | Etapa 6 (C1) | Nada vira verde por falta de informação. Paper, Test e SEM SESSÃO distintos. Idade do dado (≤300 s) separada da idade da publicação (≤60 s). Heartbeat ausente, com mais de 20 min ou sem UTC é alerta. Sessões contra o limite. Relógio com degrau aberto ou fechado. NAV sempre marcado como simulado. |
| FATOS_RITHMIC | Etapa 1 | Perguntas abertas que o e-mail das 4 confirmações e o de taxas devem fechar. O limite e a duração de um bloqueio por tentativas de login são desconhecidos: não existe margem para repetir. |

## Lacunas que a herança revelou na V2

1. **"Pregão completo" não está definido** no item 8 da hipótese ("no mínimo 7 pregões completos"). O V1 exigia
   cobertura de pelo menos 6 h do pregão e invalidava o dia com interrupção maior que ~45 min. A definição da V2
   precisa ser registrada antes de 9/11.
2. **A V2 não tem portão de relógio antes de iniciar a captura;** só mede o desvio durante a sessão.
3. **A V2 não sabe o tamanho real de um pregão em disco;** a primeira captura de 26/10 responde.
