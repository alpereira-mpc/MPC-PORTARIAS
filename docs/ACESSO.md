# Autenticação e autorização — Ferramentas MPC-PB

O Google autentica a identidade. O banco da aplicação (SQLite local ou PostgreSQL/Supabase) autoriza o e-mail e as permissões. Não há senha no sistema. Não use Supabase Auth.

A versão do Streamlit do projeto (`1.49.1`) já oferece `st.login()`, `st.user` e `st.logout()`. Não foi necessário atualizar o Streamlit.

## A. Google Cloud OAuth

1. Google Cloud Console → APIs e serviços → Credenciais.
2. Tela de consentimento OAuth (tipo Interno, se a organização permitir; senão Externo restrito aos e-mails autorizados).
3. Criar credenciais → **ID do cliente OAuth** → tipo **Aplicativo da Web**.
4. Escopos: `openid`, `email`, `profile`.
5. Anotar Client ID e Client Secret. Não versionar.

## B. Redirect URI local

Em Clientes OAuth → URIs de redirecionamento autorizados:

`http://localhost:8501/oauth2callback`

## C. Redirect URI Streamlit Cloud

`https://ferramentasmpcpb.streamlit.app/oauth2callback`

Use a mesma origem do app (com `https` e sem barra no final, com o caminho `/oauth2callback`).

## D. Secrets locais

Copie `.streamlit/secrets.example.toml` para `.streamlit/secrets.toml` (já ignorado pelo Git).

Preencha:

- `redirect_uri` local
- `cookie_secret` (string longa aleatória)
- `client_id` e `client_secret` do Google
- `server_metadata_url` como no exemplo

`DATABASE_URL` continua só no ambiente de produção PostgreSQL, se já usado.

## E. Secrets no Streamlit Community Cloud

App → Settings → Secrets. Inclua o bloco `[auth]` com:

- `redirect_uri = "https://ferramentasmpcpb.streamlit.app/oauth2callback"`
- o mesmo `cookie_secret` estável (não regenere a cada deploy sem necessidade)
- Client ID e Client Secret
- `server_metadata_url` do Google
- o `DATABASE_URL` PostgreSQL/Supabase já utilizado

## F. Primeiro administrador (Supabase)

Não existe usuário padrão no código.

1. Suba o app ou rode-o uma vez após o Google autenticar qualquer conta (mesmo que o acesso seja negado). Isso cria as tabelas no schema da aplicação (`mpc_portarias` no PostgreSQL).
2. Table Editor → schema **`mpc_portarias`** (não `public`) → tabela **`usuarios_acesso`**.
3. Inserir uma linha:

| campo | valor |
|---|---|
| nome | nome da pessoa administradora |
| email | e-mail Google em minúsculas |
| perfil | `ADMINISTRADOR` |
| ativo | `1` |
| pode_portarias | `1` |
| pode_agenda | `1` |
| pode_oficios | `1` |
| pode_admin | `1` |
| criado_em | data/hora ISO UTC, ex. `2026-09-10T12:00:00+00:00` |
| atualizado_em | o mesmo |

4. Opcional: em **`usuario_gabinetes`**, um registro por gabinete (`PROGE`, `SBBQ`, `IBMF`, `MTFF`, `BTLC`, `LAF`, `MASN`) com o `usuario_id` criado. Administrador recebe todos os gabinetes automaticamente ao ser salvo pela tela Administração; na inserção manual, inclua os gabinetes ou salve o usuário de novo pela Administração após o primeiro login.

## G. Teste local

`.\.venv\Scripts\python.exe -m streamlit run app.py`

1. Sem login → tela “Acesso restrito”.
2. Entrar com Google com e-mail não cadastrado → acesso negado + Sair.
3. E-mail do administrador → Home.
4. Sair → volta ao login.

## H. Teste em produção

Confirme Redirect URI e Secrets. Repita os cenários G. Depois: usuário só com Agenda; usuário Ofícios só PROGE; desativar `ativo=0` e verificar bloqueio.

## Tabelas

Migration aditiva, idempotente, marcador `acesso_schema_v1` em `configuracoes`:

- `usuarios_acesso`
- `usuario_gabinetes` (PK `usuario_id` + `gabinete`)

E-mail é gravado em minúsculas e é único. Não há senha, token Google nem sessão OAuth no banco.
