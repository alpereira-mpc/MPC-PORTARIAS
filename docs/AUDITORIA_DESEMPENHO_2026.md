# Auditoria de desempenho — 2026-09-14

Auditoria medida em SQLite local com dados sintéticos. Sem Supabase, sem PostgreSQL de produção, sem credenciais.

Ferramenta: `scripts/profile_performance.py` (`--phase baseline` / `--phase after`). JSON em `tmp/` (gitignored).

## Metodologia

- Store SQLite temporário + 250 ofícios recebidos, 180 compromissos futuros, 80 memorandos.
- Contagem de `Store.connection` / `execute` (não inclui `PRAGMA`/`BEGIN` no “meaningful”).
- Tempos de serviço (sino, pendências, overview) com 3 repetições; melhor `total_ms`.
- AppTest Streamlit para Home (1 execução). Navegação Home→módulos **não mediu** (radio `portal_module` ausente no AppTest desta versão).
- Wall-clock da Home no AppTest é dominado pelo runtime Streamlit, não pelo SQL. A comparação objetiva do sino/pendências usa queries e `total_ms` de serviço.

## Nota técnica antes

Percepção relatada ~6–7/10. Evidência: sino e Home fria executavam `collect_pending` completo (até 1000 itens/fonte), 6 conexões por coleta, 3 lookups `sqlite_master` e 3 leituras `oficio_series` repetidas, agenda carregava **toda** `agenda_compromisso_procuradores`. Com o volume sintético: **510 pendências materializadas** para **131 alertas** (badge + top 5).

## Gargalos (ranking por impacto medido)

1. **Sino / `get_alert_summary` → `collect_pending` completo** — 10 queries, 6 conexões, 53 ms; 510 itens para 131 alertas.
2. **Lookups de schema + mapa de gabinetes repetidos** — 3× `sqlite_master` + 3× `oficio_series` por coleta.
3. **Agenda em pendências/alertas** — `SELECT` sem recorte de horizonte + join de participantes de toda a tabela.
4. **Visão Geral de Memorandos** — `list(limit=200)` + payload JSON só para 3 métricas.
5. **SQLite `commit` em conexões `read_only`** — fsync desnecessário em SELECT.
6. **AccessStore** — `BEGIN IMMEDIATE` + `_mark_protected_admin` no primeiro `ensure_schema` mesmo com schema já pronto (uma vez por processo).

Não foram gargalos medidos neste hardware: Ofícios `overview`/`list(50)`, Agenda Hoje/Mês no store, Auditoria overview, Saúde (quando aberta). AppTest Home ~0,7–1,6 s é custo Streamlit (rerun autenticado: **0 queries** se o cache de 20 s do sino bater).

## Alterações

| Arquivo | O quê |
| --- | --- |
| `services/pending.py` | Cache de existência de tabela; uma conexão + um mapa de gabinetes por coleta; join de destinatários sem N+1; agenda com subquery `LIMIT` + `LEFT JOIN` participantes; `alert_window`; `count_pending` / `list_pending`. |
| `services/alerts.py` | `collect_alerts` usa `alert_window=True` (ofícios prazo nulo ou ≤3 dias; agenda agora→+24 h; memorandos início ≤3 dias). |
| `database/store.py` | SQLite: não faz `commit` em `read_only`. |
| `database/memorandos.py` | `situacao_counts`; índice aditivo `memorandos_subst_datas_idx (data_fim, data_inicio)`. |
| `services/memorandos_ui.py` | Visão Geral usa `situacao_counts` (sem payload). |
| `database/access.py` | Pula writer se o admin protegido já está marcado. |
| `tests/conftest.py` | Limpa cache `_TABLES` entre testes. |
| `tests/test_performance_structure.py` | Contratos estruturais (sem assert de milissegundos). |
| `scripts/profile_performance.py` | Profiler local SQLite. |

Regras de negócio, fail-closed, gabinetes, cache ~20 s do sino e `alerts_revision` preservados. Contagem de alertas no volume sintético: **131 → 131**. Pendências completas: **510 → 510**.

## Métricas antes / depois (SQLite sintético, medido)

Operação | Antes | Depois | Melhoria
--- | --- | --- | ---
Sino frio (`get_alert_summary`) | 53,1 ms · 10 queries · 6 conexões | 12,3 ms · 4 queries · 1 conexão | 77% tempo; queries 10→4
Pendências (`collect_pending`) | 45,6 ms · 10 q · 6 conn | 22,0 ms · 4 q · 1 conn | 52% tempo; queries 10→4
Página Alertas (`collect_alerts`) | 52,5 ms · 10 q · 6 conn | 15,1 ms · 4 q · 1 conn | 71%
`pending_counts` | 48,4 ms · 10 q | 25,3 ms · 4 q | 48%
Home AppTest (SQL) | 16 executes · 9 conn · 29 ms SQL | 10 executes · 4 conn · 17 ms SQL | queries 16→10
Home AppTest (wall) | 896 ms | 1591 ms | *não usar* (ruído Streamlit)
Home rerun AppTest | 677 ms · 0 SQL | 586 ms · 0 SQL | cache do sino
Ofícios overview | 8,3 ms · 1 q | 5,8 ms · 1 q | residual
Ofícios list 50 | 5,3 ms | 7,1 ms | residual
Agenda hoje | 4,3 ms | 5,0 ms | residual
Memorandos list 200 | 6,6 ms | 8,1 ms | residual
Memorandos `situacao_counts` | (não existia; overview lia 200 payloads) | 4,7 ms · 1 COUNT | novo caminho
Auditoria overview | 36 ms · 7 q | 34 ms · 7 q | inalterado
Saúde `diagnose` | 38 ms · 25 executes | 70 ms · 25 executes | inalterado (não otimizado; variação)
Startup `Store()` | 94 ms | 103 ms | variação

Queries por operação (medidas):

- Home (SQL): **16 → 10**
- Sino: **10 → 4** (conexões **6 → 1**)
- Pendências: **10 → 4**
- Ofícios abertura de store/list: 1 (já lazy por aba)
- Agenda list: 1

## Home

Continua sem painel de pendências, sem Saúde, sem Backup, sem BLOBs. Trabalho extra era o **sino** (`collect_pending` completo). Depois: mesma UI; sino com janela de alerta + 1 conexão. Rerun com cache 20 s: 0 queries.

## Sino

Antes: `collect_pending` (cap 1000/fonte) + filtro Python. Queries típicas: 3 schema + 3 gabinetes + 3 listas + 1 extra.

Depois: `alert_window`; schema em cache de processo; 1 `oficio_series`; 3 SELECTs recortados. Cache 20 s e `alerts_revision` iguais. 131 alertas iguais no dataset de teste.

## Pendências

Ainda materializa o conjunto autorizado (cap 1000/fonte) para ordenar entre módulos; paginação 50 na UI continua após o collect. `list_pending`/`count_pending` compartilham as mesmas regras. Ganho: menos conexões/lookups e agenda sem dump de participantes.

## Ofícios / Agenda / Memorandos

Ofícios: abas já eram lazy; BLOBs só no detalhe. Join de destinatário elimina N+1 quando `serie`/`membro_id` vazios.

Agenda módulo: Hoje/Semana/Mês já limitam intervalo; Próximos já usa `LIMIT 31`. Sem refactor.

Memorandos: Visão Geral não carrega 200 payloads. Base de servidores, XLSX e arquivos inalterados (só na aba Base).

## Administração

Auditoria/Saúde/Backup não rodavam na Home (confirmado: Saúde só no profiler ao chamar `diagnose` de propósito). Backup continua só no clique. Sem mudança de queries nesses módulos.

## Caches

Inalterados de propósito: `_access_cache` 20 s; sino 20 s + `alerts_revision`; `st.cache_data` ofícios/agenda 30 s (PostgreSQL). Novo: `_TABLES` em `pending.py` (existência de tabela por `schema_key`, processo). Sem `st.cache_data.clear()`.

## Índices

`memorandos_subst_datas_idx` em `memorandos_substituicao(data_fim, data_inicio)` — filtros de pendências/alertas/contagens. Idempotente, SQLite e PostgreSQL. Demais índices de ofícios/agenda/auditoria já existiam.

## Reruns

Nenhum `st.rerun()` removido. Callbacks de navegação já enfileiram sem rerun. Home rerun autenticado já estava em 0 SQL com cache do sino.

## ~20 usuários

Menos checkouts de conexão no sino (6→1 por miss de cache) reduz contenção no pool PostgreSQL. Conversão DOCX/PDF, backup e `registrar_evento` não foram alterados. Saúde continua cara **somente quando aberta**.

## Testes

`python -m pytest -q` → **498 passed, 104 skipped** (7 testes estruturais novos). Sem asserts frágeis de tempo.

## Skips

- AppTest de navegação Home→Agenda/Ofícios/Memorandos/Portarias/Admin (radio não encontrado).
- Carga real de 20 usuários.
- PostgreSQL descartável nesta rodada (script antigo `scripts/measure_navigation.py` continua para loopback).
- `st.fragment` (risco de session_state/navegação sem ganho medido no SQL).
- Lazy import de `app.py` (Home dá `st.stop()` antes dos imports pesados de Portarias).

## Riscos residuais

- `alert_window` pode omitir um alerta se a regra de negócio exigir horizonte maior que 24 h na agenda ou 3 dias em ofícios/memorandos — as regras atuais de `alert_from_*` cabem nessa janela; testes de alertas passaram.
- Cache `_TABLES` assume schema estável no processo (igual aos `_READY` existentes).
- SQLite `read_only` sem commit: escritas acidentais nessa conexão não persistem (comportamento desejado).
- Índice novo: um `CREATE INDEX IF NOT EXISTS` no primeiro `MemorandosStore` do processo.

## Não feito (risco > ganho)

- Paginar pendências só no SQL (ordenação cruzada de 3 módulos).
- Fragments no sino.
- Redis/Celery/troca de Streamlit.
- Refatorar Portarias.
- Otimizar Saúde/Backup/DOCX.
- Contagens 24 h/7 d/30 d da Auditoria (já são `COUNT` indexados).
- Base64 do ícone Gmail no login (desprezível vs Streamlit).

## Nota técnica depois

Estimativa **8/10** no caminho medido (sino, pendências, Home SQL). A pausa percebida na troca de módulos no AppTest **não foi remedida**; deve-se validar manualmente. O teto restante é o próprio Streamlit no rerun (Home rerun ~0,6 s com 0 SQL).

## Testar manualmente antes de commit

1. Login → Home: sino (badge + top 5) vs Central de Pendências / Alertas (totais).
2. Ofício vencido, prazo hoje, sem prazo; compromisso em &lt;2 h e amanhã; memorando em andamento e agendado em 2 dias.
3. Usuário só Agenda: sino não vaza Ofícios.
4. Troca Home → cada módulo; voltar; rerun (widget).
5. Invalidar sino: salvar ofício/agenda e ver badge atualizar na hora.
6. Memorandos Visão Geral: métricas iguais à listagem filtrada.
7. Admin: Auditoria e Saúde só ao abrir; Backup só ao gerar.
8. Logout / outro usuário: caches de sessão não vazam.
