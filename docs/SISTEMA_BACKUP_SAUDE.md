# Saúde e backup administrativo — Ferramentas MPC-PB

Portal Integrado de Gestão e Apoio Operacional do Ministério Público de Contas da Paraíba.

Área **Administração → Sistema**, com as subseções **Saúde** e **Backup**. Somente usuários com permissão administrativa (`pode_admin` / perfil administrador). A proteção vale na interface e nos serviços (`require_permission` / `has_permission`).

Este recurso aumenta a capacidade de recuperação da aplicação. **Não substitui** uma política institucional de backup do banco e da infraestrutura (PostgreSQL/Supabase, PITR, snapshots do provedor).

## Saúde

Página operacional, não um painel técnico. Indicadores **OK**, **ATENÇÃO** e **ERRO** aparecem em texto (e também com ênfase visual).

| Verificação | O que responde |
|---|---|
| Banco de dados | Conexão (`SELECT 1`), engine (PostgreSQL ou SQLite), ambiente (local / Streamlit Cloud, quando identificável com segurança), latência aproximada. Sem `DATABASE_URL`, host completo, usuário, senha ou connection string. |
| Schema | Marcadores em `configuracoes`, `user_version` (SQLite) ou `schema_migrations` (PostgreSQL), tabelas essenciais. **Não executa migration** ao abrir a página. Ausência de tabela/coluna: **ATENÇÃO** e o nome do componente (ex.: `auditoria_eventos`). |
| Auditoria | Tabela `auditoria_eventos`, último evento, volume em 24 h, erros operacionais. Sem eventos: **ATENÇÃO**, não falha grave. |
| Documentos | Disponibilidade leve de geração DOCX e do conversor PDF já usado pelo projeto (LibreOffice / Word). Não gera arquivo ao abrir a tela. Conversor ausente: **ATENÇÃO**. |
| Aplicação | Python, Streamlit, versão do app, build Git (8 caracteres) se disponível. Ausência de Git **não** é erro. Horário em America/Recife. |
| Atividade | Último acesso, último documento finalizado, última alteração administrativa e última atividade — a partir da auditoria. |
| Erros recentes | Resumo de `ERRO_OPERACIONAL` (24 h / 7 dias), com atalho para Acessos e Auditoria. |

O diagnóstico inicial roda ao abrir **Saúde**. Não se repete a cada rerun: use **Atualizar diagnóstico**. Consultas são leves e só ocorrem com a seção aberta.

### Tabelas essenciais

O inventário em `database/inventory.py` replica os nomes criados em `database/store.py`, `postgresql_schema.sql`, access, audit, memorandos, agenda e ofícios. Agenda e Ofícios são aditivos: se o módulo nunca foi aberto, a Saúde pode apontar **ATENÇÃO** até as tabelas existirem.

## Backup

Geração **manual**. Não há restauração, agendamento, S3, pasta pública nem URL. O ZIP é temporário para download nesta sessão.

Arquivo típico: `backup_ferramentas_mpcpb_2026-09-14_0945.zip`

```
manifest.json
README.txt
dados/*.csv
documentos/portarias/
documentos/memorandos/
documentos/oficios/
documentos/oficios_quarentena/
schema/schema_manifest.json
schema/documentos.json
```

CSV em UTF-8. BLOBs (DOCX/PDF de Portarias, arquivos de memorandos e ofícios, quarentena) saem como arquivos binários originais, com SHA-256 em `schema/documentos.json` e colunas `*_arquivo` / `*_sha256` no CSV. Nada é reconvertido.

**Não entra no ZIP:** `secrets.toml`, `.env`, `DATABASE_URL`, OAuth, cookies, código-fonte, cache, temporários, `backup_snapshots` (dumps internos do backup PostgreSQL de exclusão administrativa — evitaria recursão e misturaria formato nativo).

A tabela `backup_snapshots` permanece no banco PostgreSQL para o fluxo já existente de exclusão de Portarias; o backup lógico não a duplica.

### Consistência

SQLite: transação de leitura (`BEGIN`) na conexão do `Store`. PostgreSQL: transação `REPEATABLE READ` somente leitura, sem advisory lock de escrita, com `statement_timeout` de 300s nesta operação (o restante do portal permanece em 60s). Se a operação for longa demais, ela falha de forma segura, sem ZIP parcial.

### Auditoria

- `BACKUP_GERADO` (módulo `admin`): tabelas, registros, documentos, tamanho, SHA-256 do ZIP.
- `BACKUP_DISPONIBILIZADO`: quando o botão de download é apresentado (não há evento confiável de clique de download no Streamlit).

### Segurança

Somente administrador. `generate_backup` recusa os demais. Sem endpoint público. A interface avisa que o conteúdo é institucional.

### O que este backup não é

É um **backup lógico da aplicação**, restaurável no futuro de forma independente do engine (SQLite ou PostgreSQL), por procedimento técnico ainda não implementado.

Não é `pg_dump`, não é backup do projeto Supabase e não garante ponto-no-tempo da infraestrutura. Informações exclusivas do PostgreSQL (por exemplo o BYTEA dos snapshots internos de exclusão) não seguem neste ZIP.

**Não implemente restauração nesta fase.**
