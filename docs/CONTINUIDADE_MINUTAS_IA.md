# Continuidade — especialidades do Minutas IA

Retomada em 2026-10-14 a partir de `d70d5ac` (`main`, igual a `origin/main`). O worktree estava limpo; o `main` local estava 5 commits atrás e foi apenas avançado com fast-forward. Nenhum acesso a Secrets, produção ou Supabase operacional.

## Estado encontrado

O commit parcial já contém a arquitetura pedida. Não foi recomeçada.

- Catálogo Python e SQL de 24 especialidades da Dra. Elvira. Somente `elvira.pca.rpps` tem motor `pca-rpps-v1` e estado inicial `Funcional com ajustes`. As outras 23 ficam em `A iniciar`, sem motor.
- Tabelas aditivas, vínculos, eventos, documentos novos e a visão `minutas_documentos_catalogo` em `database/minutas_especialidades.sql`. O aplicativo não executa esse DDL.
- Classificação conservadora, plano idempotente, rollback da transação e restauração manual.
- Pesquisa, geração, extração e DOCX exigem a especialidade selecionada. Sem escopo válido a consulta não abre o acervo inteiro.
- RAG continua em FTS/GIN português. Não há embeddings.
- Interface: Procuradora fixa, classe, especialidade, acervo paginado, pendências, administração, pesquisa e geração só para RPPS.

Arquivos do commit: `database/minutas_especialidades.sql`, `database/minutas_especialidades.py`, `database/minutas_referencias.py`, `services/minutas_specialties.py`, `services/minutas_specialties_ui.py`, `services/minutas_acervo.py`, `services/minutas_ia_ui.py`, `services/minutas_library.py`, `services/minutas_search.py`, `services/minutas_generation.py`, `services/minutas_extraction.py`, `services/minutas_docx.py`, `scripts/minutas_reclassificar.py`, `docs/minutas-especialidades.md` e os testes de especialidades, geração, pesquisa, DOCX, extração e piloto.

## Causa do bloqueio no Cloud

`services/minutas_ia_ui.py` só abre Acervo, Pesquisa e Gerar Minuta depois de enxergar as relações novas no `search_path` e um catálogo não vazio.

1. Relação ausente ou invisível para o papel do `DATABASE_URL`: a tela permanece na advertência e lista os nomes ausentes. É o caso compatível com a mensagem já vista em produção. O DDL não foi executado no Supabase operacional.
2. Relações presentes e catálogo vazio: “Catálogo não provisionado”. Também permanece bloqueado.
3. Catálogo com as 24 linhas do script: a seleção abre. A geração RPPS continua vazia até a reclassificação explícita. Isso não é falha da tela.

A proteção não foi removida. Outro erro que não seja relação ausente deixa de ser disfarçado de migração faltante.

## O que esta retomada alterou

- Sonda somente leitura `missing_relations()` (`to_regclass` no PostgreSQL; `sqlite_master` no teste sintético).
- A advertência passa a citar as relações ausentes.
- O teste PostgreSQL usa `ClientCursor`, para executar o script inteiro quando houver banco descartável.

Não houve refatoração do motor, mudança de prompt, autenticação, Backup V2 ou outros módulos.

## Testes desta retomada

Sem Gemini e sem rede.

- Antes do ajuste final da sonda: geração, pesquisa, DOCX, extração, piloto e especialidades, 180 aprovados.
- Depois do ajuste: `tests/test_minutas_specialties.py` (35, incluindo o bloqueio quando as relações faltam e a navegação sem Gemini), `test_module_navigation_executes_only_the_active_view` e `test_authorized_user_sees_and_opens_private_module`.

`tests/test_minutas_specialties_postgresql.py` foi pulado: não há `MPC_TEST_POSTGRES_URL` com loopback, banco `mpc_disposable_tests` e a tabela de proteção. A migração real, RLS e FTS não foram executados aqui. Dados reais não foram reclassificados.

## Próximo passo para desbloquear o Cloud

Não publicar outro código esperando que isso crie as tabelas. O código já publicado passa a abrir o módulo no rerun seguinte à migração correta. Esta retomada só torna a mensagem mais precisa; para valer no Cloud, precisa do deploy desta revisão. O desbloqueio em si continua dependendo do SQL.

Executar só com autorização, backup restaurável comprovado e de preferência primeiro num clone. Não usar produção como teste. A ferramenta `scripts/minutas_reclassificar.py` não lê Secrets; a conexão entra por `MINUTAS_MIGRATION_DATABASE_URL`.

1. Backup do schema `mpc_portarias` e do bucket `minutas-referencias`. Provar restauração. O Backup V2 não substitui essa evidência para as tabelas novas.
2. Inventário legado, sem DDL:

```powershell
.venv\Scripts\python.exe scripts/minutas_reclassificar.py inventario-legado --report inventario-antes.json --storage-bucket minutas-referencias
```

3. No SQL Editor do mesmo projeto do `DATABASE_URL`, como papel que já acessa `minutas_referencias`, executar o arquivo inteiro `database/minutas_especialidades.sql`. Ele é aditivo e idempotente (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`, `DROP NOT NULL`). Se o editor recusar o `BEGIN` por já estar numa transação, retirar só a primeira linha `BEGIN;` e a última `COMMIT;` e executar de novo. Não apagar tabelas para “recomeçar”.
4. Conferência somente leitura:

```sql
SELECT id, grupo, estado, motor FROM mpc_portarias.minutas_especialidades ORDER BY grupo, nome;
SELECT COUNT(*) AS especialidades FROM mpc_portarias.minutas_especialidades;
SELECT to_regclass('mpc_portarias.minutas_especialidades') AS especialidades,
       to_regclass('mpc_portarias.minutas_classificacoes') AS classificacoes,
       to_regclass('mpc_portarias.minutas_acervo_vinculos') AS vinculos,
       to_regclass('mpc_portarias.minutas_especialidades_eventos') AS eventos,
       to_regclass('mpc_portarias.minutas_acervo_documentos') AS documentos,
       to_regclass('mpc_portarias.minutas_documentos_catalogo') AS catalogo;
SELECT COUNT(*) AS referencias FROM mpc_portarias.minutas_referencias;
SELECT COUNT(*) AS extracoes FROM mpc_portarias.minutas_extracoes;
SELECT COUNT(*) AS classificados FROM mpc_portarias.minutas_classificacoes WHERE estado = 'CLASSIFICADO';
```

Sucesso do DDL: 24 especialidades; `elvira.pca.rpps` em `Funcional com ajustes` com motor `pca-rpps-v1`; as seis relações não nulas; contagens de referências e extrações iguais às de antes. `classificados` pode ser 0. Nesse momento a tela deve abrir e a geração RPPS deve informar que não há processo classificado.

5. Simular e, só com o plano conferido e a evidência de restauração, aplicar:

```powershell
.venv\Scripts\python.exe scripts/minutas_reclassificar.py simular --report plano-conferido.json --storage-bucket minutas-referencias
.venv\Scripts\python.exe scripts/minutas_reclassificar.py aplicar --plan plano-conferido.json --report reconciliacao.json --storage-bucket minutas-referencias --actor operador-autorizado --backup-evidence identificador-da-evidencia --restore-verified
```

Repetir a simulação. A segunda aplicação tem de alterar 0 classificações. Conferir de novo as contagens de referências, caminhos e extrações. Não mover PDFs.

6. Na interface, com a Dra. Elvira: selecionar PCA e Institutos Previdenciários; abrir pendências; pesquisar só depois de haver parecer classificado; gerar e exportar DOCX apenas para processo RPPS `CLASSIFICADO`. Câmara, Prefeitura sem modalidade e secretaria sem nível federativo devem permanecer pendentes se a evidência documental for insuficiente.

Reversão de classificação: histórico no Acervo, que grava novo evento e não apaga arquivo nem extração. Reversão de desastre: restaurar o backup. Não dropar as tabelas novas como primeira correção, e não voltar o aplicativo antigo depois de cadastrar processo sem os dois documentos principais.

## Verificação de 14/10/2026, 15h

Backup PostgreSQL encontrado em `C:\Users\User\MPC_Backups\MPC_ANTES_ESPECIALIDADES_20261014_152117.backup`. Tamanho medido: 47.184.088 bytes (o valor informado de 4.718.408 não confere com o arquivo). Formato customizado. `pg_restore -l` saiu com código 0. O TOC contém só o schema `mpc_portarias`, com tabela e dados de `minutas_referencias`, `minutas_extracoes` e `minutas_documentos_complementares`, inclusive o índice GIN `minutas_extracoes_parecer_fts_pt_idx` e RLS. `minutas_especialidades` não está no backup, como esperado. O schema `storage` não está no arquivo: os PDFs não foram copiados.

Restauração não executada. Há PostgreSQL 17 local escutando em todas as interfaces, sem o banco descartável protegido (`mpc_disposable_tests` e a tabela de guarda). Não foi criado banco nem restaurado o arquivo.

Storage do Minutas IA: bucket privado `minutas-referencias`, API `storage/v1`, chave secreta do backend. Caminhos legados `referencias/{id}/{auditoria|parecer}/…`; complementos usam o `path` gravado. Extrações são texto no PostgreSQL, não arquivos. A cópia do bucket não foi feita: as regras do repositório impedem ler Secrets e conectar ao Storage operacional. Nenhum PDF foi contado nem hasheado nesta sessão.

O SQL revisado é compatível com PostgreSQL 17, aditivo e idempotente. Não houve correção de código. A Fase A não deve começar antes da restauração comprovada deste backup e da cópia conferida do bucket.

## Riscos residuais

- DDL, RLS e FTS não rodaram em PostgreSQL nesta sessão.
- O papel do `DATABASE_URL` precisa enxergar as tabelas novas. O desenho é o mesmo das tabelas atuais: RLS sem policy e `REVOKE` de `PUBLIC`, `anon` e `authenticated`. O dono e o `service_role` continuam passando. Um papel sem privilégio vê a mesma advertência, agora com os nomes ausentes ou invisíveis.
- Enquanto a reclassificação não for aplicada, o módulo abre, mas a geração RPPS não tem alvo. Isso preserva o isolamento.
- Vínculo adicional entre especialidades da mesma classe processual continua sendo ato explícito e justificado. A pesquisa não amplia o escopo sozinha.

## Acervo ativo separado do legado — 14/10/2026

Decisão posterior: as 24 especialidades já estão no Supabase e o módulo abre no Cloud. Os 10 processos antigos não serão reclassificados. O acervo jurídico recomeça vazio.

A separação é a coluna `minutas_referencias.acervo` (`ativo` ou `legado`), aplicada só pelo SQL manual `database/minutas_acervo_ativo.sql`. Esse script não foi executado. Enquanto a coluna não existir, as consultas permanecem as atuais e a tela de adicionar documentos avisa para não recadastrar número já existente: o arquivo iria para o registro histórico.

Depois do SQL, as linhas atuais ficam `legado` e somem da consulta, da pesquisa, do RAG e da geração. O número de processo e o hash do PDF podem existir de novo no acervo `ativo`, uma vez em cada acervo. Dentro do ativo, a unicidade e o bloqueio de cópia permanecem. Nada é apagado e os PDFs antigos não são movidos. A leitura do legado fica em Administração, somente com a coluna presente.

## Multiprocurador — reversão sem perda de acervo

A entrada institucional pede o Procurador antes das especialidades. O catálogo técnico continua único, com os identificadores `elvira.*` já persistidos. O isolamento do acervo usa `procuradora_chave`. ESPO conserva a chave histórica `elvira samara`.

Para voltar temporariamente à entrada direta do piloto, sem apagar os sete perfis nem os pareceres: definir `MINUTAS_NAV_LEGADA` como verdadeiro nos Secrets. Isso só recoloca a navegação em ESPO. Não é rollback de banco.

O script `database/minutas_procuradores.sql` é aditivo. A coluna `procurador_sigla` só é preenchida quando `procuradora_chave` já é `elvira samara` e a sigla ainda está nula. Chaves ambíguas permanecem sem sigla. Não usar `DROP`, `TRUNCATE` nem reversão que apague linhas criadas depois da migração.
