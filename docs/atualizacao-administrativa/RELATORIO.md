# Atualização administrativa — MPC-PB 1.1

Data: 09/09/2026. Projeto existente preservado: Python/Streamlit, SQLite, templates institucionais e conversão Microsoft Word. Nenhuma nova Portaria real foi finalizada.

## Resultado

**ATUALIZAÇÃO CONCLUÍDA E BANCO PRONTO PARA USO REAL**

A Portaria **9/2026 de teste** e o rascunho identificado foram removidos pela nova ação no Histórico, depois dos testes em bancos isolados e da criação dos backups. O banco de produção está íntegro, sem portarias ativas, com **baseline 8, última sequência 8 e próxima numeração 9/2026**. Os sete procuradores e as configurações existentes foram preservados. O aplicativo foi reiniciado pelo `Iniciar_MPC.cmd` e as quatro telas foram verificadas novamente.

## Evidências

| ITEM | RESULTADO | EVIDÊNCIA | OBSERVAÇÃO |
|---|---|---|---|
| Backup | Aprovado | [Estado inicial e backup](producao-antes.json); caminhos dos backups no [estado final](producao-depois.json) | Cinco backups: antes da atualização, migração, vínculo legado e de cada uma das duas exclusões. Nenhum sobrescrito. |
| Migration | Aprovado | [Migração real](migracao-producao.json) | Esquema 1 → 2; backup prévio; integridade `ok`; banco não recriado. |
| Exclusão definitiva | Aprovado | [Fluxo no navegador](navegador-producao.json); [confirmação](confirmacao-producao.png) | Somente o ID autorizado da 9/2026 foi removido; checkbox, motivo e texto EXCLUIR exigidos. |
| Cancelamento | Preservado | `test_cancelled_lower_number_occupied`, `test_delete_cancelled`, testes anteriores | Cancelar mantém o número ocupado. Canceladas só são removidas pela ação administrativa explícita. |
| Recálculo da sequência | Aprovado | `test_deletion_cases_a_b_c`, `test_successive_deletions`; [banco real](producao-depois.json) | Casos A–E cobertos; máximo restante e baseline; sem renumeração dos demais atos. |
| Baseline | 8/2026 preservado | [Configurações após reinício](producao-configuracoes.png) | Separado do último número; reconfirmar número emitido pelo aplicativo não o transforma em baseline. |
| audit_log | Aprovado | [Registro de exclusões](exclusoes-producao.png); [dados finais](producao-depois.json) | Dois registros reais, com ID original, motivo, snapshot, status, timestamp, sequência e backup. Log não ocupa número. |
| Exclusão de rascunho | Aprovado | [Fluxo real](navegador-producao.json) | Único rascunho do teste removido por confirmação simples; sequência permaneceu 8. |
| Arquivos DOCX/PDF | Aprovado | [Vínculos por conteúdo](migracao-producao.json); `audit_arquivos` no estado final | Dois arquivos legados comparados integralmente com os BLOBs, vinculados e enviados à quarentena; SHA-256 preservado. |
| Bug do titular | Corrigido | `test_legacy_special_leave_resolved_before_storage`, `test_ui_draft_delete_and_natural_legacy_reason` | Histórico mostra “do titular”/“da titular” em registros legados. Motivo padrão atualizado sem marcador interno. |
| Proteção contra placeholders | Aprovado | `tests/test_placeholders.py`; [telas reais](telas-producao-reiniciada.json) | Bloqueio antes de rascunho, prévia, finalização, DOCX e PDF; inclui marcador dividido entre elementos XML. |
| Concorrência | Aprovado | `test_deletion_finalization_concurrency` e testes anteriores | Exclusão e finalização serializadas; nenhuma duplicidade ou sequência divergente. |
| Testes | **105 aprovados em 135,23 segundos** | [JUnit final](testes-final.xml); [isolamento SQLite](isolamento-final.json) | Suíte completa, incluindo regressões antigas e novos cenários; nenhum acesso ou tentativa de acesso à produção. |
| Banco de produção | Aprovado | [Verificação final](producao-depois.json) | `integrity_check=ok`; sem órfãos; zero portarias, substituições ou exportações ativas; sete procuradores inalterados. |
| Próxima numeração | **9/2026** | [Nova Portaria após reinício](producao-novaportaria.png) | Último 8, baseline 8; nenhum novo ato real emitido. |
| Reinicialização | Aprovado | [Comparação de todas as tabelas](reinicializacao-producao.json) | Banco, sequência, configurações, cadastros e log iguais antes/depois do reinício pelo CMD. |
| DOCX/PDF e identidade visual | Preservados | [Gerações Word](documentos.json); reproduções 5, 6 e 8 nesta pasta | Todas com uma página, sem placeholders; imagens renderizadas inspecionadas. Originais de `/referencias` inalterados por SHA-256. |

## Dados removidos e identificação

- **9/2026**, Finalizada: ID `fdca049c98d3433098881e9ebfc2dc53`, declarada expressamente como teste pelo usuário. Criada em 09/09/2026 às 13:22:17 UTC, finalizada às 13:22:59 UTC. Titular Bradson, substituta Sheyla, 2ª Câmara, período 30/09/2026 a 09/10/2026.
- **Rascunho**: ID `0ca1991632df4d92ae6a06d386e6d283`, criado às 13:19:58 UTC no mesmo dia. Era o único rascunho; mesmo titular, Câmara, período e signatária, criado cerca de dois minutos antes. Nesse rascunho o substituto era Luciano e o motivo era férias. Essa diferença foi conferida, não ocultada; o conjunto de dados e a proximidade temporal correspondem ao teste manual informado.

Não foram removidos outros atos, procuradores, configurações, bases legais ou templates. A correção do catálogo alterou somente a redação padrão exata de licença especial que continha o marcador defeituoso. Os snapshots originais dos registros removidos permanecem nos backups e no log administrativo; a interface apresenta texto natural.

## Arquivos e recuperação

As exportações antigas não tinham caminho registrado no banco. Para a limpeza autorizada, os dois arquivos existentes foram comparados byte a byte com o DOCX e o PDF da única finalizada. Após backup adicional, os vínculos exatos foram registrados e a nova rotina movimentou somente esses arquivos. Não se utilizou semelhança de numeração como critério de exclusão.

O estado final contém os caminhos completos de origem, quarentena e respectivos hashes. Ambos estão em estado `QUARENTENA`; os caminhos originais não existem mais. Os arquivos permanecem disponíveis em `exports/excluidos/`, sem exclusão irreversível de seu conteúdo.

A estratégia geral cria cópia exclusiva e verificada antes do commit e só remove o original depois do commit. Falha de backup impede a exclusão. Falha transacional preserva registro, relações, sequência e arquivo original. Arquivo ausente não impede excluir o cadastro; erro de permissão deixa o original preservado e é registrado. Interrupção entre commit e remoção produz estado pendente verificável, sem limpeza automática. Veja o [guia operacional e técnico](../EXCLUSAO_ADMINISTRATIVA.md).

## Validação e correções durante a implementação

Os testes cobriram exclusão da última e de intermediárias, canceladas, exclusões sucessivas, ausência de órfãos, backup indisponível, rollback de exclusão e migração, log que não ocupa número, repetição idempotente, reutilização segura, arquivos alterados/ausentes/bloqueados, texto livre, motivos cadastrados, marcadores em campos e edições manuais, além da concorrência com finalização.

O teste no navegador identificou controles antigos permanecendo na sessão ao excluir uma finalizada e continuar com um rascunho. A ação foi ajustada para ocorrer antes da renderização da lista, e o fluxo sucessivo foi acrescentado ao teste de interface e repetido com sucesso no Edge. A revisão da migração também acrescentou o caso de reconfirmação de número já emitido pelo aplicativo, preservando o baseline anterior.

As finalizações necessárias aos testes foram exclusivamente temporárias. A instrumentação SQLite registrou os caminhos usados pela suíte e não detectou acesso à produção. As consultas finais à produção foram somente leitura. A instância isolada foi encerrada e a instância real ficou disponível em `http://127.0.0.1:8501`.
