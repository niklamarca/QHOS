Gerado em 2026-10-25 a partir dos documentos do V1; revisado em 25/10/2026 (ver LEIA_PRIMEIRO.md); sem dados pessoais.

Datas das fontes: modificação local da cópia lida, em São Paulo. Os registros são históricos; não comprovam o estado atual.
Identificadores pessoais e componentes pessoais de caminhos citados nas fontes: [removido].

- Distinguir ambiente desconhecido, ausência de captura e dados antigos; nenhum desses estados pode virar verde por
  falta de informação. — fonte: RELATO_OVERNIGHT_ETAPA8.md, 2026-10-20.

- Separar Paper, Test e SEM SESSÃO, mostrando conexão e assinatura. Test não satisfaz um painel esperado para Paper; a
  v1.5 documentada acrescentava alerta que a v1.4 não tinha. — fonte: PAINEL_NO_DIA_DO_ENSAIO.md, 2026-10-20.

- Paper verde exige sessão corrente conectada, publicação com idade ≤60 s e concordância entre system_name e environment
  no sistema Paper. Histórico não pode aparecer como conexão atual. — fonte: PAINEL_NO_DIA_DO_ENSAIO.md, 2026-10-20.

- Mostrar idade do dado e idade da publicação separadamente; a regra documental de gravação verde exige,
  respectivamente, ≤300 s e ≤60 s. Publicação nova não rejuvenesce dado velho. — fonte: PAINEL_NO_DIA_DO_ENSAIO.md,
  2026-10-20.

- Heartbeat ausente, com mais de 20 minutos ou sem UTC gera alerta; verde exige estado ok, recência e ausência de
  reinício novo. — fonte: PAINEL_NO_DIA_DO_ENSAIO.md, 2026-10-20.

- Mostrar sessões de dados contra 2, ordens contra 5 e template 77 conforme o plano histórico; ultrapassar o plano manda
  abortar. Esses números na tela não substituem confirmação contratual. — fonte: PAINEL_NO_DIA_DO_ENSAIO.md, 2026-10-20.

- Relógio deve expor offset, degrau aberto/fechado e contagem de degraus; verde depende do orçamento e de nenhum degrau
  aberto. A tela não pode presumir precisão sem a evidência da E9. — fonte: PAINEL_NO_DIA_DO_ENSAIO.md, 2026-10-20.

- Identificar NAV e caixa como simulados, sem apresentá-los como dinheiro real ou ordens enviadas à Rithmic. Os juízos
  do dia só contam no fechamento marcado por fim-do-dia. — fonte: PAINEL_NO_DIA_DO_ENSAIO.md, 2026-10-20.

- Não combinar critérios entre versões: v2.5 usava gravidade persistente por 3 minutos; v2.6 propunha outra regra e
  ainda aguardava veredito naquela fonte. — fonte: RELATO_OVERNIGHT_ETAPA8.md, 2026-10-20.

- Resumo previsto a cada 6 h; silêncio superior a 7 h, ou 6 h e meia no ensaio, indica possível parada da supervisão ou
  envio. Sem mensagem não significa operação saudável. — fonte: PAINEL_NO_DIA_DO_ENSAIO.md, 2026-10-20.

- Não inventar um indicador de quarentena: o painel descrito não tem bloco próprio; a possibilidade de HEARTBEAT_ESTADO
  depende da saída da E9 e seu formato exato ficou não verificado. — fonte: PAINEL_NO_DIA_DO_ENSAIO.md, 2026-10-20.
