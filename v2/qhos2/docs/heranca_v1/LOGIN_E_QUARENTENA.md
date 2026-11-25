Gerado em 2026-10-25 a partir dos documentos do V1; revisado em 25/10/2026 (ver LEIA_PRIMEIRO.md); sem dados pessoais.

Datas das fontes: modificação local da cópia lida, em São Paulo. Os registros são históricos; não comprovam o estado atual.
Identificadores pessoais e componentes pessoais de caminhos citados nas fontes: [removido].

- Uma autorização B1 permite uma tentativa; recusa, aborto ou condição falsa encerra o fluxo. Nova tentativa exige
  autorização nova. — fonte: RUNBOOK_P03_v4_20261020.md, 2026-10-21.

- No P03, o canal autorizado é somente ticker: um login, nenhum nas ordens e nenhuma operação de conta. “Uma tentativa
  por canal” não autoriza acrescentar outros canais. — fonte: RUNBOOK_P03_v4_20261020.md, 2026-10-21.

- Antes do P03, exigir R|Trader Pro fechado e nenhuma outra sessão Paper. Capacidade disponível no limite de sessões não
  concede autorização para tentar. — fonte: RUNBOOK_P03_v4_20261020.md, 2026-10-21.

- Contar mensagens de login realmente enviadas, inclusive retentativas internas e novas partidas do supervisor; a
  intenção de enviar só uma não basta. — fonte: RELATO_OVERNIGHT_ETAPA5.md, 2026-10-21.

- Login não confirmado é envio do template 10 sem receber template 11 com rp_code igual a 0. A regra proposta limita a
  um caso por ambiente até intervenção humana, somando reinícios. — fonte: T3_RISCO_REINICIO_EM_LACO.md, 2026-10-13.

- Distinguir conexão recusada antes do login de silêncio ou queda após o envio. O segundo caso é incerto e não deve
  gerar nova tentativa automática. — fonte: T3_RISCO_REINICIO_EM_LACO.md, 2026-10-13.

- Na bateria, recusas 13, 3 e 1039 encerram a tentativa; 1077 no template 17 é descoberta inválida com zero logins. São
  casos de teste, não prova de recusa numa sessão real. — fonte: RELATO_OVERNIGHT_ETAPA5.md, 2026-10-21.

- Não limitar a quarentena à recusa do template 11: o estudo identifica lacunas para template 75, resposta inesperada e
  silêncio após envio. O resultado incerto também precisa bloquear repetição. — fonte: T3_RISCO_REINICIO_EM_LACO.md,
  2026-10-13.

- Manter quarentena separada por ambiente e persistente entre partidas; registro presente ou ilegível bloqueia a
  partida. Não trocar Test por Paper como atalho para repetir. — fonte: T3_RISCO_REINICIO_EM_LACO.md, 2026-10-13.

- Coordenar aplicativo e supervisor: a proposta combina RestartPreventExitStatus=3 4 com ExecCondition. Esses códigos de
  saída não são os códigos de resposta do protocolo. — fonte: T3_RISCO_REINICIO_EM_LACO.md, 2026-10-13.

- Preservar o registro de quarentena e liberar somente após corrigir a causa e obter decisão explícita; nunca apagar o
  impedimento apenas para tentar de novo. — fonte: PENDENCIAS_AUTOR.md, 2026-10-21.

- Manutenção ou indisponibilidade após envio não justificam repetir login incerto; a proposta trata esse estado como
  quarentena. — fonte: T3_RISCO_REINICIO_EM_LACO.md, 2026-10-13.

- Os horários 17:20–17:45 ET e retorno perto de 12:00 ET no domingo estão numa solicitação de confirmação. Não usá-los
  como calendário confirmado sem resposta correspondente. — fonte: E4_ENVIE_ISTO_RITHMIC_7_FRASES_20261020.md,
  2026-10-20.

- Separar login aceito de dados entregues: login_veredito=PASS com veredito=FAIL não aprova C1 nem autoriza repetir
  automaticamente. — fonte: RUNBOOK_P03_v4_20261020.md, 2026-10-21.
