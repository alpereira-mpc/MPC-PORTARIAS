# Auditoria de desempenho V2 — 17/09/2026

## 1. Escopo, estado inicial e limites da evidência

Auditoria estática do código atual: portal/OIDC, usuários, Home, sidebar, Portarias,
Memorandos, Ofícios, Agenda, afastamentos, viagens por compromisso/procurador,
Tarefas, Pendências, Alertas, Administração e Tramita. Foram inspecionados também
`docs/AUDITORIA_DESEMPENHO_2026.md`, `scripts/profile_performance.py`, o adaptador
PostgreSQL, pool, caches, migrations, consultas e testes existentes.

O `git status --short` inicial estava limpo. A logística aérea já fazia parte da
base desta auditoria. A auditoria V1 foi preservada. Não houve acesso ao banco de
produção, execução de migrations em dados reais, commit, push ou instalação.

Não há `.venv` pronta; `py` não foi localizado e `python.exe` encontrado é o alias
de WindowsApps. Não foi iniciado esse alias, instalado Python ou configurado
ambiente. Testes e profiler foram escritos/atualizados, **não executados**.
Contagens abaixo são derivadas do código; não são resultados de benchmark.

A percepção inicial 5–6/10 foi informada pelo usuário. Não é possível certificar
9/10, latência subsegundo ou ausência de regressões só por análise estática.
Estimativa técnica pós-alterações: **8,5/10**, com fundamentos para buscar 9/10
nos fluxos quentes após validação em Cloud/Supabase. As medições SQLite da V1 não
devem ser reapresentadas como resultados desta versão.

## 2. Arquitetura e caminho de TODO rerun

1. `app.py` chama `portal.render_portal()` antes de importar geradores DOCX/PDF.
2. Configuração da página e leitura da identidade OIDC do Streamlit. Sem login,
   renderiza a tela de acesso e interrompe antes de criar Store.
3. `_application_store()` reaproveita `_mpc_store` na sessão. Não cria uma conexão
   viva na sessão: o Store é um objeto de configuração/acesso ao pool.
4. `current_user()` usa cache de sessão por 20 s; no miss consulta usuário e
   gabinetes. Agora a identidade do Store também participa da chave. Revogação
   continua limitada pelo TTL existente; não foi ampliada.
5. `iniciar_sessao_autorizada()` só registra os eventos da primeira autorização.
   `has_permission()` e a lista de módulos operam sobre o Principal em memória.
6. Navegação/deep links são consumidos antes dos widgets. A sidebar desenha marca,
   identificação, logout, sino e menu. Marca usa assets locais já cacheados.
7. Sino: cache de sessão de 20 s + `alerts_revision`; miss executa coletores.
   Agora também verifica o Principal completo e o Store, evitando reutilizar
   resultado de outra conta ou de permissões anteriores.
8. Home só desenha cards permitidos; não consulta dados de Relatórios, Tarefas ou
   Portarias para os cards. O custo de dados da Home é o caminho comum do sino.
9. Ao entrar em outro módulo registra auditoria da mudança, uma vez por mudança;
   depois importa e executa somente a rota selecionada. `st.stop()` mantém os
   imports e o código de Portarias fora das demais rotas.

### Bootstrap e migrations

`Store.initialize()` já tem `_INITIALIZED`, PostgreSQL tem `Resource.initialized`
e lock, e Access/Memorandos/Tarefas têm seus próprios guards. Não existia evidência
de execução de todas as migrations em TODO rerun do portal quente. SQLite ainda
verifica `user_version` ao criar outro Store conhecido; mantido para detectar
substituição de arquivo. O portal normalmente reaproveita o Store.

Exceção confirmada: `TramitaReportsStore.ensure_schema()` fazia 3 CREATE TABLE,
2 CREATE INDEX e BEGIN em **toda instanciação**, inclusive filtros da tela.
Agora inicializa uma vez por Store após commit bem-sucedido, com lock para chamadas
concorrentes. Uma falha não marca pronto; uma nova instância de Store faz a
verificação. `Store.migrate()` limpa esse marcador. A primeira inicialização
continua aditiva/idempotente e inclui os dois índices novos.

`InstitutionalFunctions.initialize()` ainda faz DDL e verificações de seed na
tela administrativa; não integra Home/sidebar. Otimização local recomendada
numa próxima rodada, com cuidado para preservar o seed de titulares ausentes.

## 3. Achados priorizados e correções

| Prioridade | Evidência no código inicial | Correção e impacto esperado |
| --- | --- | --- |
| P0 potencial em importações grandes | `Connection.executemany()` chamava `execute()` por linha; INSERT em tabela identity acrescentava RETURNING; a transação retinha o lock de escrita | Lote nativo psycopg com cursor fechado, mesma transação e revisão apenas após commit. Elimina as esperas sequenciais impostas pelo loop Python; não equivale a dizer que N INSERTs viraram uma instrução SQL. |
| P1 | DDL Tramita em toda criação de repositório | Guard após commit elimina 5 DDL + BEGIN/checkout de escrita por rerun quente de Relatórios. |
| P1 | Relatórios carregava toda competência/snapshot e repetia filtros, grupos e tabelas em Python | Agregados SQL, facetas em lote, filtros SQL e detalhes paginados 100+1. Nenhum SELECT * dos conjuntos completos permanece no caminho da UI. |
| P1 | Parse de uploads em cada rerun da área Importações | Prévia por hash e usuário, até um arquivo por tipo (3); limpar ao sair da área/módulo ou logout. Conferência de duplicidade e autorização continuam vivas. |
| P1 | Histórico da Agenda fazia 1 SELECT de participantes por compromisso | Consulta em lote para IDs da página; 32 SELECTs viram 2 numa página com 31 compromissos incluindo lookahead. |
| P1 condicionado ao volume | Próximos limitava compromissos a 31, mas não afastamentos | 31 por fonte, exibindo até 30 compromissos e 30 afastamentos. Próxima página considera ambas as fontes. Nenhum afastamento é excluído; os demais ficam acessíveis por paginação. |
| P2 | Tarefas executava as duas `st.tabs`, incluindo histórico invisível | Navegação condicional com radio; uma lista por render. Deep link define a seção ativa antes do widget. |
| P2 | Cada coleta de viagem relia bindings institucionais não utilizados | Coletor usa AgendaStore sem carregar bindings no caminho quente; editor continua carregando-os normalmente. |
| P2 | Paginação Agenda executava a página antiga antes de chamar rerun | Quatro botões usam callback e o rerun natural do Streamlit; uma passagem de consultas por clique. |
| Correção de segurança associada | Viagens eram coletadas mesmo sem permissão Agenda; sino não comparava identidade/permissões no hit | Checagem de permissão antes do coletor; cache do sino passa a incluir Principal/Store. Reduz consultas não autorizadas e impede reaproveitamento indevido. |
| Correção de estabilidade associada | Ordenação de alertas misturava datetime e inteiro na mesma posição da chave entre fontes | Uniformizadas as chaves mantendo severidade e prioridades internas; evita TypeError ao reunir tarefas, viagens e alertas operacionais. |

Não foi identificado P0 de desempenho universal da Home autenticada e quente.
A classificação P0 do lote depende do volume e latência: com milhares de linhas,
um loop síncrono de rede sob lock pode bloquear as demais escritas. Não foi medido
tempo de bloqueio na produção.

## 4. Consultas e round trips

Premissas: schemas já inicializados, conta com todos os módulos, registros nas
fontes, sem clique de mutação e sem cache de leitura salvo quando indicado.
SELECTs são instruções da aplicação; UNION ALL conta como uma instrução, não como
uma única varredura. O adaptador/pool acrescenta ping, configuração transacional,
BEGIN implícito e COMMIT. Aproximação de rede: **Q + 3–4 × C**, em que Q é o número
de SELECTs e C o número de checkouts; não é uma medição de pacotes.

| Fluxo | SELECTs antes → depois | Escritas no rerun de leitura | Checkouts/detalhes |
| --- | --- | --- | --- |
| Sidebar com caches de autorização e sino válidos | 0 → 0 | 0 | Marca, menu, permissões e logout não consultam banco. |
| Sidebar com sino expirado, autorização válida | 7 → 6 | 0 | 4 leituras operacionais (mapa + 3 fontes), 1 Tarefas, 1 viagem. Checkouts 4 → 3; rede aproximada 19–23 → 15–18. |
| Autorização expirada | +2 → +2 | 0 | Usuário + gabinetes, um checkout. Somar à sidebar quando coincidir. |
| Home quente | 0 → 0 | 0 | Mesma sidebar; não somar o custo do sino duas vezes. |
| Agenda Hoje/Semana/Mês | até 3 → até 3, fora catálogo | 0 | Compromissos, afastamentos, viagens em lote. Cache já existente pode reduzir compromisso/catálogo. |
| Agenda Histórico, 31 compromissos | até 34 → até 4, fora catálogo | 0 | 32 → 2 para compromissos/membros, +1 afastamentos e +1 viagens. Mesma conexão para a dupla de queries do Histórico. |
| Agenda Próximos | até 3 → até 3 | 0 | Ganho é volume limitado de afastamentos, além do rerun evitado na paginação. |
| Tarefas aberta, sem edição | 3 → 2 | 0 | Contadores + lista ativa OU histórico. 3 → 2 checkouts. Editor/lembretes só quando aberto. |
| Produção Mensal, cache frio | 2 → 4 | 5 DDL → 0 | Antes: lista de competências + linhas integrais, além do DDL. Depois: competências + resumo + facetas + página. 3 → 4 checkouts totais; transfere agregados e no máximo 101 detalhes. |
| Produção Mensal, mesmo filtro quente | 2 → 0 | 5 DDL → 0 | Cache de sessão; mudança de página normalmente 1 SELECT, mudança de procurador normalmente 2. |
| Estoque, cache frio | 2 → 5 | 5 DDL → 0 | Snapshot, resumo, facetas, faixas, detalhes; 4 checkouts pois faixas/detalhes compartilham conexão. |
| Estoque, mesmo filtro quente | 2 → 0 | 5 DDL → 0 | Mudança de página normalmente 2 SELECTs; mudança de filtros normalmente 3. Reset de opções inválidas pode adicionar consultas de facetas. |
| Pendências | até 4 → até 4 | 0 | Uma conexão, mapa compartilhado, joins já existentes; não havia N+1 por item nesse caminho. |
| Tela Alertas sem filtro de módulo | 7 → 6 | 0 | Coletores completos da janela; sino pode somar outra coleta se expirar na mesma execução. |

Em primeira entrada no módulo, somar o INSERT de auditoria e seu checkout de
escrita. A primeira sessão registra dois eventos; inicializações frias acrescentam
DDL/introspecções. Erros podem registrar auditoria própria. Filtros de permissões
reduzem as fontes; conta apenas Tarefas agora consulta somente sua fonte de alertas.

O ganho em Relatórios não é diminuir todos os SELECTs frios: as agregações podem
aumentá-los, mas eliminam DDL recorrente, carga integral, widgets com milhares de
linhas e repetição quente. Medir essa troca em Supabase é obrigatório antes de
afirmar ganho em milissegundos.

## 5. Banco, índices e segurança transacional

Pool permanece limitado a 4 conexões por recurso, até 4 recursos, `min_size=0`,
32 aguardando e timeout 15 s. Uma conexão pode ser reutilizada por diferentes
checkouts, não há conexão nova por card. SET LOCAL de search_path, isolation,
timeouts e read-only permanece por transação para compatibilidade com pooler.
Ping e rollback permanecem: removê-los para ganhar latência seria uma mudança de
risco sem evidência. Escritas continuam sob advisory lock; read-only não o toma.

`executemany` agora usa cursor nativo em contexto e não requer IDs retornados;
todos os call sites foram verificados. INSERT unitário continua fornecendo
`lastrowid`. Falha de lote propaga para rollback; revisão de cache só avança após
commit. Não usa thread, async, COPY, autocommit ou biblioteca nova.

Índices aditivos, nos initializers SQLite/PostgreSQL e no schema PostgreSQL:

- `tramita_movimentacoes_pagina_idx(competencia, procurador, protocolo, id)`:
  recorte por competência/procurador e ordenação estável da página filtrada.
- `tramita_estoque_procurador_idx(data_snapshot, procurador)`:
  resumo e filtros/facetas de um snapshot/procurador.

Mantidos os índices competência/tipo, snapshot e hash único da importação.
Não adicionados índices isolados de protocolo/importacao_id sem consulta atual
que os justifique. A página mensal sem procurador pode continuar precisando de
sort; EXPLAIN com distribuição real deve orientar índice futuro. Nenhum DROP,
alteração de dados ou migração destrutiva foi adicionado. Não se alterou a versão
global do bootstrap PostgreSQL.

## 6. Caches e session_state

| Cache | Política após a alteração |
| --- | --- |
| Autorização | Sessão, 20 s, email + Store. Invalidação administrativa já existente mantida; sem cache global de Principal. |
| Sino | Sessão, 20 s, revision + Principal completo + Store; top 5 e contagem preservados. Mutação própria continua invalidando via `alerts_revision`. |
| Relatórios | Sessão, 30 s, Principal + Store + revisões das 3 tabelas; até 24 entradas. SQLite inclui timestamp do DB e WAL. Importação própria limpa cache imediatamente; outra instância/processo fica sujeita ao TTL. |
| Prévia de importação | Sessão, hash + tipo + usuário; no máximo três arquivos parseados; troca de arquivo substitui entrada. Limpar ao sair de Importações/Relatórios ou logout. Parse não é usado nas telas de consulta. |
| Catálogos/configurações | Cache de apresentação PostgreSQL de 30 s já existente, invalidado por revisões. Leituras de negócio/numeração continuam sem esse cache. |
| Agenda/Ofícios | Caches de apresentação existentes de 30 s mantidos. Viagens continuam consultadas em lote. |
| Schemas | Ready de Tramita no Store somente após commit; guards existentes dos demais módulos preservados. |

Não guardamos conexões/cursor em sessão. O Store e os serviços de módulos já eram
reutilizados; não foram duplicados. Dados de relatório passam de todas as linhas
para agregados e páginas; a prévia de upload é a exceção temporária, necessária
para confirmar a importação. Estados antigos de editores/documentos ainda
merecem uma rodada específica de memória, sem limpeza genérica que apague rascunhos.

## 7. Reruns, widgets, imports e logs

Quatro chamadas explícitas de `st.rerun()` foram removidas dos botões de paginação
da Agenda. Cada clique continua provocando um rerun natural, porém não executa
primeiro a página anterior. A paginação nova de Relatórios já nasce com callbacks.

Classificação das demais ocorrências encontradas:

- Necessárias: salvar/cancelar/excluir, refazer prévia, voltar de editor, mudança
  de estado depois de widgets já construídos, recuperação de página vazia,
  navegação solicitada no corpo do script e fechamento da sobreposição Alertas.
- Evitáveis: paginação Agenda, corrigida; troca de editor por botão no corpo pode
  ser convertida futuramente, mas não é o principal custo desta rodada.
- Redundantes: não confirmado callback existente com chamada adicional a rerun;
  `queue_portal_navigation` e `queue_alerts_view` já são separados das versões
  usadas no corpo do script. Não foi removido rerun de consistência.

Tarefas deixou de construir histórico/filtros invisíveis. Ofícios, Memorandos,
Agenda e Relatórios já usam rotas condicionais. Expanders ainda executam seu
conteúdo; detalhes de viagem não fazem query individual, e listas passaram a
ter limites onde alteradas. Histórico administrativo de funções ainda é eager.

`xlrd` é importado dentro de `parse_stock`; agora seu processamento também fica
limitado à nova prévia. Geradores de Portarias ficam depois de render_portal;
não havia justificativa para reorganizar todos os imports. Marca usa `asset()`
cacheado; reconstrução de CSS/base64 foi classificada P3 e não alterada.
Logs de exceção observados ficam nos caminhos de falha, não no loop normal de
cards; logs de auditoria úteis foram preservados.

## 8. Pontos preservados e segunda rodada

- OIDC nativo, administrador protegido e verificações backend por módulo/gabinete
  permanecem. Tarefas mantém `owner_user_id` em cada consulta e escrita.
- Agenda continua com múltiplos membros, regras institucionais, filtros, deep
  links e viagens por compromisso/procurador. Nenhuma regra de afastamento foi
  alterada; apenas o volume de leitura em Próximos.
- Report: KPI global continua global; filtro por procurador afeta comparativos e
  detalhes como antes. Visão Atual mantém quadro geral por procurador. Faixas
  de dias, nulos, zero e médias continuam com as mesmas regras; resultado textual
  é normalizado em Python apenas após GROUP BY. Saída é mapeada explicitamente
  para `SAIDA` no filtro, sem depender de uppercase de texto com acento.
- Documentos, finalização, permissões de importação, conferência de hash e
  duplicidade transacional permanecem. Importações de entrada/saída ainda são
  duas transações como antes; atomicidade do par é assunto separado.

Itens a medir/tratar em segunda rodada:

1. Latência real p50/p95 de Home, navegação, filtros, sino expirado e uploads no
   Supabase/Cloud, com 1 e aproximadamente 20 usuários; cold start separado.
2. Compartilhar coleta entre sino e tela Alertas. Hoje o sino cacheia só resumo;
   reutilizar sua lista truncada na página mudaria filtros/totais, portanto não
   foi feito sem definir um contrato completo de snapshot e autorização.
3. Pendências ainda materializa até 1000 por fonte, ordenando/paginando depois.
   Tarefas mantém cap 50 na janela de alertas e 100 na lista ativa. Definir produto
   para volumes maiores antes de alterar regras de prioridade/paginação.
4. Histórico misto Agenda aplica offset por fonte e recorta a união. Isso pode
   omitir itens na navegação mista; paginação unificada com cursor exige testes
   próprios. O batching desta rodada não muda esse comportamento preexistente.
5. Administração: DDL/seed e `current_all()` de funções institucionais (5 chamadas),
   consultas da Saúde e auditoria. São rotas específicas, fora da Home quente.
6. Ofícios ainda relê séries (1 query), Memorandos importa servidores com lookup
   por matrícula no loop de escrita. Otimizar com testes de importação/upsert;
   as listagens normais não têm query por card.
7. EXPLAIN ANALYZE dos dois índices e das facetas com cardinalidade real;
   decidir se cache/materialização por importação é necessário. Não acrescentar
   índices especulativos para cada filtro textual.
8. Validar combinação de alertas de tarefas/viagens e demais módulos em horários
   iguais; as chaves heterogêneas foram corrigidas e há teste de ordenação mista.
   Não certificar ausência de falhas funcionais sem a suíte completa.

## 9. Verificação e profiler

`tests/test_performance_v2.py` cobre equivalência SQL de produção/estoque em
SQLite e PostgreSQL descartável, nulos e faixas, paginação, falha/retentativa do
schema, contagem de 2 SELECTs no Histórico, páginas de afastamentos, autorização
do coletor, isolamento/TTL do cache, prévia por conta, navegação lazy de Tarefas,
cache do sino por Principal/revision e rollback/invalidação do lote PostgreSQL.

`scripts/profile_performance.py` continua restrito a SQLite temporário. Agora
semeia 4000 movimentações, 2000 linhas de estoque e 40 tarefas, além do dataset
antigo, e mede schema quente, agregados, páginas, tarefas e Histórico. A alteração
do dataset impede comparação direta dos totais de alertas com o JSON da V1.
Nenhum profiler foi inserido na UI de produção.

Verificação realizada: revisão estática das mudanças, conferência dos call sites
de executemany, contratos de permissões/cache, SQL com placeholders e whitelist
de filtros, busca por SELECT * no caminho novo e `git diff --check`.
Sem execução de pytest, AppTest, profiler, PostgreSQL ou EXPLAIN neste computador.
`git diff --check` terminou sem erros de whitespace; o Git apenas avisou sobre
a conversão LF → CRLF configurada no Windows. `git status --short` registra as
alterações locais e os dois arquivos novos, sem commit ou push.

Para validação posterior, em ambiente já preparado: executar os testes V2,
Tramita, Agenda/Próximos, autenticação, alertas, Tarefas e PostgreSQL performance;
depois o profiler com `--phase v2`. Validar filtros em cascata e permissões
revogadas manualmente no Cloud. Não configurar o computador do trabalho.

## 10. Notas estimadas antes/depois

Estas são notas de avaliação técnica estática, não medições de percepção.

| Área | Antes | Depois estimado |
| --- | ---: | ---: |
| Startup | 7 | 7,5 |
| Navegação | 6 | 8,5 |
| Sidebar | 8 | 9 |
| Banco | 6 | 8,5 |
| Alertas | 7 | 8 |
| Agenda | 6 | 8,5 |
| Tarefas | 6,5 | 8,5 |
| Relatórios | 4 | 8,5 |
| Cache | 7 | 8,5 |
| Arquitetura geral | 7 | 8,5 |

Nota geral inicial relatada: **5–6/10**. Nota geral estimada após esta rodada:
**8,5/10**. Alvo **≥9/10** depende de medir o caminho de rede e confirmar os
resultados de regressão; não foi convertido artificialmente em resultado obtido.

Riscos principais: comportamento do lote no driver/pooler de produção, reset de
facetas quando filtros anteriores mudam, planos SQL com dados reais, TTL externo
de 30 s, e criação dos índices na primeira inicialização (pode tomar tempo/lock).
Todas as mudanças de schema são aditivas. Revisão estática não substitui execução.
