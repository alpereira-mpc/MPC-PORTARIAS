from pathlib import Path

from services.branding import BRAND_RED
from services.ui_theme import badge, html_text, record_html, status_tone


def test_brand_red_matches_streamlit_theme():
    from services.ui_theme import CONTROL_BG

    config = Path(__file__).resolve().parents[1] / ".streamlit" / "config.toml"
    text = config.read_text(encoding="utf-8")
    assert f'primaryColor = "{BRAND_RED}"' in text
    assert f'secondaryBackgroundColor = "{CONTROL_BG}"' in text
    assert 'backgroundColor = "#F6F5F4"' in text


def test_badge_and_record_escape_content():
    mark = badge("<urgente>", "danger")
    assert "<urgente>" not in mark
    assert "&lt;urgente&gt;" in mark
    assert "mpc-badge--danger" in mark
    block = record_html('Título <x>', secondary="a & b", meta="ok", accent="brand", surface="warning")
    assert "<x>" not in block
    assert "mpc-record--brand" in block
    assert "mpc-surface-warning" in block
    assert html_text(None) == ""


def test_status_tone_covers_semantic_labels():
    assert status_tone("URGENTE") == "danger"
    assert status_tone("VENCIDA") == "danger"
    assert status_tone("Concluída") == "success"
    assert status_tone("Realizado") == "success"
    assert status_tone("Cancelado") == "muted"
    assert status_tone("Inativo") == "muted"
    assert status_tone("ATIVO") == "success"
    assert status_tone("Aguardando") == "warning"
    assert status_tone("Em andamento") == "brand"
    assert status_tone("INFORMATIVO") == "info"
    assert status_tone("ALTA") == "brand"
    assert status_tone("ALTO") == "warning"


def test_stripe_and_header_helpers_are_available():
    from inspect import getsource

    from services import branding, oficios_ui, tarefas_ui
    from services.ui_theme import (
        CARD_INSTITUTIONAL_BG,
        CARD_INSTITUTIONAL_BORDER,
        CARD_OPERATIONAL_BG,
        CARD_OPERATIONAL_BORDER,
        CARD_SURFACE_A,
        CARD_SURFACE_B,
        BRAND_RED_DARK,
        CONTROL_BG,
        CONTROL_BORDER,
        CONTROL_DISABLED,
        CONTROL_HOVER,
        EMPTY_STATE_BG,
        EMPTY_STATE_BORDER,
        EXPANDER_BG,
        EXPANDER_BORDER,
        EXPANDER_HOVER,
        SIDEBAR_BG,
        card_container,
        record_html,
        stripe_index,
    )

    assert CARD_OPERATIONAL_BG == "#E6C7CC"
    assert CARD_OPERATIONAL_BORDER == "#D8DADD"
    assert CARD_INSTITUTIONAL_BG == "#FBF4F6"
    assert CARD_INSTITUTIONAL_BORDER == "#E7CDD3"
    assert CARD_SURFACE_A == CARD_OPERATIONAL_BG
    assert CARD_SURFACE_B == CARD_OPERATIONAL_BG
    assert CARD_OPERATIONAL_BG != CARD_INSTITUTIONAL_BG
    assert CARD_OPERATIONAL_BG != "#FFFFFF"
    assert CARD_INSTITUTIONAL_BG != "#FFFFFF"
    assert SIDEBAR_BG == "#F3F4F8"
    assert stripe_index(0) == "a"
    assert stripe_index(1) == "b"
    block = record_html("Item", stripe="b", accent="brand")
    assert "mpc-stripe-b" in block
    assert "mpc-card-odd" in block
    even = record_html("Item", stripe="a")
    assert "mpc-card-even" in even
    helper = getsource(card_container)
    assert "st.container" in helper
    assert "mpc_card_" in helper
    assert "operational" in helper
    assert "stripe_index" not in helper
    assert "key=" in helper
    task_card = getsource(tarefas_ui._card)
    assert "card_container(" in task_card
    assert "task_" in task_card
    styles = getsource(branding._brand_styles)
    assert "object-fit:contain" in styles.replace(" ", "")
    assert "margin:-1.35rem" not in styles
    assert "mix-blend-mode:multiply" not in getsource(branding.render_sidebar_brand)
    assert "background-image:url(" in getsource(branding.render_sidebar_brand).replace(" ", "")
    assert "st-key-sidebar_home'] button:hover" in getsource(branding.render_sidebar_brand)
    listing = getsource(oficios_ui.listing)
    assert listing.index("for index, r in enumerate(rows):") < listing.index("_details_if_open")
    assert "card_container(" in listing
    render = getsource(oficios_ui.render)
    assert "open_oficio_mov_" in render
    assert "Abrir ofício" in render
    from services.ui_theme import _css

    css = _css()
    compact = css.replace(" ", "")
    assert "st-key-mpc_card_" in css
    assert BRAND_RED in css
    assert '[class*="st-key-mpc_card_"]button' in compact
    assert EXPANDER_BG == "#F5F2F1"
    assert EXPANDER_BORDER == "#D9D1CE"
    assert EXPANDER_HOVER == "#EEE8E6"
    assert CONTROL_DISABLED == "#EEECEB"
    assert CONTROL_BG == EXPANDER_BG
    assert CONTROL_BORDER == EXPANDER_BORDER
    assert CONTROL_HOVER == EXPANDER_HOVER
    assert "--mpc-control-bg:" + CONTROL_BG in compact
    assert "--mpc-control-border:" + CONTROL_BORDER in compact
    assert "--mpc-control-hover:" + CONTROL_HOVER in compact
    assert "--mpc-control-disabled:" + CONTROL_DISABLED in compact
    assert "--mpc-red:" + BRAND_RED in compact
    assert "--mpc-red-hover:" + BRAND_RED_DARK in compact
    assert "--mpc-expander:var(--mpc-control-bg)" in compact
    assert "#F1F4F7" not in compact
    assert "#D8DEE5" not in compact
    assert "#E9EEF3" not in compact
    assert '[data-testid="stDownloadButton"]button' in compact
    assert '[data-testid="stFormSubmitButton"]button' in compact
    assert '[data-testid="stButton"]button' in compact
    assert "background-color:var(--mpc-red)!important" in compact
    assert '[data-testid="stButton"]button{background:var(--mpc-red)' not in compact
    logo_btn = compact[compact.find('[class*="st-key-sidebar_home"]button') :]
    logo_btn = logo_btn[: logo_btn.find("[data-testid=\"stNumberInput\"]button")]
    assert "background-color:transparent" in logo_btn
    assert "background:transparent" not in logo_btn.replace("background-color:transparent", "")
    assert 'button[kind="secondary"]{background:var(--mpc-white)' not in compact
    assert 'section[data-testid="stMain"][data-testid="stExpander"]details' in compact
    assert "background-color.15sease" in compact
    assert ':has(>[data-testid="stElementContainer"].mpc-filter-mark)' in compact
    assert '[data-testid="stExpander"][data-testid="stVerticalBlock"]:has(.mpc-filter-mark)' in compact
    assert '[data-testid="stTextInputRootElement"]' in compact
    assert "inset0001pxvar(--mpc-control-border)" in compact
    assert "var(--mpc-control-bg)!important" in compact
    assert "stTextInput\"]>div>div{background:var(--mpc-white)" not in compact
    assert EXPANDER_BG in css
    assert EXPANDER_BORDER in css
    assert EXPANDER_HOVER in css
    assert "--mpc-empty-state-bg:" + EMPTY_STATE_BG in compact
    assert "--mpc-empty-state-border:" + EMPTY_STATE_BORDER in compact
    assert "--mpc-surface-control-bg:var(--mpc-control-bg)" in compact
    assert EMPTY_STATE_BG == "#F7F1F2"
    assert EMPTY_STATE_BORDER == "#E3D4D7"
    assert "stAlertContentInfo" in css
    assert "[data-testid=\"stMetric\"]{\nbackground:var(--mpc-card-institutional-bg)" in css
    assert "[data-testid=\"stVerticalBlockBorderWrapper\"]{\nbackground:var(--mpc-control-bg)" in css
    assert "mpc-form-mark){\nbackground:var(--mpc-control-bg)" in css
    assert '[data-testid="stExpanderDetails"]{\nbackground:var(--mpc-control-bg)' in css
    assert CARD_OPERATIONAL_BG in css
    assert CARD_INSTITUTIONAL_BG in css
    assert "#FCEFF1" not in compact
    assert "--mpc-card-operational-bg:" + CARD_OPERATIONAL_BG in compact
    assert "--mpc-card-institutional-bg:" + CARD_INSTITUTIONAL_BG in compact
    assert "background:var(--mpc-card-institutional-bg)!important" in compact
    assert "st-key-mpc_card_operational" in css
    assert 'st-key-mpc_card_a"][data-testid="stExpander"]' not in compact
    assert 'st-key-mpc_card_b"][data-testid="stExpander"]' not in compact
    assert 'st-key-mpc_card_operational"][data-testid="stExpander"]' not in compact
    assert 'section[data-testid="stSidebar"][data-testid="stExpander"]' in compact
    assert 'st.button("← Trocar gabinete", type="primary"' in render
    assert 'type="primary"' in render[render.index("Novo Ofício") :]
    from portal import render_denied, render_portal

    assert 'button("Sair", type="primary"' in getsource(render_denied)
    assert 'button("Sair", type="primary", key="portal_logout"' in getsource(render_portal)
    assert 'button[kind="primary"]' in compact
