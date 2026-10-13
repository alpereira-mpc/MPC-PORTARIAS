# Minutas IA — implantação da Fase 2B.2

## Implantação

1. Confirme que o projeto aberto no Supabase é o mesmo do `DATABASE_URL` do
   Streamlit Cloud.
2. No SQL Editor, revise e execute manualmente
   `database/minutas_extracoes.sql`.
3. Confirme a criação de `mpc_portarias.minutas_extracoes`, com RLS habilitado e
   sem policies para `PUBLIC`, `anon` ou `authenticated`.
4. Não altere o bucket, os arquivos existentes nem os Secrets das fases
   anteriores.
5. Publique o código e reinicie o aplicativo.
6. Entre com o e-mail administrador autorizado e abra a Biblioteca de
   Referências.

O aplicativo não executa o SQL nem inicia extrações automaticamente. A tabela é
aditiva, guarda somente texto e metadados e possui vínculo com
`minutas_referencias`. A exclusão de uma referência remove suas extrações por
`ON DELETE CASCADE`.

## Processamento dos quatro processos

Processe um processo por vez, nesta ordem operacional sugerida: Rio Tinto,
Coxixola, Taperoá e SEDH.

1. Expanda o processo.
2. Confira a sequência visual dos arquivos.
3. Selecione **Processar documentos**.
4. Aguarde a mensagem de conclusão antes de abrir outro processo.
5. Confira o status individual de cada arquivo.
6. Para itens processados, abra **Ações > Visualizar texto extraído** e compare
   páginas ou texto com o original.
7. Para itens com **Necessita atenção**, confira o arquivo original. Use
   **Reprocessar extração** somente após confirmar que o arquivo é textual e
   íntegro.

No SEDH, confira expressamente a ordem Relatório da Auditoria, Cota Ministerial,
Relatório de Complementação e Parecer Definitivo conforme a classificação já
registrada. A extração não altera essa ordem.

## Funcionamento e limitações

- PDFs são extraídos página a página com `pypdf`.
- DOCX são lidos com `python-docx`, preservando a ordem entre parágrafos e
  tabelas; nenhuma paginação é inventada.
- Seções jurídicas só são registradas quando existe um título textual explícito.
  O texto integral permanece disponível quando não há classificação segura.
- Arquivos sem texto ou com texto insuficiente recebem **Necessita atenção**.
- OCR, Gemini, embeddings, RAG e geração de minutas não fazem parte desta fase.
- Hash SHA-256 e versão do extrator evitam reprocessamento de conteúdo
  inalterado. O binário permanece somente no bucket privado.
- A substituição ou exclusão invalida a extração correspondente. Reclassificação,
  ordenação e promoção atualizam os metadados sem modificar o texto ou o arquivo.
- Na preparação futura das fontes, os resultados ficam isolados por Procuradora.
  O parecer definitivo da própria referência-alvo é excluído; para Taperoá, ele
  poderá ser usado apenas na avaliação posterior.

