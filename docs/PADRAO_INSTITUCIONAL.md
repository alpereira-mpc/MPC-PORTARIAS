# Padrão institucional dos documentos

## Fontes e integridade

Foram examinados os oito PDFs e as oito cópias DOCX convertidas dos DOC originais com Microsoft Word. Os originais em `referencias/` são imutáveis. Os hashes SHA-256 anteriores e posteriores à implementação constam de `integridade-referencias.json`.

Os pacotes em `templates/` derivam das Portarias 8/2026 (`simples.docx`), 6/2026 (`multipla.docx`) e 5/2026 (`com_nota.docx`). A assinatura é uma tabela; não aparece apenas na lista de parágrafos do corpo. O logotipo está em `word/media/image1.jpeg`. As demais referências confirmam os padrões de substituição, assinatura em exercício e notas.

## Medidas extraídas

Todos os oito DOCX têm uma seção A4 de 595,35 × 842,00 pontos; margem superior 35,45 pt (1,25 cm), inferior 56,70 pt (2 cm), esquerda 85,05 pt (3 cm), direita 56,70 pt (2 cm). Distâncias de cabeçalho e rodapé: 35,45 pt. Parágrafos principais usam Times New Roman 13 pt, entrelinha 1,3 e recuo esquerdo adicional de 26,95 pt. O recuo de primeira linha no corpo é 171,05 pt. A data tem alinhamento à direita; corpo justificado.

O PDF posiciona a caixa da imagem em x=243,60 pt e y=35,40 pt, com 163,55 × 105,70 pt. A área branca interna faz parte da imagem original; o brasão visível é menor. O DOCX preserva a imagem e sua geometria. Na comparação entre PDFs, o original tem x=243,50 pt e caixa 163,80 × 105,95 pt: a conversão introduziu diferenças de até 0,25 pt (0,09 mm), sem alteração perceptível do logo. Logo extraído: JPEG 1092 × 768 pixels. As fontes PDF aparecem com pequenas quantizações (por exemplo 12,96 pt para o corpo de 13 pt).

## Slots editáveis e preservação

`word/document.xml`: título identificado por `PORTARIA`, data por `João Pessoa`, preâmbulo pela expressão `no uso de suas atribuições`, parágrafos de substituição por `R E S O L V E`, assinatura nos dois primeiros parágrafos não vazios da tabela. O gerador preserva propriedades dos parágrafos e copia o parágrafo institucional para substituições adicionais.

São preservados imagem, estilos, geometria da seção, cabeçalhos/rodapés e relacionamentos existentes. Podem mudar os textos, runs de destaque, notas e suas referências, metadados de autoria e conteúdo da assinatura. Notas são partes OOXML reais com `footnoteReference`, separador e relação de pacote; não são texto simulado no fim da página.

## Correções deliberadas

- Portaria 4: “O PROCURADORA-GERAL” passa a “O PROCURADOR-GERAL EM EXERCÍCIO”.
- Portaria 5: “Procurador-Geral” na assinatura de Elvira passa a “Procuradora-Geral”.
- Portarias 7 e 8: anos “202” são rejeitados; datas são geradas por extenso com quatro algarismos.
- Portarias 3, 6, 7 e 8: a base inicial para Procuradoria/Subprocuradoria é art. 61, § 6º, conforme a especificação e o regimento conferido. A Portaria 1 citava art. 68, § 1º.
- Texto “Ministério Público Contas” recebe a preposição ausente.
- Assinatura gerada em maiúsculas, em Times New Roman 13 pt, com alinhamento central; os originais usavam versaletes de 15 pt em alguns nomes. O cargo permanece em 11 pt.
- A base por extenso acrescenta uma linha ao preâmbulo, deslocando o conteúdo posterior. Essa diferença decorre do texto exigido e não foi ocultada mediante redução de fonte.

Os exemplos 5, 6 e 8 foram renderizados pelo Word, examinados visualmente e mantêm uma página. A fidelidade foi avaliada com as correções acima, sem reproduzir os erros antigos. A forma abreviada, a pontuação e algumas quebras de linha não são idênticas aos documentos históricos.
