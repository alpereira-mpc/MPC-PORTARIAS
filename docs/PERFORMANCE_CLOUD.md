# Medição de desempenho no Streamlit Cloud

Esta instrumentação é desativada por padrão. Para uma coleta temporária, adicione
ao Secret do Streamlit Cloud, no nível raiz:

```toml
MPC_PERF_LOG = "1"
```

Reinicie o app após salvar o Secret. Cada rerun do Portal ou de um fragmento de
módulo passa a emitir uma linha `PERF` no log do app. Não há tabela, arquivo,
chamada externa ou registro de auditoria adicional.

Exemplo de formato:

```text
PERF | module=Agenda | total_ms=412.3 | db_queries=4 | db_ms=173.8 | db_max_ms=81.4 | round_trips=8 | pool_wait_ms=2.1 | portal_authorization_ms=22.4 | sidebar_bell_ms=31.2 | module_render_ms=279.8 | db_ops=AgendaStore.active_current_and_upcoming:1/84.0ms
```

`db_ms` é o tempo observado pela aplicação dentro das chamadas PostgreSQL; ele
inclui processamento do banco e a latência de rede. `pool_wait_ms` mede apenas a
espera para obter uma conexão. SQL, parâmetros, conteúdo de documentos, nomes,
e-mails, tokens e segredos não são registrados.

Sequência sugerida: Home, Agenda, Home, Ofícios, Representações, Petições,
Tarefas, Memorandos, Busca Global, sino e uma segunda abertura de alguns módulos.
Anote a hora aproximada de cada ação e compartilhe somente as linhas `PERF`.

Para desativar, remova o Secret ou defina `MPC_PERF_LOG = "0"` e reinicie o app.
