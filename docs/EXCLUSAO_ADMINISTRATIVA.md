# Exclusão administrativa de Portarias

## Uso no Histórico

Abra a Portaria no Histórico. **Cancelar Portaria** mantém o ato, o histórico e o número ocupado. **Excluir Portaria definitivamente**, na área administrativa, remove o cadastro ativo e pode liberar o número. A decisão de excluir um ato que não foi emitido oficialmente pertence ao usuário.

Para excluir uma finalizada ou cancelada, escolha o motivo, marque a confirmação e digite exatamente `EXCLUIR`. Em “Outro”, descreva obrigatoriamente o motivo. O botão final permanece desabilitado até o preenchimento. A rotina no servidor também verifica essas condições, o ID e o status atual. Rascunhos usam confirmação simples e nunca alteram a sequência.

O Registro de exclusões fica separado da lista normal e permanece disponível mesmo sem portarias ativas. Ele contém data/hora, identificação do ato, status anterior e motivo. Não existe restauração automática nesta versão.

## Backup, migração e sequência

O esquema SQLite passa de versão 1 para 2 por migração transacional, sem recriar o banco. Antes da migração de banco existente e antes de cada exclusão, a aplicação cria um backup usando a API SQLite, incluindo dados já confirmados no WAL. Os arquivos ficam ao lado do banco em `backups/` — em produção, `data/backups/` — com timestamp, identificador único e criação exclusiva. O backup é verificado com `PRAGMA integrity_check`. Se falhar, a exclusão não inicia.

`sequencia_baselines` guarda a reserva administrativa anterior ao aplicativo. Na migração, essa informação vem dos eventos de configuração da sequência; para o banco verificado, 2026 tem baseline 8. Se não houver evidência histórica, a migração preserva conservadoramente a sequência atual, sem presumir números externos livres. Uma configuração administrativa posterior que avance além dos atos existentes também reserva os números externos.

A exclusão executa `BEGIN IMMEDIATE`, verifica novamente o registro, cria backup, registra o resumo, remove relações e ato, calcula a sequência e grava o log antes do commit. Um gatilho bloqueia exclusões comuns de finalizadas/canceladas; a autorização é local à conexão e restrita ao ID da operação administrativa.

O novo último número é o maior número restante entre finalizadas/canceladas, respeitando o baseline. Se houver ato superior ao excluído, a sequência não diminui. Não se subtrai simplesmente um número. Canceladas continuam incluídas no cálculo; rascunhos e logs não ocupam número. O mesmo bloqueio de escrita serializa finalização e exclusão. Repetir a exclusão de um ID já removido não afeta outro ato que tenha reutilizado o número.

## Arquivos e quarentena

Cada nova exportação em disco registra ID da Portaria, caminho absoluto, formato e SHA-256 em `exportacoes`. A escrita e o vínculo são protegidos contra exclusão concorrente. Arquivos antigos não possuem esse vínculo: **não são inferidos pelo nome e não são apagados automaticamente**. Downloads feitos pelo navegador fora do diretório gerenciado e prévias mantidas apenas em memória também não são arquivos vinculados.

Com “Excluir também os arquivos DOCX/PDF associados” marcado:

1. A rotina considera exclusivamente os caminhos registrados. Caminhos redirecionados, links simbólicos ou conteúdo alterado são preservados.
2. Antes do commit, cria uma cópia exclusiva e verificada em `excluidos/<identificador único>/` dentro do diretório de cada exportação. Na configuração padrão, é `exports/excluidos/`.
3. O banco confirma a exclusão e registra os caminhos originais, a quarentena e os estados dos arquivos.
4. Somente depois do commit, a rotina confere novamente os hashes e remove o original. A cópia de quarentena permanece recuperável manualmente.

SQLite e o sistema de arquivos não compartilham uma transação. Essa ordem garante que um rollback preserve os arquivos originais. Se ocorrer falha antes do commit, uma cópia adicional poderá permanecer na quarentena; o banco e os originais permanecem intactos. Se o processo parar depois do commit, o estado `PENDENTE_REMOCAO` identifica uma cópia preservada e um original possivelmente ainda presente. A aplicação não faz limpeza automática desses casos.

Arquivos ausentes recebem estado `AUSENTE`. Erros de permissão ou arquivos abertos no Word são registrados como `MANTIDO_ERRO`; alterações de conteúdo/caminho recebem `MANTIDO_ALTERADO`. Isso não desfaz nem corrompe a exclusão já confirmada no banco. Confira os estados na seção Registro de exclusões. Desmarcar a opção preserva todos os arquivos vinculados. Nunca se sobrescreve uma exportação, cópia de quarentena ou backup existente.

## Proteção de texto

O template antigo de licença especial é resolvido por gênero antes de salvar novos rascunhos ou apresentar conteúdo legado. O motivo padrão atualizado é “por motivo de gozo de licença especial”, sem marcador interno. A migração modifica somente a redação padrão exata que continha o defeito; não reescreve documentos finalizados nem personalizações.

Um validador recursivo rejeita marcadores entre chaves em campos da Portaria e edições manuais antes de rascunho, prévia, finalização e DOCX. A conversão PDF inspeciona também os textos do pacote DOCX, inclusive marcadores divididos entre elementos XML. Motivos personalizados são preservados conforme digitados, sem acrescentar outra expressão “por motivo de”.

## Testes

Execute `python -m pytest -q` no ambiente do projeto. Os testes usam bancos temporários. `tests/test_deletion.py` cobre os casos de sequência, backup, rollback, logs, arquivos, migração e concorrência; `tests/test_placeholders.py` verifica os motivos e barreiras de validação; `tests/test_ui.py` verifica confirmação e exclusão pelo fluxo da tela. Nenhum teste automatizado deve acessar `data/mpc.db`.
