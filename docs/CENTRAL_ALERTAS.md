# Central de Alertas

Alertas são projeções autorizadas dos módulos de origem; não existe cadastro
paralelo de tarefas, ofícios, representações ou compromissos. A condição deixa
de aparecer assim que deixa de ser ativa no módulo correspondente.

## Estado individual

`alertas_atencao` guarda somente `usuario_id`, uma chave lógica estável,
`lido_em` e `adiado_ate`. A chave deriva de módulo de origem, identificador e
categoria do evento, nunca do texto exibido. Há índice por usuário e horário de
adiamento; a consulta busca todos os estados da página em uma única operação.

Lido retira o item do contador, mas não o resolve. Adiado fica fora do sino até
`adiado_ate`; a própria consulta o faz reaparecer, sem jobs em segundo plano.
O Histórico atual mostra alertas ainda deriváveis e já lidos. Não são guardados
snapshots de registros resolvidos nesta fase.

## Migração funcional

Da antiga Central, prazos de Ofícios, compromissos da Agenda e substituições de
Memorandos já eram a fonte dos alertas e continuam assim. Tarefas próximas,
lembretes/atualizações seguidos, viagens e solicitações administrativas já são
gerados diretamente. Representações e Ouvidoria eram itens de listagem ampla,
mas não geram alerta automático nesta fase para evitar ruído sem uma regra de
prazo ou providência específica.

Permissões de módulo e gabinete continuam sendo aplicadas antes da projeção. A
Central apenas reutiliza resultados autorizados e suas ações de abrir revalidam
a origem.
