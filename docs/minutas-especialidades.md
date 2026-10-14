# Minutas IA — especialidades e reorganização do acervo

## Situação desta entrega

Implementação **local**, sem commit/push e sem conexão operacional, leitura de Secrets reais ou alteração de produção. A reorganização de produção **não foi executada**. Não há contagens reais disponíveis nesta sessão. A instrução do repositório proíbe acesso à produção; aplica-se a alternativa de entregar rotina e roteiro de execução autorizada.

| Ambiente | Processos | Documentos | Resultado da reclassificação |
|---|---:|---:|---|
| Produção | Não consultado | Não consultado | Nenhuma aplicação feita nesta entrega |
| Cenário sintético de reconciliação | 5 | 10 | 3 classificados; 2 pendentes; todos preservados |

Os cinco registros do teste não são cópias de documentos reais. Os números de exemplo não são evidência de classificação. Origem, classe e perfil precisam ser confirmados no inventário operacional.

## Diagnóstico do modelo anterior

- `minutas_referencias`: processo histórico, Procuradora, exercício, subtipo de PCA, metadados e caminhos dos dois documentos principais.
- `minutas_documentos_complementares`: documentos instrutórios adicionais, ordem e vínculo com a referência.
- `minutas_extracoes`: texto, páginas, seções, hash, versão do extrator, status e caminho do original. Não armazena PDFs.
- Bucket privado `minutas-referencias`: originais acessados pelo backend autorizado; nenhuma movimentação física é necessária à classificação.
- Pesquisa jurídica: PostgreSQL FTS em português; índice GIN parcial de pareceres processados em `minutas_pesquisa_juridica.sql`. Não foi encontrado mecanismo de embeddings ou índice vetorial neste módulo. Não há reconstrução de embeddings nesta entrega.
- A Biblioteca listava as referências, mas a Geração admitia apenas quatro números fixos. Isso ocultava registros da seleção da Geração sem excluir os registros do banco. Essa lista fixa foi retirada do motor. O inventário ainda identifica quais registros ficariam ocultos pelo filtro **legado**, para reconciliação.
- Resultados, análises, downloads e DOCX eram derivados mantidos na sessão. Agora a revisão do acervo, a especialidade, a configuração e a identidade invalidam os derivados pertinentes. Nenhum cache global contém PDFs.

## Modelo aditivo

`database/minutas_especialidades.sql` deve ser instalado manualmente após inventário e backup:

- `minutas_especialidades`: 24 especialidades iniciais da Dra. Elvira; estado, versão/configuração jurídica, motor, revisão, casos testados, pendências e resultados de validação.
- `minutas_classificacoes`: classe formal, modalidade, perfil, entidade, temas, evidências, motivo e origem da decisão; estado CLASSIFICADO, PENDENTE ou NAO_CLASSIFICADO.
- `minutas_acervo_vinculos`: associação primária e vínculos adicionais expressamente justificados, sem duplicação física de documentos.
- `minutas_especialidades_eventos`: antes/depois, operador e lote; alteração de estado, classificação, vínculo e upload auditáveis.
- `minutas_acervo_documentos`: documentos novos independentes, com finalidade e hash. Permite começar um processo com um único documento; mantém versões anteriores.
- `minutas_documentos_catalogo`: visão de documentos principais legados, complementos e arquivos novos. Preserva caminhos e IDs originais.

O DDL permite valores nulos nos campos dos documentos principais e no subtipo legado, sem alterar valores existentes. Os demais controles de tamanho/tipo e integridade permanecem. Não é executado automaticamente no início do aplicativo.

O catálogo distingue PCA, atos de pessoal, inspeção, tomada de contas especial, denúncia, representação, consulta, recurso e pedido rescisório. Licitações, contratos, convênios e concursos são especialidades **temáticas**, exigindo a classe efetiva do processo. Não se presume que contas de Prefeitura sejam contas de governo. Referência organizacional: [Regimento Interno do TCE-PB, RN-TC 07/2024](https://tce.pb.gov.br/wp-content/uploads/2024/12/REGIMENTOINTERNORNTCN07_2024.pdf). O catálogo funcional não substitui a classificação oficial registrada no processo.

Somente RPPS recebe `pca-rpps-v1`, configuração 1 e estado inicial **Funcional com ajustes**. As outras 23 começam em **A iniciar**, com acervo/pesquisa disponíveis, sem motor habilitado. Os campos de configuração jurídica são pontos de extensão; não foram criadas teses ou regras jurídicas novas. Mudar o estado para Validada exige confirmação de revisão humana e registro dos testes, mas não habilita um motor inexistente.

## Classificação e isolamento

A rotina examina todos os registros da Dra. Elvira, não apenas os quatro exemplos. Usa metadados estruturados, vínculos válidos e identificação expressa no texto já extraído. Não usa o nome do PDF ou o número do processo como prova de especialidade. Entidades divergentes, classes conflitantes, secretarias sem nível federativo e Prefeituras sem modalidade ficam para conferência. A atribuição manual exige fundamento e fica auditada.

A pesquisa, o inventário de fontes e a listagem especializada aplicam **na consulta SQL**: Procuradora, classificação confirmada e vínculo com a especialidade. Escopo ausente, inválido ou conflitante não amplia a busca. A pesquisa usa somente pareceres definitivos, com extração válida e correspondência ao documento cadastrado. Auditoria/complementos são instrução; modelos visuais ficam fora da extração jurídica e do RAG.

Geração e exportação exigem RPPS classificado. A assinatura das fontes inclui classificação e configuração. O parecer definitivo do processo-alvo continua excluído, inclusive hashes e duplicatas textuais. O bloqueio existente por ausência de parecer histórico apto é preservado: se Taperoá for o único RPPS confirmado, será necessário cadastrar/classificar outro precedente RPPS; o sistema não buscará Câmara ou Prefeitura para contornar a falta.

As novas tabelas têm RLS habilitada e privilégios revogados de PUBLIC/anon/authenticated; continuam acessíveis apenas pelo backend autorizado na arquitetura atual. RLS não identifica o usuário final das conexões privilegiadas: autorização de sessão e os filtros de serviço/SQL continuam obrigatórios. Não foram alteradas autenticação, permissões gerais ou Backup V2.

## Interface

- Seleção persistente de Procuradora → classe/grupo temático → especialidade.
- Acervo, Pesquisa Jurídica e Gerar Minuta executam somente a área ativa.
- Acervo paginado, pesquisa por processo/entidade e filtro documental. Arquivos e texto detalhado só são abertos por ação explícita sobre um processo/documento.
- Cadastro múltiplo: identificação local explícita, resumo editável, confirmação, progresso e resultado por arquivo. Uma falha não desfaz arquivos já concluídos. Mesmo conteúdo é deduplicado; nome repetido com conteúdo diferente exige confirmação de nova versão, sem sobrescrever o original.
- Pendências de classificação permanecem acessíveis. Correção de classe, entidade, temas e vínculos não exige reupload. Histórico permite restaurar a classificação anterior, desde que não haja decisão posterior.
- Administração: indicadores agregados, simulação/reclassificação e evolução de estados. A abertura desses painéis não chama Gemini nem executa nova análise jurídica.

## Operação segura — obrigatória antes da publicação

1. Obter autorização específica do responsável pelo banco. Não utilizar produção para testes.
2. Preparar clone isolado de homologação. Fazer backup **completo** do schema e dos objetos do bucket e provar restauração. Não presumir que o inventário fixo do Backup V2 inclua as novas tabelas. Guardar evidência datada do backup e da restauração.
3. Disponibilizar a conexão explicitamente em `MINUTAS_MIGRATION_DATABASE_URL` no ambiente seguro do operador; a ferramenta não lê `.streamlit/secrets.toml`. Não colocar a credencial em argumentos, commits ou relatórios.
4. Antes do DDL, executar inventário legado. Os relatórios são criados com modo exclusivo e não sobrescrevem arquivos.

```powershell
.venv\Scripts\python.exe scripts/minutas_reclassificar.py inventario-legado --report inventario-antes.json --storage-bucket minutas-referencias
```

O relatório inclui contagens, IDs, caminhos, tipos, hashes duplicados conhecidos, extrações órfãs/desatualizadas, metadados incompletos e registros omitidos pelo filtro antigo. O inventário de `storage.objects` é opcional tecnicamente, mas deve ser feito operacionalmente quando houver permissão. Objetos sem vínculo **no escopo Elvira** não são necessariamente órfãos globais; nunca os excluir automaticamente. Hashes ainda inexistentes são uma limitação explícita da detecção global de duplicatas.

5. Conferir divergências e corrigir integridade antes de migrar. Instalar `database/minutas_especialidades.sql` no schema correto, inicialmente no clone. O script usa `mpc_portarias`; deployments com outro schema devem revisar o DDL explicitamente.
6. Gerar e revisar o plano após o DDL:

```powershell
.venv\Scripts\python.exe scripts/minutas_reclassificar.py simular --report plano-conferido.json --storage-bucket minutas-referencias
```

7. Conferir especialmente os processos 01499/26, 01653/26, 02650/25 e 02720/25 **e todos os demais**. Resolver a distinção municipal/estadual e governo/gestão quando houver documentos suficientes. Sem evidência, manter pendente.
8. Aplicar somente o plano atual, com evidência real de backup restaurado:

```powershell
.venv\Scripts\python.exe scripts/minutas_reclassificar.py aplicar --plan plano-conferido.json --report reconciliacao.json --storage-bucket minutas-referencias --actor operador-autorizado --backup-evidence identificador-da-evidencia --restore-verified
```

A opção de restauração é uma declaração auditável do operador, **não** um teste de restauração executado pela ferramenta. O plano é recalculado e comparado; o backend bloqueia alterações concorrentes durante a aplicação e recusa inventários divergentes e extrações sem vínculo. Nenhum PDF é movido/copiado e nenhuma extração é reconstruída por reclassificar.

9. Comparar contagens, IDs e caminhos antes/depois; repetir simulação/aplicação para verificar idempotência. Verificar o índice GIN existente, RLS, vínculos e acesso do backend. Classificações manuais confirmadas não são sobrescritas pela migração.
10. Validar no clone seleção, pesquisa, geração RPPS, DOCX e navegação nos temas institucionais. Só depois autorizar a mesma sequência em produção e publicar o código no Streamlit Cloud. Sem catálogo provisionado, a nova interface fecha o acesso especializado e apresenta orientação; não há fallback para todo o acervo.

### Reversibilidade

Usar o histórico de classificação no Acervo para restaurar a decisão anterior. A reversão registra novo evento, preserva os arquivos/extrações e restaura os vínculos anteriores; recusa restaurar evento superado. Não fazer rollback do DDL excluindo tabelas, nem voltar ao aplicativo antigo após cadastrar processos sem documentos principais obrigatórios, sem um plano de compatibilidade. Backup integral é a recuperação de desastre, não a primeira ação para corrigir classificação.

## Validação local e limites

Testes direcionados cobrem classificação conservadora, preservação/reconciliação, repetição, rollback, UUID, reversão, vínculos controlados, escopo SQL, download, exclusão de modelos, contexto enviado à IA simulada, seleção de RPPS além dos quatro exemplos, lote/duplicidade/versão, cache, navegação Streamlit e contratos anteriores de geração/extração/DOCX. Os testes das Portarias 5, 6 e 8/2026 conferem pacote e término na assinatura.

O teste `tests/test_minutas_specialties_postgresql.py` exige `MPC_TEST_POSTGRES_URL` exclusivamente em loopback, banco `mpc_disposable_tests` e tabela de proteção. Nesta sessão não há banco descartável configurado: o teste foi pulado. A transação de classificação foi exercitada em SQLite sintético; a execução do DDL PostgreSQL, RLS e FTS reais continua uma etapa de homologação obrigatória. Não afirmar equivalência completa de transações SQL entre os dois bancos.

Nenhuma chamada paga foi usada nos testes. Não foi realizado benchmark de carga Supabase/Streamlit Cloud, inspeção visual em navegador/mobile dos cinco temas nem validação PDF/Word renderizado. Os testes de DOCX conferem conteúdo e XML; a alteração no exportador é apenas uma guarda de escopo, sem alteração de layout.

## Arquivos envolvidos

- Novos: `database/minutas_especialidades.sql`, `database/minutas_especialidades.py`, `services/minutas_specialties.py`, `services/minutas_acervo.py`, `services/minutas_specialties_ui.py`, `scripts/minutas_reclassificar.py`, este relatório e os dois arquivos de testes de especialidades.
- Integrações: `database/minutas_referencias.py`, `services/minutas_library.py`, `services/minutas_extraction.py`, `services/minutas_search.py`, `services/minutas_generation.py`, `services/minutas_docx.py`, `services/minutas_ia_ui.py`.
- Testes existentes ajustados: `test_minutas_generation.py`, `test_minutas_search.py`, `test_minutas_docx.py`, `test_minutas_extraction.py`, `test_minutas_piloto.py`.

Deliberadamente preservados: prompts e validações jurídicas do motor, tratamento de advertências, cabeçalho sem numeração, recomendações consolidadas, ausência de informações auxiliares no DOCX, autenticação, módulos externos, originais em `referencias/`, critérios de finalização/numeração de Portarias e infraestrutura de backup.
