# Ferramentas MPC-PB

Portal institucional em Python 3.12 e Streamlit para apoiar rotinas do Ministério
Público de Contas da Paraíba. A aplicação reúne autenticação Google, autorização
por módulo e gabinete, auditoria, temas por usuário e persistência em SQLite ou
PostgreSQL/Supabase.

## Módulos

- **Portarias:** elaboração, numeração, finalização, DOCX/PDF e histórico.
- **Agenda e Afastamentos:** eventos, reuniões, despachos, afastamentos e
  substituições.
- **Ofícios:** geração, registro, documentos, movimentações e acompanhamento de
  enviados e recebidos.
- **Memorandos:** preparação e controle de memorandos de substituição.
- **Representações e Petições:** elaboração, protocolo e acompanhamento interno.
- **Ouvidoria:** notícias de fato, triagem, providências e encaminhamentos.
- **Tarefas:** demandas pessoais, prazos, prioridades, checklists e lembretes.
- **Relatórios e Indicadores:** produção, estoque processual e relatórios
  institucionais.
- **Administração:** usuários, permissões, auditoria, saúde e Backup V2.

A Home e a Busca Global integram o acesso aos módulos. A interface só exibe as
áreas autorizadas ao usuário; as operações sensíveis também validam permissão na
camada de serviço.

## Arquitetura

- `app.py`: ponto de entrada e telas do módulo Portarias.
- `portal.py`: autenticação, shell, temas, navegação e carregamento dos módulos.
- `services/`: regras de negócio e componentes de interface por domínio.
- `database/`: Store comum, SQLite, adaptador PostgreSQL, schemas e inventário.
- `document_generator/`: geração de DOCX/PDF e cabeçalhos institucionais.
- `templates/` e `assets/`: pacotes e arte institucional.
- `tests/`: regras, persistência e fluxos Streamlit com dados sintéticos.
- `docs/`: operação, arquitetura, validação e padrões documentais.
- `referencias/`: originais imutáveis usados como referência visual.

`Store()` usa `DATABASE_URL` quando ela existe e, caso contrário, abre SQLite em
`data/mpc.db` (ou o caminho explícito de `MPC_DB_PATH`). A URL configurada e
inválida causa erro; não há fallback silencioso. O schema PostgreSQL da aplicação
é privado e as migrations são aditivas e idempotentes. Seeds não sobrescrevem
configurações nem dados existentes.

O Google OIDC autentica a identidade. A tabela interna de usuários autoriza o
e-mail, os módulos e os gabinetes; não há senha da aplicação nem uso de Supabase
Auth. Dados de sessão e widgets pertencem à sessão Streamlit do usuário, e chaves
de formulários por registro evitam contaminação entre edições.

## Instalação e execução local

No PowerShell, a partir da raiz do repositório:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

`Iniciar_MPC.cmd` inicia um ambiente já instalado. No Linux/macOS, use os
equivalentes `python3 -m venv` e `.venv/bin/python`. A conversão PDF usa Microsoft
Word no Windows ou LibreOffice quando disponível; sem conversor, o DOCX continua
disponível e a interface informa a limitação.

### Configuração de desenvolvimento

Copie `.streamlit/secrets.example.toml` para o arquivo local ignorado
`.streamlit/secrets.toml` e preencha apenas no ambiente de desenvolvimento. Nunca
versione Client Secrets, tokens, chaves, URLs de banco ou credenciais SMTP. Sem
`DATABASE_URL`, o desenvolvimento usa SQLite local.

Não aponte testes para produção. As fixtures removem conexões herdadas e usam
bancos temporários. A integração PostgreSQL exige a variável exclusiva de teste,
um banco descartável em loopback e o marcador de proteção descrito em
[`docs/POSTGRESQL_SUPABASE.md`](docs/POSTGRESQL_SUPABASE.md).

## Testes

Escolha o menor conjunto compatível com o risco. Exemplos:

```powershell
# Navegação, filtros e estado entre módulos
.\.venv\Scripts\python.exe -m pytest -q tests\test_portal_navigation_reset.py

# Temas e componentes visuais compartilhados
.\.venv\Scripts\python.exe -m pytest -q tests\test_ui_theme.py tests\test_themes.py

# Permissões de ações institucionais
.\.venv\Scripts\python.exe -m pytest -q tests\test_external_action_permissions.py
```

O workflow `targeted-regressions.yml` executa em pull requests um recorte SQLite
sem Secrets ou conexão externa. A validação PostgreSQL do Backup V2 permanece em
workflow manual próprio e usa apenas um serviço descartável protegido.

Mudanças nos geradores exigem renderizar as Portarias 5, 6 e 8/2026 e comparar
todas as páginas com `referencias/`. Não declare validação PDF quando o conversor
não estiver disponível.

## Publicação e versionamento

A versão oficial fica em `services/versioning.py` e segue SemVer. Uma entrega
deve registrar mudanças em `CHANGELOG.md`, executar os testes proporcionais ao
risco e só então receber commit/tag por solicitação explícita. Configurações e
Secrets de publicação permanecem no ambiente de hospedagem, nunca no Git.

Consulte [`docs/VERSIONAMENTO.md`](docs/VERSIONAMENTO.md) e
[`docs/ACESSO.md`](docs/ACESSO.md). O repositório não deve conter bancos de
execução, logs, ambientes virtuais, exportações ou documentos institucionais
emitidos.

## Backup e recuperação

O Backup V2 produz pacote lógico tipado, documentos originais, manifesto e hashes.
A validação e a restauração foram exercitadas apenas em destinos sintéticos e
isolados. Nenhuma rotina substitui automaticamente o banco de produção.

A guarda diária manual no servidor institucional está prevista como procedimento
operacional. Armazenamento externo automatizado, monitoramento e alertas permanecem
inativos até configuração e aprovação da infraestrutura. Download no Streamlit
ou arquivo temporário não comprova cópia durável.

SQLite pode ser reconstruído em banco novo isolado. PostgreSQL possui restauração
de teste restrita a ambiente descartável protegido; recuperação operacional deve
seguir autorização, backup nativo, validação segregada e plano de reversão. O
mecanismo, o inventário e os limites estão em
[`docs/SISTEMA_BACKUP_SAUDE.md`](docs/SISTEMA_BACKUP_SAUDE.md).

## Documentos técnicos

- [Autenticação e autorização](docs/ACESSO.md)
- [SQLite e PostgreSQL/Supabase](docs/POSTGRESQL_SUPABASE.md)
- [Backup V2, recuperação e saúde](docs/SISTEMA_BACKUP_SAUDE.md)
- [Exclusão administrativa](docs/EXCLUSAO_ADMINISTRATIVA.md)
- [Padrão institucional de documentos](docs/PADRAO_INSTITUCIONAL.md)
- [Validação documental](docs/VALIDACAO.md)
- [Alertas internos](docs/ALERTAS_INTERNOS.md)
