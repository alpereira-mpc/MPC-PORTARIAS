# Minutas IA — implantação da Fase 2B.1

## Pré-requisitos

- A Fase 2A deve estar publicada e `database/minutas_referencias.sql` já deve ter
  sido executado.
- O bucket privado e os Secrets da Fase 2A permanecem inalterados.
- Não execute este SQL em outro projeto Supabase sem antes conferir o projeto
  associado ao `DATABASE_URL` do aplicativo.

## Ordem de implantação

1. No painel do Supabase usado pelo aplicativo, abra o SQL Editor.
2. Revise e execute manualmente
   `database/minutas_documentos_complementares.sql`.
3. Confirme que a tabela
   `mpc_portarias.minutas_documentos_complementares` foi criada e que as quatro
   linhas de `mpc_portarias.minutas_referencias` continuam inalteradas.
4. Publique o código e reinicie o aplicativo no Streamlit Cloud.
5. Entre com o e-mail administrador já autorizado por
   `MINUTAS_PILOTO_EMAILS` e abra a Biblioteca de Referências.
6. Em cada processo, confirme que os dois documentos principais continuam
   disponíveis e que a seção Documentos Complementares aparece vazia.

O script é aditivo e idempotente: cria somente a tabela filha e seu índice. Ele
não contém migração de dados e não altera, exclui ou move registros e arquivos
existentes. Os campos de auditoria continuam em `TEXT`, como na tabela da Fase
2A, para preservar o contrato atual do repositório. A data conhecida do documento
usa `DATE`.

## Reorganização manual do caso SEDH — TC 02650/25

1. Abra o processo `TC 02650/25` da Procuradora Elvira Samara.
2. Em Documentos Complementares, adicione o Relatório de Análise de Defesa
   original em PDF, com posição 1.
3. Adicione a Cota Ministerial em PDF ou DOCX, com posição 2.
4. Prepare o download dos dois documentos e confira os arquivos.
5. Abra **Reorganizar relatório principal — caso SEDH**.
6. Selecione o Relatório de Análise de Defesa original como novo relatório
   principal.
7. Mantenha a posição 3 para o Relatório de Complementação de Instrução que está
   atualmente no campo principal; informe sua data, se conhecida.
8. Marque a confirmação explícita e selecione **Reorganizar documentos**.
9. Confira a sequência resultante: Relatório de Análise de Defesa principal,
   Cota Ministerial na posição 2, Relatório de Complementação na posição 3 e
   Parecer Definitivo ao final.
10. Prepare novamente os downloads e compare-os com os originais.

A reorganização troca os vínculos dos dois relatórios em uma única transação no
PostgreSQL. Nenhum objeto é copiado, removido ou renomeado no Storage. Se a
transação falhar, os metadados são revertidos pelo banco e os arquivos permanecem
inalterados.

## Verificação posterior

- Cadastre dois complementares em um processo de teste, em ordem de upload
  diferente da ordem processual, e corrija suas posições.
- Reclassifique um documento e informe/remova sua data.
- Confirme o download de PDF e DOCX.
- Teste uma exclusão somente após marcar a confirmação.
- Verifique com um usuário não autorizado que o módulo continua oculto e que a
  chamada direta ao serviço é recusada.

