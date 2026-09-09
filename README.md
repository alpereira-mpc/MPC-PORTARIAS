# MPC-PB — Gerador de Portarias PROGE

Aplicação local para redigir, conferir e emitir Portarias da Procuradoria-Geral do Ministério Público de Contas da Paraíba. Interface Streamlit, banco SQLite e documento principal DOCX. Os dados e documentos não são enviados a APIs externas. A conexão é necessária apenas para instalar dependências.

## Iniciar neste computador

Execute `Iniciar_MPC.cmd` ou, no PowerShell aberto nesta pasta:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Abra http://127.0.0.1:8501. Para encerrar, pressione Ctrl+C no terminal. O servidor aceita conexões somente do próprio computador.

## Instalação em outro computador

Requisitos: Python 3.12 de 64 bits; Windows recomendado para conversão com Microsoft Word. No PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Em Linux/macOS, use Python 3.12, `python3 -m venv .venv` e `.venv/bin/python`; instale LibreOffice para PDFs. Preserve `templates/` e `assets/` junto ao código. Os modelos DOC originais não são necessários durante a execução, mas devem permanecer intactos em `referencias/`.

## Primeiro uso

1. Abra **Configurações → Numeração anual** e confirme a última Portaria realmente emitida. Para 2026, a sugestão é 8; não é aplicada sem confirmação. Após confirmar 8, o próximo número será 9.
2. Confira os sete procuradores iniciais, os ocupantes das funções, as bases legais e o diretório de exportação. Nomes e funções iniciais vêm da especificação e são editáveis.
3. Em **Nova Portaria**, escolha titular, substituto, função, assento, datas, motivo e signatário. O substituto não é escolhido automaticamente.
4. Adicione outras substituições se necessário. Cada uma pode ter período próprio e motivo próprio ou decorrente da anterior.
5. Prepare a prévia. Confira o texto e, se necessário, edite somente aquela Portaria. Restaure a redação automática a qualquer momento.
6. Clique em **FINALIZAR PORTARIA**. O DOCX fica preservado no banco e é exportado. O PDF é gerado pelo botão independente.

Prévia DOCX/PDF recebe identificação `[PRÉVIA]`; o número exibido na tela é uma previsão. Se outro usuário local finalizar antes, o número definitivo será recalculado sob transação. Rascunhos não reservam número. Uma Portaria cancelada mantém seu número. Duplicar cria um rascunho sem número e restaura o texto automático.

## Banco, arquivos e recuperação

Na versão 1.1, o Histórico oferece **Excluir Portaria definitivamente** com motivo, confirmação textual, backup automático e log administrativo. A exclusão da última Portaria recalcula a sequência respeitando o baseline anterior ao aplicativo; cancelamento continua ocupando número. Rascunhos têm exclusão com confirmação simples. Consulte o [guia de exclusão e quarentena](docs/EXCLUSAO_ADMINISTRATIVA.md).

- Banco: `data/mpc.db`, relativo à pasta da aplicação, independentemente do diretório do terminal.
- Exportações: `exports/` por padrão, alterável nas Configurações.
- Logs: `data/mpc.log`, com rotação de até três arquivos adicionais.
- Backup consistente: **Configurações → Backup local**. Para restaurar, encerre o aplicativo, preserve o banco atual e seus arquivos WAL/SHM como conjunto de segurança e substitua o banco pelo backup usando um diretório limpo. Não troque bancos com o programa aberto.
- Testes: bancos e exportações temporários; não alteram o banco de uso real.
- Exemplos: `docs/exemplos/`, com as Portarias 5, 6 e 8/2026 geradas para validação; não são registros de emissão.

A inicialização tem versão de esquema e não recria cadastros nem reinicia sequências. Uma finalização grava número, dados e DOCX na mesma transação. Se a exportação em disco falhar, reexporte pelo Histórico: o DOCX continua no banco. Arquivos existentes recebem uma nova versão (`_v2`, `_v3`); nunca são sobrescritos. Atos finalizados preservam dados e DOCX originais mesmo após alterações dos cadastros/templates. Cancelamento exige confirmação e motivo, registrados no histórico.

## PDF e identidade visual

No Windows, o modo Automático tenta **Microsoft Word via pywin32**, em processo separado, e depois **LibreOffice headless**. Word deve estar instalado e operacional para o mesmo usuário. Cada tentativa tem limite de 55 segundos. Sem conversor funcional, o aplicativo apresenta uma mensagem e mantém o DOCX disponível.

LibreOffice é localizado no PATH ou nos diretórios usuais; `MPC_LIBREOFFICE` pode indicar seu executável. Word foi validado neste computador. LibreOffice não estava instalado, portanto sua conversão real não foi validada aqui. Para reproduzir a mesma paginação em outro computador, use Word e Times New Roman; a renderização entre programas pode variar.

Os templates preservam os pacotes institucionais convertidos de cópias dos documentos originais: A4, margens, espaçamento, logo, título, corpo e assinatura. O logo foi extraído em JPEG de 1092 × 768 pixels, incluindo a área branca original. Um PNG oficial de alta resolução pode melhorar a impressão, mas não é necessário para funcionar. As notas são notas de rodapé reais do Word.

A redação corrige gênero, anos truncados, a expressão “Ministério Público de Contas” e a fundamentação. A base legal por extenso ocupa uma linha adicional em relação aos exemplos antigos. Os três documentos de validação permanecem com uma página. Veja `docs/VALIDACAO.md`.

## Bases legais

A configuração inicial usa art. 61, § 6º, para Procuradoria-Geral/Subprocuradoria-Geral e art. 70, § 3º, para Corregedoria/Ouvidoria. Conferência feita no [Regimento Interno RN-TC nº 07/2024 publicado pelo TCE-PB](https://tce.pb.gov.br/wp-content/uploads/2024/12/REGIMENTOINTERNORNTCN07_2024.pdf), páginas impressas 30 e 34. As bases e notas são editáveis no banco; funções sem base predefinida exigem preenchimento. Avisos de coincidência de períodos e situações incomuns permitem confirmação. O sistema não determina juridicamente quem deve substituir o titular.

## Desenvolvimento e testes

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m black app.py database services document_generator tests scripts
```

`app.py` contém as telas; `database/` trata persistência; `services/` contém redação, validação e exportação; `document_generator/` gera DOCX/PDF. `database/seed.json` é usado somente na criação inicial do banco. `MPC_DB_PATH` permite apontar uma instância separada para outro banco, por exemplo em homologação. Não coloque bancos, logs nem documentos emitidos no controle de versão.

Para gerar novos exemplos isolados: `python scripts/generate_examples.py`. As evidências dos testes estão em `docs/VALIDACAO.md` e `docs/test-results.xml`.

A atualização administrativa e a limpeza autorizada da Portaria de teste 9/2026 estão documentadas em [Relatório da atualização](docs/atualizacao-administrativa/RELATORIO.md), com testes, backups e conferência do banco após reiniciar.
