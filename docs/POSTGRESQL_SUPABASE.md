# SQLite local e PostgreSQL/Supabase

## Seleção e segurança

`Store()` procura `DATABASE_URL` primeiro no ambiente e depois nos Streamlit
Secrets. Se a chave estiver ausente, usa o SQLite e o caminho local anteriores,
incluindo `MPC_DB_PATH`. Uma URL presente, mas vazia, inválida ou inacessível
gera erro; não há fallback silencioso para outro banco.

`Store(caminho)` continua abrindo explicitamente um SQLite, inclusive para
backups e testes. Os argumentos explícitos de URL/schema permitem testar o
PostgreSQL isoladamente. O app não contém URL ou senha fixa. Os testes removem
`DATABASE_URL` herdada e substituem os Secrets; não leem o Supabase real.

`.streamlit/secrets.toml` está no `.gitignore`. Configure o valor de
`DATABASE_URL` somente no ambiente/Secrets; não cole credenciais neste arquivo,
no código, em arquivos de teste ou em comandos que serão compartilhados.

## Arquitetura

- `database/store.py`: uma API e as mesmas regras de negócio para os dois bancos.
- `database/backend_config.py`: seleção segura do backend.
- `database/postgresql.py`: conexão, adaptação de parâmetros/linhas, transações,
  bootstrap e backup PostgreSQL.
- `database/postgresql_schema.sql`: migration PostgreSQL v1, equivalente às
  estruturas do SQLite v2, com constraints, índices, foreign keys e triggers.
- `services/deletion.py`: mantém o fluxo administrativo; delega somente a
  autorização de exclusão à camada comum.

O app identifica o backend no painel de backup, usa o caminho retornado pelo
backup e inicia a reutilização de catálogos a cada rerun. Não foram alterados formulários,
conteúdo jurídico, numeração funcional, templates, DOCX ou PDF.

As tabelas ficam no schema privado `mpc_portarias`, não em `public`.
O usuário PostgreSQL precisa poder criar schema, tabelas, índices, funções e
triggers. Não execute o arquivo de schema diretamente em `public`;
`Store.initialize()` controla sua aplicação transacional e versionada.

Tipos: snapshots usam `BYTEA`; IDs auxiliares usam `IDENTITY`; números/anos usam
inteiros. Datas ISO, payloads JSON e configurações continuam como `TEXT`, e
`ativo` como inteiro 0/1, para preservar o contrato atual e as consultas
compartilhadas. Não há conversão do SQLite nem alteração de sua versão.

## Bootstrap e idempotência

Na primeira inicialização do schema PostgreSQL, sob bloqueio transacional:

1. São criadas as estruturas ausentes sem apagar tabelas.
2. Havendo dados antigos do aplicativo em `public`, a inicialização é interrompida
   para revisão explícita, evitando criar uma numeração paralela inadvertida.
3. O seed só é aplicado se todas as tabelas do aplicativo, inclusive de backup,
   estiverem vazias. Dados existentes nunca são sobrescritos pelo seed.
4. São cadastrados os sete procuradores, funções, assentos, motivos e bases
   do `database/seed.json` atual.
5. O baseline e o último número de 2026 ficam em **8**; a próxima Portaria é **9**.
   A configuração de confirmação da sequência também é persistida.
6. A versão é registrada em `schema_migrations`. Reinicializações não repetem
   o seed, mesmo se os atos forem posteriormente excluídos.

Banco parcialmente preenchido não recebe seed corretivo automático. Uma
estrutura antiga/incompleta exige revisão administrativa; versões desconhecidas
são recusadas. Não há `DROP TABLE` no código de inicialização de produção.

## Numeração e transações

Cada operação PostgreSQL usa uma transação em `READ COMMITTED`. As escritas usam
um `pg_advisory_xact_lock` por schema. Isso serializa as mutações da aplicação,
incluindo finalização, ajuste de sequência, cancelamento e exclusão.
O bloqueio é do servidor e vale também entre processos/réplicas.

A consulta do próximo número, a geração do DOCX, a gravação do ato, a atualização
da sequência e o evento permanecem na mesma transação. Se qualquer etapa falhar,
há rollback de todas essas gravações. O banco também aplica `UNIQUE(ano,numero)`.
Rascunhos/prévias não reservam número; a indicação exibida é uma previsão.
Repetir a finalização do mesmo registro não consome outro número.

Cancelamento conserva número e snapshot. Exclusão administrativa exige as
confirmações anteriores, backup e auditoria; só recupera o último número quando
permitido pela regra existente, respeitando baseline e atos posteriores.
Nenhuma Portaria posterior é renumerada.

Os locks e `search_path` são transacionais. Prepared statements automáticos
estão desativados para compatibilidade com pooler em modo de transação.
As conexões retornam ao pool ao sair do contexto. Leituras usam `READ ONLY` sem
lock de escrita. O pool tem no máximo quatro conexões por configuração e processo,
com verificação antes do empréstimo e espera limitada a 15 s. Conexões ociosas
são reduzidas após 60 s e renovadas após até 600 s. Timeout de conexão: 10 s;
espera de lock: 30 s; execução de cada comando: 60 s.

## Backups e auditoria

SQLite continua produzindo seus backups `.db` como antes.

PostgreSQL produz um snapshot lógico `.json.gz` com formato
`mpc-postgresql-v1`, todas as tabelas funcionais e blobs codificados em base64.
A leitura é feita linha a linha por cursor do servidor, evitando montar uma
cópia integral descomprimida do banco em RAM. O arquivo não sobrescreve outro.

Uma cópia comprimida e seu SHA-256 ficam na tabela `backup_snapshots`, vinculados
pela referência registrada em `audit_log.backup`. Na exclusão, essa gravação,
a auditoria, a remoção do ato e o ajuste da sequência compartilham a transação.
Se o backup falhar, a exclusão não acontece. Se a transação falhar depois,
o ato permanece e as gravações no PostgreSQL são revertidas; uma cópia local de
segurança eventualmente criada pode permanecer.

A cópia persistente sobrevive à perda do filesystem do Cloud. Pode ser localizada
no banco por `backup_snapshots.referencia`; `conteudo` contém o gzip completo e
`sha256` permite verificar sua integridade. Backups anteriores não são embutidos
recursivamente em novos snapshots; devem ser conservados separadamente.
Isso não substitui a política externa de backup/PITR do serviço PostgreSQL.

O formato não é SQLite nem um arquivo `pg_dump`. Não há restauração ou migração
automática. Uma restauração futura deve ser explícita, em destino isolado,
reaplicando o schema e respeitando dependências e sequências IDENTITY.

## SSL, dependência e implantação

Dependências PostgreSQL: `psycopg[binary]>=3.2,<4` e `psycopg_pool>=3.2,<4`.
SSL é obrigatório (`require` no mínimo); `verify-ca` e `verify-full` são
preservados quando configurados na URL. `require` cifra a conexão, mas não
valida a identidade do servidor como `verify-full`.

O Supabase disponibiliza URL direta e poolers. Se o Cloud não alcançar o
endpoint IPv6 direto, use o pooler adequado para IPv4 nos Secrets. Não é preciso
usar o cliente HTTP Supabase nem adicionar um ORM ao projeto.

## Testes e limites

Os testes PostgreSQL exigem `MPC_TEST_POSTGRES_URL`, restrita a loopback,
banco `mpc_disposable_tests` e marcador `public.mpc_test_guard` com valor
`disposable-mpc-tests-v1`. Cada caso usa um schema aleatório `mpc_test_*`.
Sem essa configuração, a integração PostgreSQL é explicitamente pulada;
isso não equivale a validar o backend.

Nesta tarefa foi usado PostgreSQL 17.11 descartável, com certificado SSL local,
e psycopg 3.3.5. Resultado final: **205 testes aprovados, zero falhas e zero
testes pulados**, em 154,57 segundos (151 testes anteriores e 54 novos casos).
Comando: `python -m pytest -q --basetemp=tmp/pytest-dual-full-final
--junitxml=tmp/pytest-dual-full-final.xml`, com a variável exclusiva de teste
configurada para o servidor descartável. Relatório JUnit em `tmp/`, ignorado
pelo Git.
Foram exercitados bootstrap concorrente, finalizações em threads e processos,
rollback, virada de ano, cancelamento, hard delete, auditoria, perda da cópia
local do backup, reconexão e telas Streamlit, além de toda a suíte SQLite.

Limitações: as escritas da aplicação continuam serializadas por schema, o que
prioriza consistência sobre throughput de mutações. Alterações SQL feitas fora da aplicação
não participam automaticamente desse lock. O blob comprimido de um backup ainda
precisa caber em memória para ser enviado ao PostgreSQL. Arquivos de exportação
do Cloud continuam efêmeros; snapshots persistidos permitem reexportação.
O Supabase real não foi acessado: permissões, conectividade e pooler devem ser
confirmados após publicar. Não há sincronização entre SQLite e PostgreSQL.

Fontes: [conexões Supabase](https://supabase.com/docs/guides/database/connecting-to-postgres),
[SSL Supabase](https://supabase.com/docs/guides/platform/ssl-enforcement),
[transações psycopg](https://www.psycopg.org/psycopg3/docs/basic/transactions.html),
[prepared statements](https://www.psycopg.org/psycopg3/docs/advanced/prepare.html).

A auditoria de performance posterior está em [PERFORMANCE_POSTGRESQL.md](PERFORMANCE_POSTGRESQL.md).
Schema/bootstrap bem-sucedido é lembrado por processo e configuração/schema;
`migrate()` força nova verificação idempotente. O cache de catálogos dura apenas
um rerun e é invalidado nas escritas. SQLite, números e documentos não usam esse cache.
