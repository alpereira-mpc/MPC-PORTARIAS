# Guia de intervenção do Codex

## Antes de alterar

Avalie autonomamente a intervenção mínima necessária. Ajustes simples não
justificam auditoria ampla, refatoração, múltiplas ferramentas nem suíte completa.
Leia primeiro o teste e o componente diretamente afetados; só amplie a inspeção
quando houver evidência concreta de dependência ou risco.

Não faça refatorações oportunistas, mudanças estéticas ou atualização de
dependências. Preserve as funcionalidades e as regras de negócio atuais.

## Arquitetura

- `app.py`: entrada e módulo Portarias.
- `portal.py`: autenticação, autorização, temas, shell e navegação.
- `services/`: regras e interfaces dos módulos.
- `database/`: persistência comum, SQLite, PostgreSQL, schemas e inventário.
- `document_generator/`: DOCX/PDF.
- `templates/` e `assets/`: identidade institucional.
- `tests/`: contratos de domínio, persistência e Streamlit.
- `docs/`: operação e decisões técnicas; `referencias/` é imutável.

Antes de mudar código compartilhado, localize importadores, callbacks e testes
dependentes. Mantenha regras de negócio fora da interface quando possível.

## Regras de segurança

- Preserve numeração, estados, snapshots, auditoria, permissões, transações,
  idempotência e prevenção de duplicações.
- Finalização deve persistir número anual único e DOCX atomicamente. Cancelamento
  mantém o número. Exclusão definitiva só ocorre na rotina administrativa com
  confirmação, backup, auditoria e recálculo transacional permitido.
- Seeds e migrations nunca sobrescrevem dados ou configurações existentes.
- Não conecte à produção, execute migration operacional, leia Secrets reais nem
  altere autenticação ou Backup V2 sem defeito comprovado.
- Não modifique originais em `referencias/`, não sobrescreva exportações e não
  envie documentos a serviços externos.
- Não faça commit, tag ou push sem solicitação explícita.

## Streamlit e temas

Trate `st.session_state` como estado por sessão: use chaves por módulo, registro e
usuário quando necessário; limpe apenas chaves transitórias explícitas. Ao voltar
de uma tela, preserve filtros e pesquisa, mas descarte confirmações, uploads e
valores provisórios que não pertencem ao próximo registro.

Callbacks apenas atualizam estado e deixam o rerun natural ocorrer. Chamadas no
corpo do script podem solicitar rerun; dentro de fragmentos, escolha
conscientemente `scope="fragment"` ou `scope="app"`. Mudanças entre módulos devem
reentrar no app; interações internas não devem reinicializar navegação ou sessão.

Use tokens de `services/themes.py` e `services/ui_theme.py`; não introduza cores
fixas para superfícies compartilhadas. Valide componentes comuns nos cinco temas.

## Validação proporcional

Use Python 3.12, pytest e bancos/arquivos sintéticos em `tmp_path`. Execute primeiro
os testes existentes do comportamento alterado e acrescente apenas regressões
relevantes. Para navegação/estado, cubra abrir, voltar, cancelar e alternar dois
registros. Para mutações, cubra autorização no serviço, sucesso, repetição e
rollback conforme o risco.

Não execute a suíte completa por padrão. Sempre verifique a sintaxe dos arquivos
Python alterados e `git diff --check`. Mudanças em geradores exigem as Portarias
5, 6 e 8/2026; informe conversor indisponível em vez de afirmar validação PDF.
Preserve os testes PostgreSQL aprovados e nunca aponte-os para produção.

## Entrega

Relate brevemente: alterações, testes executados, riscos encontrados, riscos
residuais e decisões deliberadamente evitadas por segurança.
