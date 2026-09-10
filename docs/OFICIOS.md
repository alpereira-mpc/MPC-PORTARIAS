# Ofícios — Geração e Controle

O card Ofícios abre Visão Geral, Novo Ofício, Enviados, Recebidos e Acompanhamento. A Home não inicializa o módulo nem consulta o banco. Streamlit, deployment e módulos Portarias/Agenda foram preservados.

## Séries e modelos

| Membro da base existente | Série | Evidência / situação |
|---|---|---|
| Elvira Samara Pereira de Oliveira | PROGE | Cabeçalho dos ofícios consultados; pacote baseado no 007/2026 |
| Bradson Tibério Luna Camelo | BTLC | Conteúdo do 01/2026, apesar do nome de arquivo BLTC |
| Luciano Andrade Farias | LAF | Confirmada pela solicitação; sem modelo próprio localizado |
| Isabella, Marcílio, Manoel e Sheyla | Não confirmada | Não foi identificada série documental própria; não foram inventadas siglas |

Os nomes são resolvidos contra os IDs do catálogo existente somente na instalação inicial. Não há segunda base de membros. Membros sem série podem salvar rascunhos, mas não finalizar. LAF também exige confirmação do cabeçalho/modelo antes da primeira geração. Na Visão Geral, configure a sigla comprovada, o pacote institucional, o cabeçalho com `{numero}` e `{ano}` e a quantidade de dígitos. Uma série utilizada oficialmente não pode ter seu padrão alterado nesta V1.

O pacote PROGE preserva `Ofício MPC/PB - PROGE n. 007/2026` (três dígitos); BTLC preserva `Ofício BTLC-MPC-PB nº 01/2026` (dois). Os modelos PROGE de 2025 variam entre 017, 18 e 20; o padrão adotado para novas emissões é o modelo 007/2026. Margens, logomarca, estilos e espaços em branco provêm de cópias dos pacotes originais. Os originais em `referencias/` não são alterados.

## Numeração e geração

As sugestões encontradas são PROGE: próximo 21/2025 e 008/2026; BTLC: próximo 02/2026. A pasta não comprova completude. Em **Visão Geral → Séries e configuração inicial das sequências**, confirme o próximo número oficialmente disponível, para cada série/ano. A confirmação não pode retroceder abaixo das referências conhecidas nem de uma sequência anteriormente confirmada.

Salvar/editar rascunho e baixar prévia não consomem número. **Finalizar e gerar ofício** trabalha sobre o rascunho salvo, valida membro/série, gera DOCX e PDF e persiste os dois arquivos e a sequência na mesma transação. Falha de conversão mantém o rascunho e não consome número. Repetir a finalização do mesmo registro devolve o número já atribuído.

Há unicidade de `(serie, ano, numero)`. SQLite usa `BEGIN IMMEDIATE`; PostgreSQL reutiliza o lock transacional de escrita e o pool do Store. Números são independentes por série/ano. Cancelamento mantém o número, motivo, data e histórico. Apenas rascunhos podem ser excluídos, mediante confirmação; não existe exclusão administrativa de ofício numerado nesta V1.

O corpo aceita texto simples; linhas em branco separam parágrafos reais. Vocativo, fechamento e título da assinatura são editáveis. A conversão reutiliza `document_generator/pdf.py`, com Word local ou LibreOffice no Cloud. Não há envio de documentos para conversores externos.

## Controle e arquivos

Enviados: Rascunho, Gerado, Enviado, Aguardando resposta, Respondido, Concluído e Cancelado. Recebidos: Recebido, Em análise, Aguardando providência, Encaminhado, Respondido, Concluído e Arquivado. As listas de estados ficam centralizadas no serviço.

Recebidos admitem vários destinatários internos e vários originais PDF/DOCX. Cada upload é validado pelo conteúdo e limitado a 10 MB (DOCX também tem limite de expansão de 50 MB e rejeita VBA). PDFs protegidos não são aceitos. Arquivos nunca são executados. Os nomes de download são sanitizados para Windows/Linux.

Os arquivos ficam em BLOB no SQLite e BYTEA no PostgreSQL, com nome, MIME, tamanho e instante de inclusão. Não dependem do filesystem efêmero do Streamlit Cloud. `files()` consulta somente metadados; `download()` é o ponto isolado de recuperação dos bytes. A interface só busca bytes após **Preparar download**. Não há URLs públicas de documentos.

O vínculo opcional enviado → recebido aparece nos detalhes dos dois registros e não muda status automaticamente. Prazos vencidos e próximos (janela de sete dias) aparecem com texto/ícone. Acompanhamento filtra no banco antes da paginação; listagens têm 50 registros por página. Visão Geral usa uma consulta agregada. As leituras de listagens e indicadores no PostgreSQL usam TTL de 30 segundos e invalidação por revisão pública do Store; detalhes e arquivos são buscados sob demanda.

A modelagem é aditiva: `oficios`, `oficio_series`, `oficio_sequencias`, `oficio_destinatarios`, `oficio_arquivos` e `oficio_movimentacoes`. O marcador de instalação fica em `configuracoes`; a inicialização é idempotente. Movimentações guardam instante UTC, estado anterior/novo e observação. Configurações e exclusões de rascunho usam o log de eventos existente. Datas operacionais aparecem em formato brasileiro.

## Validação e segurança

`tests/test_oficios.py` cobre numeração, concorrência, rollback, uploads, reabertura em outro processo, filtros, prazos, relações e navegação. Os testes PostgreSQL exigem banco descartável local com a proteção já utilizada pelo projeto. `MPC_TEST_OFFICE=1` habilita o teste de conversão real, sem substituir o conversor por simulação.

`scripts/validate_oficios.py` gera exemplos com o mesmo conteúdo de PROGE 007/2026 e BTLC 01/2026, converte pelo mecanismo existente e renderiza páginas e referências em `tmp/oficios-visual/`. PyMuPDF é ferramenta de desenvolvimento para essa conferência, não dependência de execução do módulo.

**Enquanto o portal não tiver autenticação, não armazene documentos sensíveis ou confidenciais em produção.** Antes do primeiro uso oficial, confira a sequência de cada série e faça um teste de upload/download e conversão no ambiente Streamlit Cloud. Testes locais não substituem a verificação das permissões e do LibreOffice no deployment efetivo.

### Conferência visual local — 10/09/2026

- PROGE 007/2026 e BTLC 01/2026: geração com o conteúdo dos modelos, conversão real pelo Microsoft Word e inspeção de todas as páginas renderizadas. Um ofício por página, sem cortes/sobreposição; logomarca, margens, recuos, assinatura e distribuição vertical conferidos.
- Comparação raster na mesma resolução (857 × 1109): 0,355% dos pixels PROGE e 0,231% BTLC diferem por mais de 25 níveis de cor. Há pequenas diferenças de composição dos runs/assinatura; não se afirma identidade binária ou pixel a pixel.
- O `render_docx.py` da skill foi tentado e diagnosticou ausência de LibreOffice neste Windows. A conferência foi concluída com o conversor Word já existente e rasterização local do PDF. LibreOffice/Cloud não foram visualmente validados nesta máquina.
- Regressão visual Portarias 5, 6 e 8/2026: DOCX e PDF reais, uma página cada e sem cortes. Permanecem as diferenças preexistentes entre os casos de teste atuais e os originais (base legal, redação e datas completas). O gerador de Portarias não foi alterado.

### Arquivos desta implementação

Criados: `database/oficios.py`, `services/oficios.py`, `services/oficios_ui.py`, `document_generator/oficios.py`, `templates/oficios/PROGE.docx`, `templates/oficios/BTLC.docx`, `tests/test_oficios.py` e `scripts/validate_oficios.py`, além desta documentação.

Alterados: `portal.py` (card/rota), `requirements.txt` (validação PDF com pypdf) e `tests/test_portal.py` (card ativo). A versão do Streamlit não mudou; não foram alterados Store, backend PostgreSQL, pool, Agenda, gerador de Portarias ou deployment.

### Resultado final dos testes

Suíte completa: **396 testes aprovados, zero falhas, zero ignorados**, em 402,48 segundos. Inclui 36 testes de Ofícios e regressões existentes de Portarias, Agenda, Home, cache/pool, SQLite e PostgreSQL. Execução com PostgreSQL 17.6 descartável em loopback, TLS e guarda de teste; `MPC_TEST_OFFICE=1` habilitado para DOCX/PDF reais via Word. O banco de produção não foi acessado.

Também foi conferida com AppTest a abertura de rascunho cujo signatário foi inativado. Black, compilação dos novos módulos e `git diff --check` passaram. Commit e push não foram executados. Antes de implantar em produção, resta o teste operacional de conversão/upload/download no próprio Streamlit Cloud e a confirmação administrativa das sequências.
