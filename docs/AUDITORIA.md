# Acessos e Auditoria — Ferramentas MPC-PB

Rastreamento institucional de sessões, módulos e ações relevantes. Não substitui a autenticação Google OIDC nem a tabela `usuarios_acesso`.

## Schema

Migration aditiva e idempotente, marcador `auditoria_schema_v1` em `configuracoes`.

Tabela `auditoria_eventos`:

| campo | uso |
|---|---|
| id | identidade |
| usuario_id | quando conhecido |
| usuario_email / usuario_nome | denormalizados; sobrevivem à exclusão do cadastro |
| sessao_id | sessão Streamlit (`st.session_state`), não rerun |
| evento / modulo / acao | classificação |
| entidade_tipo / entidade_id | identificadores de documento, usuário ou compromisso |
| resultado | OK, NEGADO, ERRO |
| detalhes_json | metadados sanitizados |
| criado_em | ISO UTC (`+00:00`); a UI converte para America/Recife (`dd/mm/aaaa HH:MM:SS`) |

Não há chave estrangeira para `usuarios_acesso` (logs não são apagados com o usuário). Não há botão de editar ou excluir log.

## Retenção

Nesta versão os registros não são apagados automaticamente. Uma política futura (por exemplo 12 meses, 24 meses ou prazo institucional) pode ser aplicada por rotina administrativa explícita. Não implementar limpeza sem definição expressa.

## Privacidade

Não se grava senha, token OAuth, cookie, secret, conteúdo integral de documentos/e-mails nem bytes de arquivos. `detalhes_json` aceita metadados como número/gabinete ou contagens de importação.

## Performance

Inserts em transação curta e isolada da finalização documental. Índices em `criado_em`, `usuario_id`, `usuario_email`, `modulo`, `evento`, `acao`, `resultado` e `sessao_id`. Dashboards só consultam quando a seção está aberta. Home sem login não abre o banco. Falha de auditoria é logada tecnicamente e não impede Portaria, Ofício ou Memorando.
