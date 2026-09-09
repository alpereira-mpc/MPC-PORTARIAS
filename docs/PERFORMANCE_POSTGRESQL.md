# Auditoria de performance — 09/09/2026

## Diagnóstico

O app recriava `Store()` a cada interação. Cada construção abria uma conexão
SSL, adquiria o bloqueio exclusivo e executava duas verificações DDL e a consulta
de versão. O seed já era idempotente, mas a verificação de schema se repetia.
Cada método seguinte abria outra conexão SSL e executava cinco comandos de
preparação, incluindo o mesmo bloqueio usado na finalização. Até leituras
aguardavam gravações e backups. Procuradores e assentos também eram consultados
duas vezes no mesmo rerun de cadastro; Configurações repetia procuradores.

Esses custos foram reproduzidos em PostgreSQL local. Não foi medido o endpoint
Supabase real: região, latência de rede, carga e plano do serviço continuam
desconhecidos. Não é possível atribuir uma porcentagem exata da lentidão da
nuvem a cada um deles sem telemetria naquele ambiente.

## Medição antes/depois

AppTest do Streamlit 1.49.1, PostgreSQL 17.11 com SSL em loopback, Python 3.12,
psycopg 3.3.5 e psycopg_pool 3.3.1. Schema descartável com sete procuradores e
sem Portarias; três reruns por tela, mediana. Medição anterior às alterações;
medição final após a suíte, sem testes simultâneos. Tempos incluem execução
da tela e infraestrutura do AppTest; não medem pintura do navegador remoto.

| Tela | Rerun antes → depois (ms) | Novas conexões SSL | Chamadas `execute` | Comandos SQL/checagens |
|---|---:|---:|---:|---:|
| Nova Portaria | 1401,47 → 161,54 | 8 → 0 | 50 → 21 | 50 → 42 |
| Histórico | 358,72 → 110,57 | 4 → 0 | 26 → 9 | 26 → 18 |
| Procuradores | 595,60 → 122,05 | 6 → 0 | 38 → 9 | 38 → 18 |
| Configurações | 807,20 → 132,05 | 9 → 0 | 56 → 21 | 56 → 42 |

`execute` conta envios explícitos pelo driver. Quatro `SET` agora viajam juntos,
por isso envios e comandos SQL diferem. Os totais incluem preparação e checagem
de conexão; excluem `BEGIN`/`COMMIT` implícitos do driver. Consultas de dados da
tela: respectivamente 7→7, 3→3, 5→3 e 8→7, além da consulta de versão removida
dos reruns. O cenário de preview/histórico preenchido pode executar mais consultas.

| Tela | Aquisição de conexões somada antes → depois (ms) | initialize no rerun antes → depois (ms) |
|---|---:|---:|
| Nova Portaria | 1230,44 → 2,88 | 85,30 → 0,02 |
| Histórico | 221,60 → 0,92 | 61,94 → 0,01 |
| Procuradores | 367,70 → 1,14 | 60,49 → 0,02 |
| Configurações | 645,47 → 2,15 | 62,58 → 0,01 |

Antes, aquisição era a abertura física; depois inclui empréstimo e checagem do
pool. Medianas de componentes não precisam somar a mediana total. Houve variação
de tempo entre execuções no Windows: as contagens são evidência mais estável
que os milissegundos. Estes números não prometem o mesmo ganho na nuvem.

Primeira execução do app, após preparar o schema: 1660,42→1544,38 ms.
Bootstrap de schema vazio: 384,01→1038,50 ms, uma única amostra por versão,
incluindo criação de tabelas, seed e, depois, início do pool. A conexão física
nessa amostra foi 66,42→143,47 ms. Não houve ganho demonstrado na partida fria;
o ganho comprovado está nos reruns. Não há delays artificiais.

Instrumentação reproduzível: `scripts/measure_navigation.py caminho_saida.json`,
com `MPC_TEST_POSTGRES_URL` apontando exclusivamente para banco descartável em
loopback, nome `mpc_disposable_tests` e marcador de segurança exigido pela suíte.
O script recusa outros destinos antes de conectar, cria schema isolado e não
finaliza atos. Artefatos desta execução, ignorados pelo Git:
`tmp/performance-before.json`, `tmp/performance-after-final.json` e
`tmp/pytest-performance-full.xml`.

## Alterações e garantias

- Pool compartilhado no módulo Python por configuração de conexão/processo;
  `Store` continua sendo um objeto leve por rerun. Não há conexão compartilhada
  simultaneamente por transações. Não depende do ciclo de vida de uma sessão
  Streamlit e não requer `st.cache_resource`.
- Máximo quatro conexões por pool, mínimo zero; espera de 15 segundos e fila
  limitada a 32. Ociosidade de 60 segundos e vida máxima de 600 segundos.
  O registro guarda no máximo quatro configurações para limitar trocas de URL.
- Checagem antes do empréstimo, descarte e reposição de sockets interrompidos;
  commit/rollback antes da devolução. Não se repetem escritas automaticamente
  após erros, evitando duplicação quando o resultado do commit é desconhecido.
- SSL e prepared statements desabilitados permanecem. `search_path`, timeouts
  e autorização administrativa são transacionais, compatíveis com pooler por
  transação. Erros de conexão dos workers são sanitizados antes de ir ao log.
- Schema inicializado é lembrado somente depois do commit, por configuração e
  schema. Falhas permitem nova tentativa; `migrate()` força verificação.
  Reinício do processo verifica novamente. Nenhuma alteração no schema ou seed.
- Leituras usam `READ ONLY` sem bloquear escritores. Escritas mantêm o mesmo
  advisory lock, constraints e transações de numeração, cancelamento e exclusão.
- Catálogos são reutilizados somente no rerun, com cópia defensiva e invalidação
  ao abrir uma transação de escrita. Novo rerun limpa o cache, inclusive se um
  Store for reutilizado. Não há TTL nem cache entre sessões/reruns. Numeração,
  configurações, snapshots DOCX/PDF e histórico não entram nesse cache.
- SQLite conserva conexões, PRAGMAs, schema e comportamento anteriores. Nenhum
  dado local/real, template, gerador, conteúdo jurídico ou arquivo de configuração
  Streamlit foi alterado.

## Consultas grandes e riscos restantes

O Histórico já exclui os blobs DOCX/PDF, mas carrega todos os metadados/payloads
para os filtros atuais em Python. A auditoria também lista todas as entradas.
`get()` pode carregar ambos os snapshots de um ato selecionado. Foram preservados:
paginar antes dos filtros poderia ocultar resultados e alterar a funcionalidade.
Os índices existentes atendem IDs, unicidade anual e vínculos administrativos.
Um índice isolado em `criada` não elimina a transferência completa do Histórico;
não se criou migration sem evidência de gargalo de ordenação.

Backups/finalizações ainda podem manter o bloqueio de escrita durante trabalho
demorado, e o backup comprimido ainda precisa caber em memória. Leitores agora
podem ver o último estado confirmado durante uma gravação em andamento, sem ler
dados não confirmados; a validação crítica continua dentro da transação de escrita.
Cada processo tem seu próprio pool: limites totais devem considerar réplicas.
Falha de rede durante uma operação é comunicada; a próxima aquisição pode se
recuperar. Mudanças externas de schema requerem reinício ou `migrate()` explícito.

## Validação e entrega

**216 testes aprovados, zero falhas e zero pulados em 134,29 segundos.**
Comando: `python -m pytest -q --basetemp=tmp/pytest-performance-full
--junitxml=tmp/pytest-performance-full.xml`, com a URL exclusiva do servidor de
teste. São os 205 testes anteriores e 11 novos; o teste de erro/SSL existente
foi ajustado para interceptar o construtor usado pelo pool.

Cobertura: SQLite, PostgreSQL real descartável, bootstrap e retry, finalização
concorrente em threads/processos, sequência anual, cancelamento, hard delete,
backup e audit_log, telas, conexão interrompida, rollback de SQL/exceção Python,
isolamento da autorização de exclusão, leitura durante escrita, rejeição de
escrita em leitura, limites do pool e invalidação/cópia/escopo do cache.

Arquivos de produto: `app.py` (uma chamada no início do rerun),
`database/postgresql.py`, `database/store.py`, novo `database/pool.py`,
`requirements.txt` (adição de `psycopg_pool>=3.2,<4`).
Validação/documentação: `tests/test_backend_selection.py`, novo
`tests/test_postgresql_performance.py`, novo `scripts/measure_navigation.py`,
`docs/POSTGRESQL_SUPABASE.md` e este relatório.

Pronto para revisão e commit/push, com validação de navegação na nuvem após
publicar. O Supabase real não foi acessado. Nenhum commit ou push foi executado.

Referência técnica: [pool de conexões psycopg](https://www.psycopg.org/psycopg3/docs/advanced/pool.html)
e [parâmetros e checagem do pool](https://www.psycopg.org/psycopg3/docs/api/pool.html).
