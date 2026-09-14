# Alertas internos

Visão reduzida do que exige atenção imediata no Ferramentas MPC-PB. **Não há tabela própria de alertas** e não há cópia dos registros. A fonte da verdade permanece em Ofícios, Agenda, Memorandos e, para o administrador, nos sinais leves de Saúde/Auditoria.

## Objetivo

Destacar somente situações que realmente merecem atenção agora ou nas próximas horas/dias curtos. Os alertas desaparecem sozinhos quando a condição deixa de existir no módulo de origem (por exemplo, ofício vencido que passa a `Respondido`/`Concluído`).

Esta versão **não envia e-mail**, não dispara push e não mantém estado lido/dispensado.

## Alertas × Pendências

| | Central de Pendências | Alertas |
|---|---|---|
| Papel | Tudo que está ativo e exige acompanhamento | Subconjunto urgente |
| Ofícios | Inclui prazos futuros (PRÓXIMA/FUTURA) | Vencido, hoje, +1 a +3 dias; providência sem prazo (informativo) |
| Agenda | Compromissos futuros até o horizonte da Central | Hoje, próximas 2 h, próximas 24 h |
| Memorandos | AGENDADA/EM ANDAMENTO no horizonte da Central | EM ANDAMENTO, inicia hoje, inicia em até 3 dias |

Itens `FUTURA` e `PRÓXIMA` continuam na Central. Não há cadastro paralelo nem sincronização.

## Fontes

- Ofícios, Agenda e Memorandos via a mesma agregação da Central (`PendingItem`).
- Sistema (somente administrador): ping de banco, schema **se já houver diagnóstico em cache**, conversor PDF e `ERRO_OPERACIONAL` recente. Não se executa o diagnóstico pesado completo a cada acesso.

## Severidades

Ordem: **CRÍTICO** → **ALTO** → **ATENÇÃO** → **INFORMATIVO**. O texto da severidade é sempre escrito por extenso (não só cor). Dentro da mesma faixa, data/hora mais próxima primeiro (vencidos mais antigos primeiro).

### Ofícios

- Prazo vencido → CRÍTICO · “Prazo vencido”
- Prazo hoje → ALTO · “Prazo vence hoje”
- Prazo amanhã até +3 dias → ATENÇÃO · “Prazo próximo”
- Providência pendente sem prazo (já considerada na Central) → INFORMATIVO · “Providência pendente”
- Fora: Respondido, Concluído, Cancelado, Arquivado, Rascunho e demais exclusões da Central. +4 dias ou mais: só na Central.

### Agenda

Não há “vencido”. Compromisso já iniciado (horário passado) não alerta. Um compromisso gera **um** alerta, com a maior severidade aplicável:

- Até 2 horas → CRÍTICO · “Compromisso em breve”
- Hoje (depois disso) → ALTO · “Compromisso hoje”
- Nas próximas 24 horas, mas não hoje → ATENÇÃO · “Compromisso próximo”
- Realizado, Cancelado, passado ou distante: sem alerta.

### Memorandos

- EM ANDAMENTO → ALTO · “Substituição em andamento” (não é vencida)
- AGENDADA hoje → ALTO · “Substituição inicia hoje”
- AGENDADA em até 3 dias → ATENÇÃO · “Substituição próxima”
- ENCERRADA, CANCELADA ou +4 dias: sem alerta.

### Sistema (administrador)

Somente se houver problema real:

- Banco ERRO → CRÍTICO
- Schema ERRO (cache da Saúde) → CRÍTICO; tabelas/colunas faltando → ALTO
- Conversor PDF ausente → ATENÇÃO
- Auditoria indisponível ou `ERRO_OPERACIONAL` nas últimas 24 h → ATENÇÃO

Usuário comum nunca vê alertas técnicos. Saúde OK não gera alerta de sistema.

## Permissões

Derivadas, sem coluna `pode_alertas`:

`administrador` **ou** Ofícios **ou** Agenda **ou** Memorandos.

Gabinetes iguais aos da Central. Filtro de gabinete não amplia autorização.

## Atualização automática e ausência de “lido”

Não há marcar como lido, dispensar, snooze nem ocultar. Isso exigiria persistência por usuário. O alerta é a condição ativa; ao resolvê-la na origem, some.

## Timezone

America/Recife, as mesmas funções da Central (`today_recife`, `parse_date`, `parse_datetime`, `now_recife`). Datas `dd/mm/aaaa`; data/hora `dd/mm/aaaa HH:MM`. String só-data não desloca o dia via UTC.

## Navegação

Alertas **não** é item do rádio principal. O acesso é o **sininho** no sidebar (abaixo do e-mail, antes de Sair). O popover mostra até 5 alertas e **Ver todos os alertas**.

A tela completa usa `portal_special_view = "alerts"` (pedido `portal_alerts_request` consumido antes do rádio). **← Voltar** restaura o módulo anterior ou Início. Sessões antigas com `portal_module="Alertas"` caem para Início.

Deep-links reutilizam `request_portal_navigation` / `pending_open_*` (e `pending_open_admin` para Saúde), após limpar o overlay.

## Performance

Metadados apenas (sem BLOBs). O sino usa `get_alert_summary` (top 5 + contagens) com cache de sessão de 20 s. Alertas de sistema só a partir do cache da Saúde — sem diagnóstico, LibreOffice ou schema a cada página. A listagem completa pagina 50 itens e limita 1000 por fonte. Isolamento: falha de Ofícios não derruba Agenda/Memorandos/Sistema.

## Auditoria

`ALERTAS_ACESSADOS` (módulo `alertas`) somente ao abrir a visualização completa (**Ver todos**), uma vez por entrada. Renderizar o sino ou abrir o popover **não** gera evento. Falha de fonte: `ERRO_OPERACIONAL` sanitizado.

## Privacidade

Títulos e contexto curtos. Corpo de ofício, anexos e observações longas ficam no módulo de origem.

## Limitações e o que não entra agora

Sem e-mail, WhatsApp, push, SMS, scheduler, notificações do navegador, tabela de notificações, IA, alterar status pelo alerta, busca global. Volume acima do limite da Central é truncado na origem.

Possibilidades futuras: e-mail opt-in, estado lido persistido, janelas configuráveis por gabinete.
