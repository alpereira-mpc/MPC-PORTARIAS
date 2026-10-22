# Auditoria: identidade e organização sancionatória das Minutas IA

Intervenção local de 22/10/2026. Não houve acesso a produção, Secrets,
migrations, alteração de permissões, edição de acervo, commit ou criação de branch.

## Causas encontradas

O cabeçalho era reconstruído por `_normalize_structure` a partir do processo,
independentemente da identificação presente no relatório gerado. A extração de
metadados aceitava poucos rótulos e construções narrativas. Campos depois do nome
(como “Sebastião Pinto Dantas, Prefeito Municipal”) e rótulos inseridos no meio de
um parágrafo extraído do PDF não eram reconhecidos. A normalização da extração
pode juntar linhas do PDF. Na ausência do metadado, o cabeçalho recebia “Não
informado”, sem bloquear a exportação.

Na exportação da instrução local, as linhas do cabeçalho eram conciliadas, mas o
objeto passado ao renderizador/pós-processamento continuava sem os metadados.
Modelos com todos os campos reconhecidos, mas sem campo de responsável, podiam
terminar o preenchimento sem acrescentar essa identificação.

O prompt exigia texto de `analise_sancionatoria` em todos os casos. Markdown,
DOCX padrão e os dois caminhos de composição dos modelos cadastrados inseriam
explicitamente o título “Análise sancionatória”. Não era apenas comportamento
espontâneo do modelo de IA.

## Correção e critérios

- A resolução utiliza somente metadados do processo e documentos atuais já
  selecionados pelo fluxo. RAG e modelos visuais não fornecem identidade.
- São reconhecidos rótulos e relações explícitas entre nome e cargo, inclusive
  nome antes/depois do cargo, em linhas ou parágrafos extraídos. Menções a gestor
  atual, papéis processuais e exercícios diferentes são desconsideradas.
- Responsáveis sucessivos mantêm os períodos documentados. Vários nomes sem
  delimitação segura ou declaração coletiva geram pendência; a IA não desempata
  candidatos. Divergência com metadados também exige revisão.
- `identidade_responsaveis` registra valor, exercício, evidências com fonte/página
  e pendências na estrutura normalizada. O mesmo valor alimenta contexto,
  identificação, renderização e pós-processamento.
- Ausência de identidade e divergências explícitas no corpo aparecem como
  advertências. O DOCX é bloqueado até a resolução. A saída efetiva é conferida
  em parágrafos, tabelas, caixas de texto e cabeçalhos/rodapés.
- O campo sancionatório permanece como controle interno para não remover as
  validações jurídicas existentes. O prompt exige fundamentação dentro das
  questões pertinentes, sem capítulo genérico; admite texto de controle vazio.
- Texto legado com vínculo único é integrado à questão correspondente, de modo
  idempotente. Multa sem vínculo inequívoco do texto exige nova geração. Títulos
  genéricos reconhecidos são recusados no conteúdo e na saída DOCX. Pedidos e
  fundamentos legítimos são preservados, sem criação automática de multas.

## Abrangência e preservação visual

Os caminhos de geração histórica de RPPS e de instrução PCA compartilhada são
contemplados: Prefeituras, Câmaras, RPPS e Secretarias, inclusive perfis estaduais
que utilizem esse motor quando habilitados. Não se altera a seleção/habilitação
de especialidades, as competências jurídicas, a orientação obrigatória ou o RAG.

Nenhum arquivo de modelo foi alterado. Não houve mudança em fontes, margens,
imagens, estilos, numeração ou assinaturas. A única inclusão estrutural necessária
é o campo de responsável quando o modelo não o contém: o parágrafo é clonado da
identificação do próprio gabinete. Isso pode alterar a paginação pelo acréscimo
de conteúdo; não se afirma identidade visual pixel a pixel ou paginação idêntica.

## Arquivos

| Arquivo | Finalidade |
| --- | --- |
| `services/minutas_consistencia.py` | Resolução de identidade, evidências, conflitos e integração de sanções. |
| `services/minutas_generation.py` | Propagação, estrutura normalizada, advertências e Markdown sem capítulo separado. |
| `services/ai_service.py` | Instruções de identidade e fundamentação por irregularidade. |
| `services/minutas_docx.py` | Bloqueios, conferência da saída e passagem do alvo conciliado ao renderizador. |
| `services/minutas_modelo_docx.py` | Composição sem capítulo separado; campo obrigatório ausente com estilo herdado. |
| `tests/test_minutas_consistencia.py` | Casos novos, incluindo PDF extraído e DOCX reais com conteúdo sintético. |
| `tests/test_minutas_generation.py` | Ajuste dos contratos e exercícios das fixtures ao comportamento seguro. |
| `tests/test_minutas_docx.py` | Identidade nas fixtures e ausência de capítulo autônomo. |
| `tests/test_minutas_instrucao.py` | Identidade documental sintética no fluxo de instrução. |
| `tests/test_minutas_modelo_docx.py` | Exportação nos sete perfis e preservação dos modelos sintéticos. |
| `docs/minutas_identidade_e_sancoes.md` | Registro desta auditoria e limites de homologação. |

## Validação e limites

A rodada ampliada focalizada aprovou 312 testes, com três pulados: amostra local
do modelo do assessor ausente, referências locais de PCA ausentes e LibreOffice
indisponível. Incluiu orientação, Denúncias, Recursos, Aposentadorias e os DOCX
das Portarias 5, 6 e 8/2026. A suíte completa não foi executada.

A rodada final dos dez arquivos de testes de Minutas afetados/dependentes, em
diretório temporário exclusivo, aprovou 271 testes com os dois pulos por amostras
locais ausentes. A compilação de todos os Python alterados e `git diff --check`
passaram. Uma repetição intermediária sobreposta encontrou bloqueio do diretório
temporário pelo Windows; a repetição final isolada eliminou esse erro de ambiente.

Foram verificados estilos, margens, dimensões, logotipos, tabelas, ementa, assinatura
e numeração nos testes existentes, além de exportação com os sete perfis e
modelos sintéticos. O caso 02659/25 foi reproduzido com PDF sintético: extração
real, conciliação e geração efetiva de DOCX, sem regra particular de produção.
O documento original desse processo não foi disponibilizado nesta execução.

A geração por IA nos testes usa respostas controladas. Não houve chamada externa
de IA, leitura dos modelos cadastrados em produção ou homologação no Streamlit
Cloud. Não foi validado PDF final por conversor. OCR defeituoso, identificação em
redação não reconhecida e ambiguidade documental podem exigir revisão: a política
é bloquear o DOCX sem identidade segura, nunca adivinhar. As verificações de
sanções não substituem a revisão jurídica sobre cabimento e fundamentação.
