# Afastamentos na Agenda

Os afastamentos de Procuradores são registros próprios em `agenda_afastamentos`; não são compromissos. Eles aparecem em Hoje, Semana, Mês e Próximos quando o período intersecta a visualização e são identificados visualmente como **AFASTAMENTO**.

O Procurador-Geral deve ser substituído por um Subprocurador-Geral. Um Subprocurador-Geral deve ser substituído por um Procurador que não seja PG nem Sub-PG. Para Procurador comum, substituto é opcional. O próprio afastado e quem já esteja afastado em período coincidente não podem ser selecionados.

O cadastro bloqueia períodos sobrepostos do mesmo titular. Uma indicação já usada como substituto em outro afastamento é permitida nesta versão; a modelagem mantém fontes separadas para futura integração com alertas e pendências.

O status é calculado no fuso `America/Recife`: AGENDADO, EM ANDAMENTO, ENCERRADO ou CANCELADO. Registros podem ser editados e cancelados; o cancelamento é lógico e preserva o histórico. Eventos de auditoria: `AFASTAMENTO_CRIADO`, `AFASTAMENTO_EDITADO` e `AFASTAMENTO_CANCELADO`.
