# Implantação experimental no Cloud Run

Esta preparação é opcional e não muda a implantação existente no Streamlit
Community Cloud. A imagem inicia `portal.py`, preserva os templates, assets,
referências e o conversor PDF com LibreOffice. Não use SQLite no Cloud Run:
o sistema de arquivos de uma instância é temporário e desaparece em reinícios
ou troca de instância. Para homologação, configure o PostgreSQL/Supabase já
previsto pela aplicação por meio de `DATABASE_URL`.

## Pré-requisitos e decisões humanas

1. Escolha o projeto, região e um domínio HTTPS novo para a homologação.
   Não reutilize nem substitua a URL do Streamlit Community Cloud.
2. No provedor Google OAuth, acrescente **sem remover a atual** a URI
   `https://<novo-dominio>/oauth2callback` (ou a URL inicial do Cloud Run
   seguida de `/oauth2callback`). O valor deve ser idêntico ao configurado
   em `[auth].redirect_uri`.
3. Crie ou aproveite uma conta de serviço exclusiva do Cloud Run. Conceda a
   ela somente `Secret Manager Secret Accessor` nos segredos que usará. O
   operador que faz a implantação precisa de Cloud Run Admin e Service Account
   User; isso não deve ser concedido à identidade da aplicação.
4. Confirme que o projeto e a rede do Supabase aceitam conexões originadas do
   Cloud Run. A aplicação não deve iniciar sem `DATABASE_URL` nessa modalidade.

## Segredos

Crie no Secret Manager um segredo de texto com o conteúdo TOML equivalente ao
arquivo `.streamlit/secrets.toml` usado hoje. Ele deve conter pelo menos
`[auth]` (incluindo o novo `redirect_uri`) e `DATABASE_URL`; inclua somente as
seções de e-mail e chaves opcionais que já estejam realmente em uso.

No Console do Cloud Run, em **Volumes**, monte esse segredo como o arquivo
`/app/.streamlit/secrets.toml`. Monte um arquivo, e não o diretório
`.streamlit`, para preservar o `config.toml` que já vem na imagem. Segredos que
o código já lê por variável de ambiente podem ser adicionados em **Variables &
Secrets → Reference a secret**; fixe uma versão para variáveis de ambiente.
Nunca informe esses valores como variáveis comuns, argumentos de build, Docker
build args ou conteúdo versionado.

## Roteiro pelo Google Cloud Console

1. Habilite Cloud Run, Cloud Build, Artifact Registry e Secret Manager no
   projeto escolhido. Crie um repositório Docker no Artifact Registry.
2. Faça o build da imagem a partir deste diretório usando o Dockerfile e envie-a
   ao Artifact Registry. O `.dockerignore` exclui bases locais, exportações,
   logs e credenciais do contexto de build.
3. Em **Cloud Run → Create service**, selecione a imagem e a região definida.
   A imagem usa `PORT` fornecida pelo Cloud Run; não defina uma porta fixa no
   serviço.
4. Em **Container(s), Volumes, Networking, Security**, configure inicialmente:
   - CPU: 1 vCPU; memória: 1 GiB.
   - Request timeout: 60 minutos.
   - Concurrency: 1 para a homologação. Isso é conservador para Streamlit,
     sessões WebSocket e a conversão local por LibreOffice.
   - Autoscaling: mínimo 0 e máximo 1 instância.
   - Session affinity: ativada como melhor esforço, sem depender dela para
     consistência.
   - Ingress público e **Allow unauthenticated invocations** somente para que
     usuários alcancem a tela de login e o callback OIDC. A autorização continua
     sendo feita pelo Google OIDC e pelas regras internas da aplicação.
5. Monte o segredo TOML e configure a conta de serviço exclusiva. Implante sem
   alterar a revisão do Community Cloud.
6. Abra a URL nova em uma janela anônima: valide a tela de login, o callback
   OAuth com uma conta autorizada, acesso a dados, geração DOCX e uma conversão
   PDF. Não use dados institucionais reais para o primeiro teste sem autorização
   administrativa.

## Operação, limites e custos

- WebSockets são solicitações de longa duração: o timeout de 60 minutos encerra
  a conexão nesse limite, portanto o navegador precisa reconectar. Afinidade de
  sessão é apenas melhor esforço.
- Uma conexão WebSocket mantém uma instância ativa e pode gerar cobrança. Com
  mínimo zero, há cold start; com máximo um, uma sessão ou conversão lenta pode
  fazer as demais solicitações aguardarem ou falharem. Não aumente concorrência
  antes de observar CPU, memória, latência e estabilidade das sessões.
- A instância pode reiniciar a qualquer momento. `/tmp`, `data/`, `exports/` e
  logs locais não são persistentes nem compartilhados. Downloads devem vir dos
  bytes da sessão ou do banco; arquivos que precisem sobreviver exigem decisão
  explícita de armazenamento externo.
- O bloqueio de conversão PDF existente vale apenas dentro da única instância.
  O máximo de uma instância reduz esse risco na homologação; para escalar no
  futuro, reavalie coordenação de conversão, sessões e persistência externa.
- O Supabase deve suportar a carga e os limites de pool. Monitore erros de
  conexão, esgotamento de pool e migrações; não exponha a URL do banco em logs.

## Referências oficiais

- [WebSockets no Cloud Run](https://cloud.google.com/run/docs/triggering/websockets)
- [Segredos em instâncias Cloud Run](https://cloud.google.com/run/docs/configuring/instances/secrets)
- [Configurações de serviços Cloud Run](https://cloud.google.com/run/docs/configuring)
