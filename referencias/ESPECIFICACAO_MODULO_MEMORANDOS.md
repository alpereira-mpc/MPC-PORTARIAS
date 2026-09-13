# ESPECIFICAÇÃO — MÓDULO MEMORANDOS — FERRAMENTAS MPC-PB

Quero implementar agora, em UMA ÚNICA TAREFA, o novo módulo MEMORANDOS do portal Ferramentas MPC-PB.

O projeto atual está funcional e estável em produção. Antes de alterar qualquer coisa, inspecione a arquitetura existente e reutilize os padrões já consolidados de Store, PostgreSQL/SQLite, autorização, documentos, PDF, cache, lazy loading, UI e testes.

Não quero uma implementação paralela desconectada do restante do portal.

## 1. OBJETIVO DO MÓDULO

Nesta primeira versão, o módulo Memorandos gerará exclusivamente:

MEMORANDOS DE SUBSTITUIÇÃO DE SERVIDORES

Futuramente haverá outros tipos de memorando, como solicitações de Procuradores para participação em eventos. Portanto, a arquitetura deve nascer extensível, mas NÃO implemente agora tipos que não sejam SUBSTITUIÇÃO.

O sistema NÃO terá numeração própria de memorandos.

O PDF gerado será posteriormente inserido no sistema oficial de memorandos do TCE-PB, onde receberá a numeração oficial.

Depois disso, o usuário poderá voltar ao histórico do Ferramentas MPC-PB e registrar o número oficial recebido no sistema do TCE-PB.

Esse número posterior é apenas metadado do histórico e NÃO pode modificar o PDF já finalizado.

## 2. REFERÊNCIAS DOCUMENTAIS

Use como referências principais os documentos disponíveis nesta tarefa e todos os modelos pertinentes existentes em:

referencias/

ou, se for mais organizado:

referencias/memorandos/

Esses documentos são REFERÊNCIAS DE DESENVOLVIMENTO.

Não implemente leitura dinâmica desses arquivos em produção.

O gerador deve trabalhar com um modelo controlado e versionado do projeto.

Se os modelos apresentarem inconsistências entre si, prevalecem as regras explícitas desta especificação.

IMPORTANTE:

Todos os memorandos novos devem ser padronizados com o título:

MEMORANDO

centralizado, seguindo visualmente o modelo institucional.

Reproduza cuidadosamente:
- logo institucional;
- papel A4;
- margens;
- título MEMORANDO;
- destinatário;
- assunto;
- vocativo;
- corpo justificado;
- espaçamentos;
- destaques tipográficos;
- fechamento;
- assinatura.

Não copie erros gramaticais ou inconsistências eventualmente existentes nos documentos antigos.

## 3. DESTINATÁRIO

Nesta versão, o destinatário será sempre:

Ao Excelentíssimo Senhor Presidente do Tribunal de Contas do Estado da Paraíba

Vocativo:

Senhor Presidente,

Não é necessário cadastrar destinatários.

## 4. SIGNATÁRIO

O signatário padrão deve ser:

ELVIRA SAMARA PEREIRA DE OLIVEIRA

com o cargo institucional correspondente de Procuradora-Geral.

Mas o formulário deve permitir selecionar qualquer outro Procurador do MPC-PB como signatário.

Use o catálogo institucional de Procuradores já existente no projeto como fonte de verdade.

Não crie uma segunda lista independente se já existir estrutura equivalente.

Ao selecionar o signatário, preencher automaticamente:
- nome;
- cargo institucional atual usado na assinatura.

Exemplos de funções existentes incluem Procuradora-Geral, Subprocurador(a)-Geral, Procurador(a), Ouvidor e Corregedor, conforme o catálogo atualmente utilizado pelo portal.

Armazene no memorando um SNAPSHOT do nome e cargo utilizados na assinatura, para que uma mudança futura de função não altere documentos históricos.

## 5. BASE DE SERVIDORES

A planilha XLSX fornecida contém a base que deverá abastecer a pesquisa de servidores.

Ela possui informações equivalentes a:
- nome;
- matrícula;
- cargo;
- setor/lotação.

NÃO consulte o XLSX a cada uso.

Crie uma tabela própria de servidores no banco da aplicação.

O sistema deve continuar suportando:
- SQLite local;
- PostgreSQL/Supabase em produção.

Não coloque a planilha de servidores nem uma exportação completa de seus dados no Git.

O repositório é público.

A base deve ser carregada para o banco por uma função administrativa de importação.

Não exponha conteúdo integral da planilha em logs.

## 6. ATUALIZAÇÃO DA BASE VIA XLSX — SOMENTE ADMIN

Dentro do módulo Memorandos, crie uma área:

Base de Servidores

visível SOMENTE para administrador.

O administrador deverá poder fazer upload de uma nova planilha XLSX e atualizar a base sem alteração de código.

Fluxo obrigatório:
1. upload;
2. leitura;
3. validação dos cabeçalhos;
4. prévia;
5. relatório do que será alterado;
6. confirmação;
7. importação transacional.

Antes de confirmar, mostrar pelo menos:
- quantidade total de linhas;
- novos servidores;
- servidores atualizados;
- registros sem matrícula;
- matrículas zero;
- duplicidades;
- linhas inconsistentes;
- registros que exigem revisão.

Não sobrescrever a base cegamente.

Para matrícula válida, usar matrícula normalizada como principal identificador de atualização.

Nome NÃO deve ser chave única.

Para matrícula vazia/zero/inválida, não descarte silenciosamente.

Identifique e apresente essas linhas ao administrador.

Adote uma estratégia segura para importá-las sem fundir duas pessoas diferentes.

Não exclua automaticamente um servidor apenas porque ele não apareceu em uma nova planilha.

Se quiser oferecer “desativar ausentes”, deve ser uma ação separada, desmarcada por padrão e com confirmação explícita.

Registrar auditoria da importação:
- usuário;
- data;
- nome do arquivo;
- hash quando viável;
- total processado;
- total incluído;
- total atualizado;
- total com inconsistência.

Não é necessário armazenar permanentemente o XLSX original depois da importação.

## 7. CADASTRO-MESTRE DO SERVIDOR

Estruture a base com campos equivalentes a:
- id interno;
- nome;
- nome normalizado para pesquisa;
- matrícula original;
- matrícula normalizada;
- cargo;
- setor;
- gênero gramatical, quando conhecido;
- ativo;
- criado_em;
- atualizado_em.

A planilha não deve ser presumida como fonte confiável de gênero.

NÃO inferir gênero automaticamente pelo nome.

No formulário do memorando, se o gênero não estiver disponível, exigir:
- Masculino
ou
- Feminino

para que a redação fique gramaticalmente correta.

O valor utilizado deve ser armazenado no snapshot do memorando.

Se for conveniente, permita ao administrador corrigir gênero e dados cadastrais na área Base de Servidores.

## 8. AUTOCOMPLETE DE SERVIDORES

Ao preencher um memorando, quero um campo pesquisável/autocomplete.

Ao começar a digitar o nome, apresentar opções correspondentes.

Também deve ser possível localizar por matrícula.

Exibição sugerida:

NOME — MATRÍCULA — SETOR

Ao selecionar uma pessoa, carregar automaticamente:
- nome;
- matrícula;
- cargo;
- setor/lotação.

Com ~centenas de servidores, isso deve continuar rápido.

Não faça query ao banco a cada caractere se houver alternativa nativa mais eficiente.

Pode usar selectbox pesquisável do Streamlit ou solução equivalente simples e estável.

Cacheie apenas metadados seguros quando útil.

## 9. DADOS AUTOMÁTICOS, MAS EDITÁVEIS NO MEMORANDO

Nome e matrícula identificam a pessoa selecionada.

Cargo e lotação devem ser preenchidos automaticamente pela base, MAS devem permanecer editáveis no formulário do memorando.

Essa edição é permitida ao usuário que estiver elaborando o memorando.

IMPORTANTE:

editar cargo ou lotação dentro do memorando NÃO deve alterar automaticamente o cadastro-mestre do servidor.

O memorando deve guardar os valores como SNAPSHOT.

Isso é necessário porque a base pode registrar cargo efetivo, enquanto o documento pode precisar mencionar cargo comissionado/função atualmente exercida.

## 10. GABINETE DO SERVIDOR SUBSTITUÍDO

O gabinete do servidor substituído NÃO deve ser preenchido automaticamente pelo setor da planilha.

Deve existir um campo obrigatório:

Gabinete do servidor substituído

com os Procuradores atuais do MPC-PB.

O usuário selecionará o Procurador responsável pelo gabinete.

A redação deverá ser produzida assim:

Para Luciano, por exemplo:

“lotado no gabinete do Procurador Luciano Andrade Farias”

ou, para pessoa do gênero feminino:

“lotada no gabinete do Procurador Luciano Andrade Farias”

Para Procuradoras:

“lotado no gabinete da Procuradora [NOME]”

ou:

“lotada no gabinete da Procuradora [NOME]”

REGRA ESPECIAL PARA ELVIRA:

quando o gabinete selecionado for o da Procuradora-Geral Elvira Samara Pereira de Oliveira, NÃO escrever:

“gabinete da Procuradora Elvira...”

Escrever:

“lotado no gabinete da Procuradoria-Geral”

ou:

“lotada no gabinete da Procuradoria-Geral”

Use o catálogo já existente dos Procuradores e não duplique essa informação desnecessariamente.

## 11. LOTAÇÃO DO SUBSTITUTO

Para o substituto, o setor da base pode ser utilizado como sugestão inicial.

O campo continua editável.

Quando houver códigos conhecidos de setor, use apresentação institucional adequada quando isso for seguro.

Exemplos:

1CAM -> Secretaria da 1ª Câmara
2CAM -> Secretaria da 2ª Câmara

Não invente tradução para códigos desconhecidos.

Para código desconhecido, preserve o valor original e deixe o campo editável.

## 12. NATUREZA DA FUNÇÃO

Os modelos antigos usam tanto:

“cargo comissionado”

quanto:

“função de confiança”

Portanto, inclua campo obrigatório:

Natureza da função

Opções iniciais:
- Cargo comissionado
- Função de confiança

Isso deve controlar o assunto e a redação do documento.

Não tente inferir cegamente esse dado a partir do cargo da planilha.

## 13. MOTIVO DO AFASTAMENTO

Criar campo estruturado.

Opções iniciais:
- Férias regulamentares
- Licença para tratamento de saúde
- Licença especial
- Outro

Em Outro, permitir texto livre.

O texto que vai ao documento deve permanecer editável quando necessário.

Não criar lógica gramatical excessivamente rígida que impeça casos futuros.

## 14. PERÍODO

Campos obrigatórios:
- Data inicial
- Data final

Validar:

data final >= data inicial.

Mostrar duração calculada ao usuário como informação auxiliar.

No documento, adotar redação institucional uniforme baseada no período informado.

Não exigir que o usuário calcule manualmente quantidade de dias.

## 15. SUBSTITUIÇÃO SIMPLES

Fluxo:

SERVIDOR AFASTADO
↓
SUBSTITUTO

Dados principais:
- servidor afastado;
- gênero;
- matrícula;
- cargo/função utilizada no documento;
- gabinete;
- natureza da função;
- início;
- fim;
- motivo;
- substituto;
- gênero do substituto;
- matrícula;
- cargo do substituto;
- lotação do substituto.

O documento deverá conter todas as informações essenciais de forma coerente e gramaticalmente correta.

## 16. SUBSTITUIÇÃO EM CASCATA

Não limite a cascata a apenas dois níveis.

Depois da primeira substituição, disponibilizar botão:

Adicionar substituição em cascata

Exemplo:

KÁTIA
↓
MARIA DA LUZ
↓
ANA CLÁUDIA

Ao clicar em adicionar etapa:

o substituto da etapa anterior deve automaticamente se tornar o substituído da próxima etapa.

Esse servidor já deve aparecer preenchido e não deve ser escolhido novamente manualmente.

O usuário escolhe apenas o novo substituto.

Permita várias etapas enquanto necessário.

Permita remover somente etapas adicionais de forma segura.

Todas as etapas do mesmo memorando usarão, nesta primeira versão, o mesmo período.

O texto adicional deve seguir o padrão institucional equivalente a:

“Em decorrência da substituição acima, indico ... para substituir no mesmo período ...”

Adaptar corretamente:
- servidor/servidora;
- lotado/lotada;
- o/a;
- nomes;
- cargos;
- matrículas;
- lotações.

Não implementar heurística genérica perigosa para feminizar nomes de cargos.

Cargo/função documental permanece editável.

## 17. VALIDAÇÕES DA CADEIA

Bloquear erros evidentes:
- mesmo servidor substituindo a si próprio;
- servidor repetido de maneira inválida na cadeia;
- ciclo A → B → A;
- cadeia quebrada;
- etapa seguinte cujo substituído não corresponda ao substituto anterior.

Avisar quando existir outra substituição registrada no sistema com período sobreposto envolvendo a mesma pessoa.

Sobreposição deve gerar AVISO quando puder ser legítima, não bloqueio indiscriminado.

## 18. ESTRUTURA DE BANCO EXTENSÍVEL

Não crie uma arquitetura limitada a memorando de substituição.

Quero uma estrutura geral equivalente a:

memorandos
    tipo = SUBSTITUICAO

e tabelas específicas para detalhes da substituição.

Sugestão conceitual:
- memorandos
- memorandos_substituicao
- memorandos_substituicao_etapas
- memorandos_arquivos
- servidores
- servidores_importacoes

Adapte nomes ao padrão já utilizado no projeto.

A entidade genérica Memorando deve permitir no futuro adicionar:

PARTICIPACAO_EVENTO
OUTROS

sem reescrever o módulo.

Use IDs adequados e FKs.

Finalizados não devem depender dos dados atuais da tabela servidores.

Guarde snapshots dos dados utilizados.

## 19. STATUS DO DOCUMENTO

O memorando em si pode ter estados:

RASCUNHO
FINALIZADO
CANCELADO

Rascunho pode ser editado e excluído.

Finalizado não deve ter seus dados documentais alterados silenciosamente.

Se necessário corrigir um documento finalizado, preserve rastreabilidade.

Não faça hard delete do documento final.

## 20. STATUS DA SUBSTITUIÇÃO

Além do status documental, calcule visualmente a situação da substituição pelas datas:

AGENDADA
- data inicial futura

EM ANDAMENTO
- hoje entre data inicial e final

ENCERRADA
- data final passada

CANCELADA
- memorando cancelado

Esse status temporal pode ser derivado em tempo de consulta.

Não precisa ser atualizado diariamente no banco.

## 21. FLUXO DE CRIAÇÃO

Quero um fluxo parecido com o padrão seguro já utilizado em Ofícios:

Preencher
→ Pré-visualizar PDF
→ Conferir
→ Finalizar
→ Baixar PDF

Permitir também:

Salvar rascunho

A prévia deve possuir fingerprint/hash dos dados.

Se qualquer dado do formulário for alterado depois da prévia:

INVALIDAR a prévia e exigir nova geração antes de finalizar.

Reutilize o padrão já consolidado no módulo Ofícios quando adequado.

## 22. GERAÇÃO DO DOCUMENTO

Internamente você pode gerar DOCX e converter para PDF utilizando a infraestrutura já existente no projeto.

Mas, nesta versão, a saída oficial apresentada ao usuário será PDF.

Não é necessário disponibilizar DOCX oficial.

Reutilize a infraestrutura existente de conversão PDF se ela já estiver estável.

Não crie uma segunda implementação de conversão se a atual servir.

O PDF deve ser gerado somente quando solicitado/finalizado, sem processamento pesado durante simples navegação.

## 23. PDF FINAL

Ao finalizar:
- gerar PDF definitivo;
- armazenar uma cópia no banco/estrutura de arquivos do módulo;
- registrar tamanho, hash e data;
- permitir download imediatamente.

Botão claro:

Baixar PDF

O mesmo PDF deve continuar disponível depois no Histórico.

O PDF histórico deve ser exatamente o arquivo finalizado originalmente.

Alterações futuras na base de servidores, cargo, signatário ou template NÃO podem alterar retroativamente esse arquivo.

## 24. NOME DO ARQUIVO

Crie nome amigável e seguro, por exemplo:

Memorando_Substituicao_Niltamir_Galdino_Guedes_2026-09-08.pdf

Sanitize caracteres incompatíveis com arquivos.

Não utilizar número interno de memorando no nome porque este sistema não fará numeração oficial.

## 25. NÚMERO OFICIAL POSTERIOR

No Histórico de memorandos finalizados, permitir:

Registrar número oficial

Campo livre adequado ao padrão do TCE-PB.

Exemplo conceitual:

123/2026

ou o formato que o sistema oficial fornecer.

Essa informação:
- pode ser incluída depois;
- pode ser corrigida por usuário autorizado;
- fica visível no histórico;
- NÃO altera o PDF finalizado;
- NÃO participa de sequência do Ferramentas MPC-PB.

Registrar auditoria da alteração.

## 26. HISTÓRICO E CONSULTA

O módulo deve permitir visualização fácil.

Criar navegação conceitualmente semelhante a:

Visão Geral
Novo Memorando
Em andamento
Histórico
Base de Servidores [ADMIN]

Na Visão Geral mostrar indicadores leves, por exemplo:
- em andamento;
- agendadas;
- encerradas recentemente.

Não faça queries desnecessárias.

## 27. EM ANDAMENTO

Apresentar de forma clara:

SUBSTITUÍDO → SUBSTITUTO

Em cascata:

A → B → C

Mostrar pelo menos:
- período;
- gabinete;
- motivo;
- status;
- número oficial, se já houver.

Clique/expander deve abrir detalhes completos e permitir baixar PDF.

## 28. HISTÓRICO

Filtros:
- servidor;
- gabinete;
- período;
- status;
- número oficial;
- busca textual.

Ordenação padrão:

mais recentes primeiro.

Paginar se necessário.

Não carregar BLOB/PDF durante listagem.

Buscar os bytes do PDF SOMENTE quando o usuário pedir download.

## 29. DETALHES DO HISTÓRICO

Ao abrir um registro mostrar:
- signatário;
- período;
- gabinete;
- motivo;
- natureza da função;
- cadeia completa;
- dados utilizados de cada participante;
- data de criação;
- criador;
- data de finalização;
- número oficial;
- status;
- botão Baixar PDF.

## 30. PERMISSÃO DO NOVO MÓDULO

O portal já possui autorização por módulos.

Crie uma permissão própria:

pode_memorandos

Não vincule Memorandos artificialmente a Portarias, Ofícios ou Administração.

Requisitos:
- ADMINISTRADOR sempre possui acesso total;
- usuários comuns têm a permissão configurável;
- existentes ADMINISTRADOR devem receber pode_memorandos = 1 na migration;
- usuários comuns existentes devem iniciar sem permissão;
- novo checkbox “Memorandos” na Administração — Usuários e Acessos;
- criação/edição de usuários deve preservar essa permissão;
- testes de autorização devem ser atualizados.

Usuário sem pode_memorandos:
- não vê o módulo ativo na Home;
- não consegue abri-lo por manipulação de session_state/rota;
- backend também deve negar acesso.

## 31. HOME DO PORTAL

Quando implementado, o card:

Memorandos de Substituição

deixa de ser “EM BREVE” e passa a “ATIVO”.

Preserve o layout atual da Home:
- duas colunas;
- ativos primeiro;
- em breve depois;
- altura uniforme.

Não altere o restante do layout.

## 32. BASE DE SERVIDORES E PERMISSÕES

Usuário comum com acesso a Memorandos:
- pode pesquisar servidores;
- pode usar dados no formulário;
- pode editar cargo/lotação NO MEMORANDO;
- não pode importar XLSX;
- não pode administrar a base-mestra.

Administrador:
- possui tudo acima;
- pode abrir Base de Servidores;
- importar/atualizar XLSX;
- corrigir cadastro-mestre quando implementado.

## 33. SEGURANÇA E PRIVACIDADE

Nunca disponibilize a base de servidores para usuário sem permissão de Memorandos.

Não exponha matrícula/dados em logs desnecessários.

Não versionar no Git:
- planilha completa;
- dumps da base;
- uploads;
- arquivos temporários contendo a base.

Atualize .gitignore se necessário.

Para testes, use dados SINTÉTICOS.

Não inclua a planilha real em fixtures do repositório.

## 34. MIGRATIONS

A migration deve ser:
- aditiva;
- idempotente;
- segura para SQLite e PostgreSQL;
- sem apagar dados existentes;
- sem modificar Portarias, Agenda ou Ofícios.

Use o padrão de migration/marker já consolidado no projeto.

Crie marcador equivalente a:

memorandos_schema_v1

ou nome coerente.

Para pode_memorandos:

PostgreSQL:
adicionar coluna com segurança se ausente.

SQLite:
inspecionar schema antes do ALTER, evitando erro em reexecução.

Atualizar administradores existentes para pode_memorandos = 1.

Não alterar permissões de usuários comuns existentes.

## 35. BACKUP

Verifique a infraestrutura existente de backup/exportação PostgreSQL.

Garanta que as novas tabelas de Memorandos e Servidores sejam incluídas no inventário de backup da aplicação, se esse mecanismo for aplicável.

Aproveite para corrigir, de forma segura e testada, a limitação já conhecida de as tabelas:

usuarios_acesso
usuario_gabinetes

não estarem no inventário TABLES do backup, se essa correção for simples e não causar regressão.

Atualize testes de inventário de schema.

## 36. PERFORMANCE

Preserve todas as otimizações recentes.

Home:
não consultar tabelas de Memorandos.

Só carregar código/dados pesados quando o módulo for aberto.

Base de servidores:
não abrir XLSX em navegação normal.

Autocomplete:
não gerar N+1 queries.

Histórico:
não carregar PDFs/BLOBs na listagem.

PDF:
gerar somente sob demanda.

Reutilizar Store/pool/cache existentes.

Não criar conexão própria paralela.

## 37. UI E IDENTIDADE VISUAL

Use o shell visual institucional já implementado:
- logo atual da sidebar;
- cabeçalho institucional horizontal;
- tipografia;
- botões;
- cores;
- espaçamentos.

Não faça redesign global.

O módulo deve parecer nativo do Ferramentas MPC-PB.

## 38. ESTRUTURA DE SERVIÇOS

Separe responsabilidades de forma semelhante aos demais módulos:

database/
services/
document_generator/
UI

Não coloque SQL direto na UI.

Não coloque geração de documento misturada com widgets Streamlit.

Não duplique helpers existentes.

## 39. AUDITORIA

Registrar quando possível:
- criação de rascunho;
- edição;
- finalização;
- cancelamento;
- registro/alteração do número oficial;
- importação da base;
- edição administrativa da base.

Associar ao e-mail do usuário autenticado quando a infraestrutura atual permitir.

## 40. TESTES DO BANCO

Criar testes SQLite completos para:
- schema;
- idempotência;
- servidores;
- importação;
- upsert por matrícula;
- inconsistências;
- criação de memorando;
- cascata;
- snapshots;
- arquivos;
- número oficial;
- status;
- exclusão de rascunho;
- preservação de finalizado;
- filtros.

Criar equivalentes PostgreSQL onde a infraestrutura descartável permitir.

Se MPC_TEST_POSTGRES_URL não estiver configurada, skip esperado.

Nunca usar Supabase real nos testes.

## 41. TESTES DE AUTORIZAÇÃO

Cobrir:
- admin vê Memorandos;
- usuário com pode_memorandos vê;
- usuário sem permissão não vê;
- backend nega acesso;
- usuário comum não vê Base de Servidores;
- admin vê;
- usuário comum não consegue chamar operação administrativa diretamente;
- administrador continua com todos os módulos.

## 42. TESTES DO FORMULÁRIO

Cobrir no mínimo:
- substituição simples;
- cascata de 2 etapas;
- cascata de 3 etapas;
- prevenção de ciclo;
- pessoa substituindo a si própria;
- edição de cargo/lotação no snapshot;
- gabinete manual;
- regra especial Procuradoria-Geral;
- gênero masculino;
- gênero feminino;
- signatário padrão Elvira;
- outro signatário;
- motivo Outro;
- preview invalidada após alteração.

## 43. TESTES DO DOCUMENTO

Crie testes estruturais e, se a infraestrutura existente permitir, testes visuais/extração de texto para garantir:
- título MEMORANDO;
- destinatário correto;
- assunto;
- vocativo;
- nomes;
- matrículas;
- cargo/função;
- período;
- motivo;
- gabinete;
- cascata;
- fechamento;
- signatário;
- cargo do signatário.

Compare o resultado com os modelos anexados/referências.

Não exija pixel-perfect se o pipeline atual não oferece estabilidade para isso, mas preserve fielmente layout, quebras e aparência institucional.

## 44. TESTES DO PDF

Validar:
- PDF gerado;
- bytes não vazios;
- arquivo armazenado;
- hash;
- download;
- histórico baixa exatamente o mesmo PDF;
- alteração posterior de número oficial não muda o hash do PDF.

## 45. TESTES DE IMPORTAÇÃO XLSX

NÃO use a planilha real como fixture.

Gere XLSX sintético nos testes contendo:
- registros válidos;
- matrícula duplicada;
- matrícula zero;
- matrícula vazia;
- nome duplicado;
- atualização de cargo;
- atualização de setor.

Validar prévia e importação.

## 46. TESTE COM OS EXEMPLOS REAIS

Além da suíte sintética, durante o desenvolvimento use os modelos fornecidos para reproduzir manualmente:

CASO SIMPLES:
Niltamir → Ana Cláudia

CASO CASCATA:
Kátia → Maria da Luz → Ana Cláudia

Esses testes manuais servem para conferir texto e layout.

Não hardcode esses nomes na lógica de produção.

## 47. NÃO IMPLEMENTAR AGORA

Não implementar ainda:
- memorando de evento;
- memorando de viagem;
- outros destinatários;
- numeração própria;
- envio automático ao sistema TCE-PB;
- integração com sistema externo;
- assinatura digital;
- e-mail automático.

Deixe apenas arquitetura preparada para novos tipos.

## 48. NÃO ALTERAR

Não alterar:
- funcionamento de Portarias;
- numbering de Portarias;
- Ofícios;
- numbering de Ofícios;
- Agenda;
- OIDC;
- login Google;
- Supabase configuration;
- DATABASE_URL;
- Session Pooler;
- otimizações recentes;
- identidade visual global.

## 49. VALIDAÇÃO FINAL

Execute a suíte mais ampla possível.

Além dos testes novos, rode regressão de:
- access;
- portal;
- UI;
- backend selection;
- Portarias;
- Agenda;
- Ofícios;
- PostgreSQL;
- cache/session state.

Não aceite regressão dos módulos existentes.

## 50. ENTREGA

Nesta mesma tarefa:

1. analise a arquitetura existente;
2. analise os modelos em referencias;
3. analise a planilha XLSX fornecida;
4. implemente banco;
5. implemente permissão;
6. implemente importação XLSX;
7. implemente autocomplete;
8. implemente substituição simples;
9. implemente cascata;
10. implemente gerador;
11. implemente PDF;
12. implemente armazenamento/download;
13. implemente Em andamento;
14. implemente Histórico;
15. implemente número oficial posterior;
16. implemente Base de Servidores admin;
17. integre Home/Admin;
18. crie testes;
19. rode regressão;
20. faça revisão final.

NÃO faça commit.
NÃO faça push.
NÃO conecte ao Supabase real.
NÃO altere Secrets.

Ao final entregue relatório com:
- arquitetura implementada;
- migrations;
- tabelas;
- arquivos criados;
- arquivos alterados;
- regras do gerador;
- importação de servidores;
- permissões;
- funcionamento de simples/cascata;
- PDF;
- histórico;
- número oficial;
- testes executados;
- resultados;
- PostgreSQL skipped, se houver;
- riscos residuais;
- passos exatos para eu testar localmente;
- conclusão objetiva:

PRONTO PARA TESTE LOCAL

ou

AINDA HÁ PENDÊNCIAS.
