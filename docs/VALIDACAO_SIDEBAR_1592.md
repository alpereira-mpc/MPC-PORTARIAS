# Sidebar — validação no Streamlit 1.59.2 (09/10/2026)

## Causa

O rádio deixou de usar BaseWeb e passou a usar React Aria. Os seletores existentes
`label[data-baseweb="radio"]` deixaram de corresponder às opções, que agora usam
`data-testid="stRadioOption"`. A estrutura do círculo também mudou. Além disso,
o Markdown do rádio deixou de receber `largerLabel`, reduzindo o tamanho padrão
do texto e dos ícones que herdam esse tamanho.

Fontes oficiais comparadas:
- https://github.com/streamlit/streamlit/blob/1.56.0/frontend/lib/src/components/shared/Radio/Radio.tsx
- https://github.com/streamlit/streamlit/blob/1.59.2/frontend/lib/src/components/shared/Radio/Radio.tsx
- https://github.com/streamlit/streamlit/blob/1.59.2/frontend/lib/src/components/shared/Radio/styled-components.tsx

## Alterações

`services/ui_theme.py`: CSS restrito à navegação e ao botão de notificações na
sidebar. Texto de 14,5 px, ícones Material de 20 px, opções de 44 px, espaçamento
de 3 px, largura útil completa, cores do tema ativo, destaque lateral de 3 px,
hover discreto e foco visível. Oculta apenas o desenho dos círculos, preservando
inputs nativos, semântica, labels e teclado. Suporta os seletores antigos e novos.
O botão de notificações tem altura mínima de 42 px e tipografia de 14,5 px.

`tests/test_portal.py`: atualização de três expectativas dos seletores existentes
para contemplar a estrutura React Aria e os estados selecionado/foco.

Nenhuma mudança em roteamento, callbacks, permissões, autenticação, dependências,
nomes/ordem dos módulos ou estilos de formulários fora da sidebar. A rolagem
mobile existente foi mantida e verificada. Nenhum commit ou push.

## Ambiente e verificações

O checkout recebido fixa 1.56.0 em `requirements.txt` e a `.venv` também usa 1.56.0.
A versão 1.59.2 foi instalada separadamente em `tmp/sidebar-runtime-1592` e usada
por `PYTHONPATH`, sem modificar a instalação original. Navegador Edge/Chromium
headless, aplicação real com identidade de teste e SQLite exclusivo em `tmp/`;
nenhum acesso a dados reais nem login no Google.

- Desktop: 1366 × 1100; temas Dourado, Azul, Verde, Rosa e Lilás. Inspeção de
  capturas e estilos computados: fontes 14,5 px, ícones 20 px, linhas 44 px,
  largura útil 239 px; círculos ocultos, item ativo e hover coerentes com o tema.
- Teclado: foco no rádio nativo, ArrowDown seleciona Busca Global, mantém o foco
  visível e executa a navegação. Clique nos demais módulos até Administração e
  retorno a Início, sem exceção apresentada pela aplicação.
- Notificações: abertura e fechamento do popover em desktop e mobile; estado
  vazio exibido corretamente. Fluxos com alertas cobertos por `tests/test_alerts.py`.
- Mobile: 390 × 844; rolagem até Administração, seleção, fechamento automático
  pelo callback existente e reabertura. Sem overflow horizontal no conteúdo da
  sidebar nem no documento. Viewport adicional de 320 × 740 nos cinco temas.
- Não houve teste em aparelho físico ou Safari/iOS.

Capturas e scripts de verificação ficam em `tmp/sidebar-validation/` e
`tmp/sidebar_*.py` (intermediários locais ignorados pelo Git).

## Testes automatizados efetivamente executados

Na 1.59.2, `tests/test_portal.py`, `tests/test_portal_navigation_reset.py`,
`tests/test_themes.py`, `tests/test_ui_theme.py` e `tests/test_alerts.py`:
**118 passaram, 3 ignorados e 5 falharam**.

As cinco falhas foram reproduzidas carregando o `services/ui_theme.py` original
obtido de `git show HEAD:services/ui_theme.py`, sem as alterações desta tarefa:

- `test_authenticated_user_can_logout_to_restricted_screen`
- `test_stripe_and_header_helpers_are_available`
- `test_oficios_recebidos_acompanhamento_controls_are_scoped`
- `test_two_received_details_can_render_in_the_same_rerun`
- `test_oficio_detail_button_uses_the_same_open_state`

Na 1.56.0, os mesmos quatro primeiros arquivos (sem `test_alerts.py`):
**87 passaram, 2 ignorados e as mesmas 5 falharam**.

Após o ajuste final de ícones/largura, os testes de formato e estilos da navegação
na 1.59.2 passaram: **6 aprovados**. `git diff --check` sem problemas.
A suíte completa do repositório não foi executada.
