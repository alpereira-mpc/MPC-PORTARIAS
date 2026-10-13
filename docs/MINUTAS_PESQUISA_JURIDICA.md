# Pesquisa Jurídica — implantação da Fase 3B.5A

A pesquisa consulta diretamente os textos já extraídos dos pareceres definitivos.
Não há tabela ou cópia de conteúdo adicional. Alterações e exclusões da extração
passam a valer imediatamente na consulta.

Antes da publicação do código, execute manualmente no SQL Editor o arquivo
`database/minutas_pesquisa_juridica.sql`. Ele cria, de forma idempotente, um índice
GIN parcial sobre `to_tsvector('portuguese', texto_integral)`. O script não altera
RLS, não concede acesso e não reprocessa PDFs.

A consulta usa `websearch_to_tsquery`, restringe resultados a pareceres com status
`PROCESSADO`, versão atual do extrator e vínculo consistente com a referência
cadastrada. Os trechos são recuperados das páginas persistidas em `unidades_json`.

Esta fase implementa pesquisa lexical, não busca semântica. Flexões reconhecidas
pelo dicionário português e expressões explícitas são contempladas, mas sinônimos e
conceitos não escritos no documento não são inferidos.
