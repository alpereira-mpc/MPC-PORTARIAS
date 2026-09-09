# Auditoria final — MPC-PB — Gerador de Portarias PROGE

Data: 09/09/2026. Ambiente: Windows, Python 3.12, Streamlit 1.49.1, Microsoft Edge e Microsoft Word 15.0 instalado neste computador.

## Conclusão

**APTO PARA USO REAL** no ambiente verificado, com Microsoft Word disponível para exportação PDF. Nenhuma Portaria oficial foi finalizada durante a auditoria. Os testes de finalização, cancelamento e concorrência utilizaram exclusivamente bancos temporários.

## Banco de produção e isolamento

A consulta a `data/mpc.db` foi feita com conexão SQLite `mode=ro`. A comparação integral antes/depois confirmou igualdade dos dados de todas as tabelas e da estrutura. `PRAGMA integrity_check` retornou `ok`.

- Há **zero registros em `portarias`**. As reproduções de **5/2026, 6/2026 e 8/2026 não estão registradas como atos oficiais finalizados**, nem como rascunhos, no banco de produção.
- Não havia dados de teste para remover; **nenhum dado foi excluído**.
- A sequência de 2026 já contém `ultimo=8`, com configuração confirmada. A próxima emissão real é **9/2026**; nenhuma emissão foi realizada para conferir isso.
- A instrumentação de `sqlite3.connect` durante as suítes inicial e final registrou os caminhos utilizados e bloquearia acesso ao banco de produção. Não houve tentativa bloqueada nem acesso à produção. A inspeção dos testes confirmou uso de `tmp_path`, fábrica de Store isolada no AppTest e caminho temporário explícito no subprocesso de reinicialização.
- A interação no navegador foi executada contra `tmp/auditoria_final/interface.db`, por `MPC_DB_PATH`, na porta 8503. A produção não foi usada para salvar sequer rascunhos.
- A geração das reproduções usou outro banco isolado, sem inserir portarias. O número impresso nas reproduções é apenas parâmetro do gerador documental.

Evidências: [estado inicial](producao-antes.json), [estado final](producao-depois.json), [conclusão do banco](banco-conclusao.json), [isolamento inicial](isolamento-inicial.json) e [isolamento final](isolamento-final.json).

## Resultados verificáveis

| ITEM | RESULTADO | EVIDÊNCIA | OBSERVAÇÃO |
|---|---|---|---|
| Testes | Aprovado | [JUnit final](testes-final.xml); [JUnit inicial](testes-inicial.xml) | 51 testes iniciais; 56 após correções, sem falhas. |
| Banco | Aprovado | [Consulta antes/depois](banco-conclusao.json) | Zero portarias; estrutura e dados íntegros e inalterados. |
| Isolamento dos testes | Aprovado | [Conexões SQLite](isolamento-final.json) | Sem acesso ou tentativa de acesso a `data/mpc.db`. |
| Próxima numeração | Aprovado | [Sequência de produção](banco-conclusao.json) | Última 8/2026; próxima 9/2026, já configurada. |
| Rascunho e prévia | Aprovado | [Navegador](navegador.json); [reinicialização](reinicializacao.json) | 17 rascunhos acumulados na instância de auditoria, todos sem número; sequência permaneceu em 8. |
| Cancelamento | Aprovado | `test_draft_preview_finalize_cancel_year` no JUnit | Ato temporário 9 cancelado conserva 9; próxima sequência continua em 10. |
| DOCX | Aprovado | [Medições do Word](word-gerados.json) | Word abriu as três reproduções e a prévia de Isabella; uma página cada. |
| PDF | Aprovado | [Métricas comparativas](comparacao-metricas.json); [prévia PDF](isabella-previa.pdf) | Exportação real pelo Word. Mesma paginação, texto e fontes do DOCX reconvertido. |
| Comparação visual | Aprovado com diferenças justificadas | [5](comparacao-5.png), [6](comparacao-6.png), [8](comparacao-8.png) | Sem cortes ou sobreposições. Diferenças intencionais descritas abaixo. |
| Quatro telas | Aprovado | [Texto de cada tela](telas.json); capturas `tela-*.png` | Navegação real no Edge, título e conteúdo conferidos após carregamento; também cobertas por AppTest. |
| Persistência | Aprovado | [Dois ciclos pelo CMD](reinicializacao.json) | Todas as tabelas iguais: banco, configurações, sequência, sete cadastros e 17 rascunhos. Histórico finalizado também preservado em teste de subprocesso temporário. |
| Concorrência | Aprovado | `test_concurrent_numbering`, `test_same_draft_concurrent` | Seis finalizações temporárias concorrentes produziram 9 a 14, sem repetição. Quatro chamadas para o mesmo rascunho consumiram apenas 9. |
| UNIQUE e transação | Aprovado | `test_duplicate_unique_constraint`, `test_generator_failure_rolls_back`; schema na consulta | `UNIQUE(ano,numero)` e `BEGIN IMMEDIATE`. Falha DOCX reverte status e sequência. |
| Falha PDF | Aprovado | `test_pdf_absent_is_recoverable` | Falha simulada preservou ato finalizado, DOCX e próxima sequência 10. |
| Exportações existentes | Aprovado | `test_export_never_overwrites` | Criação exclusiva `xb`; segunda exportação recebe sufixo de versão, preservando a primeira. |
| Gênero | Aprovado | [Isabella](isabella-previa.txt), [em exercício](em_exercicio-previa.txt), testes de redação/UI | Procuradora, Subprocuradora-Geral e Procurador-Geral em exercício coerentes; Histórico corrigido. |
| Datas | Aprovado | [Mesmo mês](isabella-previa.txt), [entre meses](procuradora_geral-previa.txt), [entre anos](entre_anos-previa.txt) | 8 a 14/09/2026; 29/06 a 09/07/2026; 29/12/2026 a 09/01/2027, todos por extenso e com ano completo. |
| Base legal | Aprovado | Redações das prévias e bases do banco | Art. 61, § 6º para PG/SubPG; art. 70, § 3º para Corregedoria/Ouvidoria. |
| Múltiplas substituições | Aprovado | [Duas sucessivas](sucessivas-previa.txt); testes de duas e três substituições | Segundo bloco com “R E S O L V E, ainda,” e período anterior; conflito independente da ordem corrigido. |
| Ouvidoria/Corregedoria | Aprovado | [Ouvidor](ouvidor-previa.txt), [Corregedor](corregedor-previa.txt), reprodução 5 | Nota real de rodapé com chamada sobrescrita e texto de antiguidade. |
| Personalizações | Aprovado | [Motivo livre](motivo_personalizado-previa.txt), [assento Outro](assento_outro-previa.txt) | Texto livre preservado. Base do cenário “Outro” explicitamente fictícia, restrita ao teste isolado. |
| Cadastro inicial | Aprovado | [Sete registros de produção](banco-conclusao.json); [tela](tela-procuradores.png) | Nomes, gênero, função e assento conferidos individualmente. |

As bases foram conferidas no [Regimento Interno publicado pelo TCE-PB](https://tce.pb.gov.br/wp-content/uploads/2024/12/REGIMENTOINTERNORNTCN07_2024.pdf), art. 61, § 6º e art. 70, § 3º. A base de “Outro” não constitui validação de uma hipótese jurídica personalizada.

## Conferência documental

Foram relidos os **oito DOC e oito PDF** originais. Os DOC foram abertos no Word através de cópias; os PDF tiveram texto extraído e todas as páginas renderizadas e inspecionadas. Os hashes dos 16 originais permaneceram iguais. Evidências: [DOC](referencias-doc.json), [PDF](referencias-pdf.json), [visão das oito páginas](originais-contato.png).

| Elemento | Verificação nas reproduções 5, 6 e 8 |
|---|---|
| Papel e margens | A4; Word 595,35 × 842 pt. Superior 35,45 pt (1,25 cm), inferior/direita 56,70 pt (2 cm), esquerda 85,05 pt (3 cm), iguais aos DOC originais. |
| Logotipo | Mesma imagem institucional. Posição aproximadamente x=243,5 pt, y=35,4 pt; dimensão 163,8 × 105,95 pt. Variação de exportação de até 0,25 pt, inferior a 0,1 mm. |
| Título e data | Times New Roman 13 pt; título em negrito, data alinhada à direita; posições institucionais preservadas. |
| Corpo, recuos e espaçamento | Times New Roman 13 pt, justificado, recuo esquerdo adicional 26,95 pt e primeira linha 171,05 pt; configuração de entrelinhas e parágrafos do modelo preservada. |
| Destaques | “R E S O L V E”, nomes, Câmara, períodos e funções de Ouvidor/Corregedor destacados conforme o modelo. |
| Assinatura | Centralizada na célula do modelo; nome em maiúsculas e negrito, 13 pt; cargo 11 pt e gênero correto. Originais usam versalete/15 pt: a mudança atende ao requisito de nome integralmente em maiúsculas. |
| Rodapé | Reprodução 5 tem nota verdadeira, chamada sobrescrita, separador e texto em 10 pt; 6 e 8 não recebem nota indevida. |
| Paginação | Originais, reproduções e prévia de Isabella: uma página. Sem página extra, corte de assinatura ou sobreposição com rodapé. |

Diferenças intencionais: inclusão de “de” em “Ministério Público de Contas”; referência legal completa, que acrescenta uma linha ao preâmbulo e desloca o corpo cerca de uma entrelinha; assinatura feminina corrigida na 5; art. 61, § 6º nas 6 e 8; período por extenso e ano 2026 completo na 8; eliminação da vírgula indevida após “R E S O L V E” na 8. Não foram copiados “O PROCURADORA-GERAL”, “Público Contas” ou anos “202” encontrados nos originais.

A prévia DOCX baixada pela tela foi aberta e reconvertida independentemente no Word. Ambos os PDF têm uma página e os mesmos 546 caracteres não brancos, fontes e tamanhos. A diferença máxima de coordenadas dos caracteres foi **0,24 pt (0,085 mm)**: aparência equivalente, mas não identidade pixel a pixel. As imagens foram inspecionadas; não há mudança de quebra de linha. Evidências: [DOCX](isabella-previa.docx), [PDF da tela](isabella-previa.pdf), [reconversão Word](isabella-word-reconversao.pdf).

## Cenário solicitado e cadastro

Na tela Nova Portaria foram selecionadas Isabella Barbosa Marinho Falcão, Subprocuradora-Geral, 1ª Câmara, substituída por Sheyla Barreto Braga de Queiroz, de 08/09/2026 a 14/09/2026, por gozo de férias regulamentares. A signatária foi Elvira Samara Pereira de Oliveira. A redação usa “designar a Procuradora”, “substituir a Subprocuradora-Geral”, art. 61, § 6º, e assinatura “Procuradora-Geral do Ministério Público de Contas da Paraíba”. Utilizaram-se exclusivamente Preparar prévia e downloads DOCX/PDF. O texto da tela exibe número previsto 9; os arquivos trazem `[PRÉVIA]`, sem reserva de número.

Cadastro de produção conferido: Elvira — Procuradora-Geral/Tribunal Pleno; Isabella — Subprocuradora-Geral/1ª Câmara; Bradson Tibério Luna Camelo — Subprocurador-Geral/2ª Câmara; Marcílio Toscano Franca Filho — Ouvidor; Manoel Antonio dos Santos Neto — Corregedor; Luciano Andrade Farias — Procurador; Sheyla Barreto Braga de Queiroz — Procuradora. Identificadores internos de função são canônicos; a apresentação aplica o gênero.

## Defeitos corrigidos nesta auditoria

1. **Assento personalizado perdido ao editar cadastro:** teste reproduziu a troca de “Câmara Especial” por “Tribunal Pleno”. O seletor agora reconhece “Outro” e conserva o texto salvo.
2. **Alerta de substituto afastado dependente da ordem:** teste reproduziu ausência de aviso ao inverter a posição das substituições independentes. A verificação agora é simétrica e não acusa indevidamente a sucessão marcada como decorrente.
3. **Função feminina apresentada no masculino:** seletores, editor de cadastro e Histórico agora exibem a forma adequada ao gênero, mantendo o identificador interno.
4. **Rascunho exibido como `None` no Histórico:** agora mostra “Rascunho”.

Foram acrescentados cinco testes de regressão e repetida integralmente a suíte após os ajustes. As tentativas iniciais do roteiro de navegador precisaram de esperas de carregamento e de seletores compatíveis com os controles do Streamlit; a execução final dos nove cenários foi concluída. Não houve novas funcionalidades, alteração das referências ou migração destrutiva do banco.

Nenhuma pendência relevante foi identificada nos cenários exigidos. As evidências refletem o Word e o ambiente local verificados; LibreOffice não foi necessário nem homologado nesta auditoria.
