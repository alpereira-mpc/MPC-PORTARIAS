# Agenda dos Procuradores

Módulo independente carregado somente pela rota Agenda. O Gerador de Portarias e a Home mantêm seu fluxo; a Home não consulta o banco para mostrar resumo.

## Persistência

`database/agenda.py` usa `Store.connection`, inclusive o pool e o bloqueio transacional já existentes no PostgreSQL. A inicialização aditiva ocorre no primeiro acesso à Agenda de cada sessão, com `CREATE TABLE IF NOT EXISTS`, sem alterar versões, sequências ou tabelas de Portarias.

- `agenda_compromissos`: UUID, tipo, início, término opcional, situação, criação/atualização e JSON textual com detalhes e confirmações.
- `agenda_compromisso_procuradores`: vínculo N:N com a tabela existente de procuradores; exclusão de compromisso remove seus vínculos por chave estrangeira.

Os nomes dos três membros são resolvidos uma única vez no catálogo existente (normalização de acentos/espaços). Os IDs resultantes são preservados em chaves `agenda_member_*` de `configuracoes`, sem sobrescrever vínculos existentes. Renomear posteriormente um membro mantém a regra. Se os nomes já estiverem diferentes na primeira instalação, conferir esses vínculos antes do uso.

## Disponibilidade

`services/agenda.py` centraliza catálogos e `RULES`. Cada regra contém identificação, dia da semana, atividade, tipos afetados e exigência de confirmação. Não cria sessões fictícias nem atribui horários às sessões. Para reuniões e despachos: Bradson na terça-feira (2ª Câmara), Elvira na quarta-feira (Tribunal Pleno) e Isabella na quinta-feira (1ª Câmara). Eventos não exigem confirmação institucional automática; Isabella não possui regra na terça-feira.

Alertas institucionais e conflitos têm confirmações independentes. O formulário recalcula ao alterar o agendamento; a gravação revalida dentro da transação, com dados atuais. Compromissos cancelados não conflitam. Evento sem horário ocupa as datas inclusivas; compromisso com início e sem término é comparado na precisão de um minuto, sem exibir uma duração presumida. Intervalos adjacentes não conflitam.

Leituras da agenda têm cache de 30 segundos e invalidação nas operações locais. O PostgreSQL usa as revisões do pool existente. A consulta reúne compromissos e participantes em lote, sem N+1. Mudanças de outras instâncias podem levar até 30 segundos para aparecer; a gravação sempre reconsulta.

## Validação manual

- Conferir visual em desktop e tela estreita, agrupamento mensal/semanal e eventos que atravessam datas.
- Conferir nomes e vínculos institucionais no banco real.
- Experimentar os campos Outro, editar/cancelar/excluir e filtros com dados representativos.
- Validar acesso no Streamlit Community Cloud após publicação pelo responsável; nenhum deployment foi alterado.

Os backups administrativos PostgreSQL existentes continuam com o escopo de Portarias. Para backup integral incluindo Agenda, usar backup do banco PostgreSQL/Supabase. O backup SQLite de arquivo inclui as tabelas da Agenda.

## Visualização Próximos

Lista compromissos com início a partir da data atual de São Paulo, incluindo todos os horários de hoje, sem limite final de data. Os filtros de procurador, tipo e situação são aplicados no SQL. Eventos iniciados antes de hoje não entram nesta visualização, mesmo que ainda estejam em andamento.

A paginação mostra 30 compromissos; a consulta busca no máximo 31 para identificar a próxima página, antes de juntar os participantes. A ordenação é início e ID (desempate estável). Não há consulta de contagem nem consultas individuais por participante. A data atual, os filtros e o deslocamento da página integram o cache existente. Mudanças de filtros, data atual ou gravações locais reiniciam a paginação. Não exibe campo de referência nem sessões institucionais automáticas.
