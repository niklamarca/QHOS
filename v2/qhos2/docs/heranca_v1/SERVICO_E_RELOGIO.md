Gerado em 2026-10-25 a partir dos documentos do V1; revisado em 25/10/2026 (ver LEIA_PRIMEIRO.md); sem dados pessoais.

Datas das fontes: modificação local da cópia lida, em São Paulo. Os registros são históricos; não comprovam o estado atual.
Identificadores pessoais e componentes pessoais de caminhos citados nas fontes: [removido].

- Separar captura, reinício do processo, religador e sentinela. No desenho descrito, a sentinela faz zero logins e o
  religador é acionado por OnSuccess. — fonte: T3_RISCO_REINICIO_EM_LACO.md, 2026-10-13.

- Aumentar a espera entre reinícios não limita as tentativas: o retrato registra StartLimitIntervalSec=0 e esperas de 60
  s a 1800 s. O bloqueio de login precisa de regra própria. — fonte: T3_RISCO_REINICIO_EM_LACO.md, 2026-10-13.

- Distinguir alarmes: LACO_DE_FALHA usa NRestarts ≥ 2; RECONEXOES_EM_RAJADA sinaliza pelo menos três falhas de época em
  15 minutos. Reinício do processo e reconexão interna não são a mesma contagem. — fonte: T3_RISCO_REINICIO_EM_LACO.md,
  2026-10-13.

- No caso documentado para systemd 255, ExecCondition pulado termina com sucesso e aciona OnSuccess; o religador agenda
  outra partida e cria laço local, mesmo sem login. A reprodução foi em cópia. — fonte: LEIAME_P9C.md, 2026-10-21.

- O P9c em cópia verifica a quarentena também no religador: registro presente impede nova partida e novo timer; ambiente
  desconhecido não religa. — fonte: LEIAME_P9C.md, 2026-10-21.

- Os 9/9 casos e 4/4 mutantes do P9c usaram comandos simulados; systemd 255 real, ext4, religador real e timers já
  pendentes ficaram não verificados. Correção em cópia não prova instalação. — fonte: LEIAME_P9C.md, 2026-10-21.

- Lacuna: as fontes lidas não especificam INVOCATION_ID nem o defeito de reinício no mesmo segundo. Não inventar
  identidade de execução ou regra de deduplicação para preencher essa ausência. — fonte: RELATO_OVERNIGHT_ETAPA9.md,
  2026-10-21.

- No portão 4 v1.2, contar rodadas efetivas por UTC, auditar duplicatas e invalidar conflitos. A tolerância de ±120 s é
  da borda da janela; não é tolerância de precisão do relógio. — fonte: RELATO_OVERNIGHT_ETAPA9.md, 2026-10-21.

- A cobertura histórica exigia pelo menos 6 h no RTH de 08:30–15:15 CT, janela de 6,75 h; interrupção superior a cerca
  de 45 minutos invalidava a sessão. — fonte: RELATO_OVERNIGHT_ETAPA9.md, 2026-10-21.

- No pré-voo P03 descrito, última sincronia ≤15 minutos e correção ≤2 s; o relato registra NetTime a cada cerca de 5
  minutos, com correções da ordem de ms. Isso não é medição atual. — fonte: RELATO_OVERNIGHT_ETAPA5.md, 2026-10-21.

- Lacuna: não foram localizados limites físicos em ms/ppm nem uma série de desvios medidos do portão 4 da E9. Não
  substituir essa evidência pela tolerância da janela ou pelos critérios P03. — fonte: RELATO_OVERNIGHT_ETAPA9.md,
  2026-10-21.

- Quietude operacional é outra verificação: o retrato ANTES do P03 exige run_s ≥1800 e folga ≥5400 s; passar nesses
  limites não mede desvio do relógio. — fonte: RUNBOOK_P03_v4_20261020.md, 2026-10-21.

- Reservar a janela inteira de T−45 minutos a FIM+40 minutos e exigir ensaio a seco com menos de 24 h no pré-voo. As
  datas históricas não autorizam uma nova execução. — fonte: RUNBOOK_P03_v4_20261020.md, 2026-10-21.

- Dimensionar disco antes de habilitar profundidade: a estimativa era cerca de 12 GB/dia, frente ao teto de cerca de 8
  GiB/dia, composto por 2048 MiB por corrida de 6 h. — fonte: PENDENCIAS_AUTOR.md, 2026-10-21.

- O pré-voo histórico P03 exigia pelo menos 2 GB livres; isso não dimensiona uma captura contínua de profundidade nem
  substitui o orçamento diário. — fonte: RELATO_OVERNIGHT_ETAPA5.md, 2026-10-21.

- WSL inacessível não prova host desligado. As fontes não demonstram monitoramento funcionando com o host desligado;
  preservar essa diferença de estados. — fonte: RELATO_OVERNIGHT_ETAPA9.md, 2026-10-21.

- Silêncio de avisos por mais de 7 h, ou 6 h e meia no ensaio, indica possível parada da máquina ou do envio; não
  identifica sozinho a causa. — fonte: PAINEL_NO_DIA_DO_ENSAIO.md, 2026-10-20.
