from pathlib import Path

from services.branding import BRAND_RED
from services.ui_theme import badge, html_text, record_html, status_tone


def test_brand_red_matches_streamlit_theme():
    config = Path(__file__).resolve().parents[1] / ".streamlit" / "config.toml"
    assert f'primaryColor = "{BRAND_RED}"' in config.read_text(encoding="utf-8")


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
        CARD_SURFACE_A,
        CARD_SURFACE_B,
        SIDEBAR_BG,
        card_container,
        record_html,
        stripe_index,
    )

    assert CARD_SURFACE_A == "#FFFFFF"
    assert CARD_SURFACE_B == "#FCEFF1"
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
    assert "key=" in helper
    task_card = getsource(tarefas_ui._card)
    assert "card_container(" in task_card
    assert "task_" in task_card
    styles = getsource(branding._brand_styles)
    assert "object-fit:contain" in styles.replace(" ", "")
    assert "margin:-1.35rem" not in styles
    assert "mix-blend-mode:multiply" not in getsource(branding.render_sidebar_brand)
    listing = getsource(oficios_ui.listing)
    assert listing.index("for index, r in enumerate(rows):") < listing.index("_details_if_open")
    assert "card_container(" in listing
    render = getsource(oficios_ui.render)
    assert "open_oficio_mov_" in render
    assert "Abrir ofício" in render
