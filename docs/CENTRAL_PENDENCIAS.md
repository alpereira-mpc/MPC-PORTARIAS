# Central de Pendências

Visão transversal do que exige atenção nos módulos já existentes. **Não há tabela própria de pendências** e não há cópia dos registros. A fonte da verdade permanece em Ofícios, Agenda e Memorandos.

## Objetivo

Reunir, em uma tela, itens operacionais pendentes, com filtros por período, módulo, gabinete e urgência, e navegação de volta ao módulo de origem. A Central não altera status, não marca item como resolvido e não envia notificações.

## Fontes e inclusão

| Módulo | Inclusão | Exclusão |
|---|---|---|
| **Ofícios** | Recebidos com prazo aberto ou vencido, ou status `Recebido` / `Em análise` / `Aguardando providência` / `Encaminhado`. Enviados em acompanhamento (`Gerado`, `Enviado`, `Aguardando resposta`). | `Respondido`, `Concluído`, `Cancelado`, `Arquivado`, `Rascunho`. |
| **Agenda** | Compromissos `Agendado` ou `Confirmado` com início ≥ hoje (America/Recife). | `Cancelado`, `Realizado`, compromissos com início anterior a hoje. |
| **Memorandos** | Situação calculada `AGENDADA` ou `EM ANDAMENTO` (`data_fim` ≥ hoje e status documental ≠ `CANCELADO`). | `ENCERRADA` (`data_fim` < hoje), `CANCELADA`. |

Consultas usam colunas de metadados (`status`, datas, série, assunto). Não leem BLOBs (`conteudo`, `docx`, `pdf`) nem `auditoria_eventos`.

## Portarias

**Fora da primeira versão.** Existe `status='Rascunho'`, mas não há prazo operacional confiável. Rascunhos não foram tratados como pendência de ação nesta etapa.

## Urgência (America/Recife)

Datas `YYYY-MM-DD` são datas de calendário, sem deslocar para o dia anterior via UTC.

**Ofícios (prazo):** `< hoje` VENCIDA; `= hoje` HOJE; +1 a +3 URGENTE; +4 a +7 PRÓXIMA; `> 7` FUTURA; sem prazo SEM PRAZO.

**Agenda (início):** hoje HOJE; +1 a +3 URGENTE; +4 a +7 PRÓXIMA; depois FUTURA. Passado não entra.

**Memorandos:** `AGENDADA` segue a data de início; `EM ANDAMENTO` é HOJE (não VENCIDA só porque o início já passou).

Ordem da listagem: VENCIDA → HOJE → URGENTE → PRÓXIMA → FUTURA → SEM PRAZO; depois a data mais próxima.

## Permissões

O item de menu **Pendências** aparece se o usuário tem Ofícios, Agenda ou Memorandos. Administrador vê todos os gabinetes (`GABINETES`). Usuário de Ofícios só vê os gabinetes autorizados (série / destinatários / procurador do memorando / participantes da agenda). Filtro de gabinete não amplia a autorização: gabinete alheio devolve lista vazia. Sem Ofícios, não há itens de Ofícios.

## Navegação

- Ofícios: seleciona gabinete, página Recebidos/Acompanhamento e tenta abrir o detalhe.
- Agenda: abre o editor do compromisso (`AgendaStore.get`).
- Memorandos: entra em **Em andamento**.

Navegação programática (Home **Ver pendências** e **Ver em…**) não altera `portal_module` nem outras chaves de widget depois que o rádio já foi criado no mesmo ciclo. Usa `portal_navigation_request` e chaves transitórias (`pending_open_oficio`, `pending_open_agenda`, `pending_open_memorando`), consumidas no início do módulo de destino.

A ação continua no módulo original.

## Home

Resumo com três contagens (vencidas, hoje, próximos 3 dias) e **Ver pendências**. Só consulta após login, com o `Store` já aberto. Se as tabelas de Ofícios/Agenda ainda não existirem, a fonte é omitida (sem criar schema). Falha no resumo não derruba a Home.

## Performance

Filtros SQL por status/data/gabinete, `LIMIT 1000` por fonte, listagem paginada (50). Cards usam o conjunto já filtrado. Sem pooling extra.

## Auditoria

`CENTRAL_PENDENCIAS_ACESSADA` (módulo `pendencias`), uma vez por entrada na sessão, no mesmo mecanismo de `MODULO_ACESSADO`. Falha de fonte: `ERRO_OPERACIONAL` sanitizado.

## Limitações

Sem e-mail, scheduler, tabela de tarefas, restore de status na Central, IA ou busca global. Volume acima de 1000 itens por módulo é truncado na consulta. Isolamento: se Ofícios falhar, Agenda e Memorandos seguem visíveis.
