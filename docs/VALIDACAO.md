# Validação da implementação MPC-PB

Validação realizada em 9 de setembro de 2026, em Windows com Python 3.12 e Microsoft Word. A pasta `auditoria/` da tentativa anterior foi removida antes da implementação. Este relatório registra os testes da aplicação implementada; os bancos usados nos testes são separados do banco de uso real.

## Resultados e evidências

| ITEM | RESULTADO | TESTE REALIZADO | OBSERVAÇÃO |
|---|---|---|---|
| Suíte automatizada | 51 testes aprovados | `python -m pytest -q --junitxml=docs/test-results.xml` | Resultado individual e duração em `test-results.xml`; nenhum teste ignorado. |
| Telas | Aprovado | Streamlit AppTest em Nova Portaria, Histórico, Procuradores e Configurações | Teste inclui seleção de titular/substituto, prévia, finalização e acesso ao histórico. |
| Navegador local | Aprovado | Microsoft Edge headless, carregamento das quatro telas | Nenhum erro JavaScript observado; capturas examinadas durante a implementação. |
| Serviço local | Aprovado | GET `http://127.0.0.1:8501/_stcore/health` | Resposta `ok`; servidor vinculado a 127.0.0.1. |
| Gênero gramatical | Aprovado | Casos de Subprocuradoria, Ouvidoria, Corregedoria feminina e signatário masculino | Corrigidos os erros dos modelos 4 e 5. |
| Procurador-Geral em exercício | Aprovado | Bradson como signatário, teste `test_acting_signer` | Preâmbulo masculino e assinatura com “em exercício”. |
| Base legal | Aprovado no escopo conferido | Testes de composição e consulta ao regimento publicado pelo TCE-PB | Art. 61, § 6º, e art. 70, § 3º, armazenados em configuração editável. |
| Datas e ano | Aprovado | Mesmo mês, meses/anos diferentes, fevereiro, ano bissexto e entradas inválidas | Ano truncado também é rejeitado em edição manual da prévia. |
| Múltiplas substituições | Aprovado | Dois e três parágrafos; período comum e próprio; combinação com nota | A segunda substituição não precisa ter o mesmo período. |
| Nota de rodapé | Aprovado | Inspeção de `footnotes.xml`, relação de pacote, marcador e PDF da Portaria 5 | Nota real do Word, ligada à citação legal. |
| Numeração concorrente | Aprovado | Seis finalizações concorrentes, além de quatro tentativas do mesmo rascunho | Sequência 9–14 sem duplicidade; mesmo rascunho consome só um número. |
| Rascunho e prévia | Aprovado | Salvar, preparar prévia, duplicar e conferir sequência | Sem consumo até finalizar. |
| Cancelamento e imutabilidade | Aprovado | Cancelar e tentar excluir/alterar ato finalizado | Número retido; banco impede exclusão e alteração do conteúdo do ato. |
| Falha durante geração | Aprovado | Gerador simulado com erro de disco | Transação revertida, rascunho preservado e número não consumido. |
| Exportação sem sobrescrita | Aprovado | Exportar o mesmo DOCX duas vezes | Arquivos distintos com bytes idênticos, usando sufixos de versão. |
| Persistência e backup | Aprovado | Abrir o banco em outro processo Python; criar e reabrir backup | Número, cadastros, configuração e bytes DOCX preservados. |
| Reinicialização do servidor | Aprovado | Dois ciclos reais de iniciar/encerrar Streamlit com banco de teste populado | Portaria 9 preservada, próximo número 10, DOCX idêntico em ambos; `reinicializacao.json`. |
| Geração DOCX | Aprovado | Exemplos 5, 6 e 8 em `exemplos/` e inspeção OOXML | Templates derivados dos originais, com correções de redação. |
| Geração PDF pelo Word | Aprovado | Conversão real dos três exemplos usando o conversor da aplicação | Três PDFs válidos, cada um com uma página. |
| PDF sem conversor | Aprovado | Simulação de ausência de Word/LibreOffice | Mensagem recuperável; DOCX e ato finalizado continuam disponíveis. |
| PDF pelo LibreOffice | Não validado em conversão real | Caminho alternativo implementado, mas executável ausente | A validação visual foi feita com Word. Não foi declarado sucesso do LibreOffice. |
| Referências originais | Aprovado | Comparação SHA-256 dos 16 arquivos antes/depois | Todos inalterados; `integridade-referencias.json`. |

## Comparação visual dos exemplos

Os oito pares de referência foram analisados. Os DOC foram convertidos em **cópias** para examinar o pacote e as propriedades; os PDFs históricos serviram de referência visual. Os PDFs finais dos exemplos 5, 6 e 8 foram renderizados em imagens e todas as páginas foram examinadas.

| Elemento | Resultado |
|---|---|
| Papel e margens | A4 e margens preservadas nas seções DOCX. PDFs com 595,32 × 842,04 pt. |
| Logo | Mesma imagem institucional, com posição e escala visual equivalentes. Diferença máxima da caixa PDF: 0,25 pt, aproximadamente 0,09 mm. |
| Corpo, título e data | Times New Roman 13 pt; corpo justificado; data à direita; recuos e entrelinha dos modelos preservados. |
| Negritos | Nomes, períodos, assentos, funções específicas e “R E S O L V E” destacados. |
| Assinatura | Nome em maiúsculas, 13 pt e negrito; cargo em 11 pt. Nome e cargo alinhados ao mesmo centro da tabela institucional. |
| Notas | Texto do § 3º em rodapé real; marcador ligado à citação e separador visível. |
| Paginação | Uma página em cada exemplo. Sem texto cortado ou sobreposição observada. |
| Diferenças deliberadas | Base por extenso acrescenta uma linha ao preâmbulo; assinatura usa maiúsculas em vez dos versaletes de alguns originais; datas por extenso e gênero corrigidos. |

A comparação mede fidelidade institucional com as correções exigidas, não identidade pixel a pixel. Detalhes do padrão e correções: `PADRAO_INSTITUCIONAL.md`. Medidas dos PDFs: `comparacao-visual.json`. O renderizador auxiliar baseado em LibreOffice não pôde executar por ausência de `soffice.exe`; a renderização efetiva foi feita com Microsoft Word e as imagens foram conferidas.

## Estado para primeiro uso

O banco de uso real contém os sete cadastros iniciais, sem Portarias de teste e sem confirmação artificial da sequência. O usuário deve confirmar a última Portaria emitida em Configurações. Os exemplos em `docs/exemplos/` não representam atos emitidos pelo banco de produção.

O fallback LibreOffice e a paginação em outras instalações não foram validados neste ambiente. Não foram encontrados erros remanescentes nos cenários executados; os resultados não dispensam a conferência do texto de cada ato antes de sua emissão.
