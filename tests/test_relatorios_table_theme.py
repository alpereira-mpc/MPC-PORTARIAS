from services.relatorios_ui import style_report_table
from services.themes import THEMES


def test_report_table_style_uses_theme_tokens_and_preserves_values():
    rows = [
        {"Procurador": "Primeiro", "Entradas": 3},
        {"Procurador": "Segundo", "Entradas": 7},
    ]

    styler = style_report_table(rows, "azul")
    html = styler.to_html()
    palette = THEMES["azul"]

    assert styler.data.to_dict("records") == rows
    assert "font-weight: 700" in html
    assert palette["themed_table_header_bg"] in html
    assert palette["themed_table_header_fg"] in html
    assert palette["themed_table_bg"] in html
    assert palette["themed_table_stripe_bg"] in html
    assert palette["themed_table_border"] in html
    assert "#fff" not in html.lower()
    assert "white" not in html.lower()


def test_every_theme_defines_report_table_palette():
    required = {
        "themed_table_bg",
        "themed_table_header_bg",
        "themed_table_border",
        "themed_table_fg",
        "themed_table_header_fg",
        "themed_table_stripe_bg",
    }

    for palette in THEMES.values():
        assert required <= palette.keys()
        assert palette["themed_table_bg"] != palette["themed_table_stripe_bg"]
