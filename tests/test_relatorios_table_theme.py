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


def test_report_table_formats_counts_and_days_without_changing_numeric_values():
    rows = [
        {
            "Tempo médio com procurador": 6.3,
            "Maior permanência atual": 16.0,
            "Quantidade": 184.0,
            "Dias com Procurador": 184.0,
            "Dias no MPC-PB": 1.0,
            "Produção/Distribuições": 80.5,
            "Mediana de permanência": 7.9,
        }
    ]

    styler = style_report_table(rows)
    html = styler.to_html()

    assert styler.data.to_dict("records") == rows
    assert ">6,3<" in html
    assert ">16<" in html
    assert html.count(">184<") == 2
    assert ">1<" in html
    assert ">80,5%<" in html
    assert ">7,9<" in html
    assert "184.000000" not in html
    assert ">16.0<" not in html
