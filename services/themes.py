"""Institutional color palettes; functional status colors stay in ui_theme."""

THEMES = {
    "vermelho": {
        "primary": "#9B1724",
        "primary_hover": "#7E121C",
        "primary_soft": "#F3E6E8",
        "sidebar_bg": "#D48792",
        "card_operational_bg": "#E6C7CC",
        "card_operational_border": "#D8DADD",
        "card_institutional_bg": "#FBF4F6",
        "card_institutional_border": "#E7CDD3",
        "card_b_bg": "#FCEFF1",
        "card_b_border": "#E9C7CC",
    },
    "azul": {
        "primary": "#24577D",
        "primary_hover": "#183E5C",
        "primary_soft": "#E5EFF5",
        "sidebar_bg": "#9ABFD6",
        "card_operational_bg": "#C6DDEA",
        "card_operational_border": "#D8DADD",
        "card_institutional_bg": "#F4F8FB",
        "card_institutional_border": "#CCDFEC",
        "card_b_bg": "#EEF6FA",
        "card_b_border": "#C4DBE9",
    },
    "verde": {
        "primary": "#246349",
        "primary_hover": "#194632",
        "primary_soft": "#E5F2EA",
        "sidebar_bg": "#9EC8AF",
        "card_operational_bg": "#C9E2D1",
        "card_operational_border": "#D8DADD",
        "card_institutional_bg": "#F4FAF6",
        "card_institutional_border": "#CDE4D3",
        "card_b_bg": "#EDF7F0",
        "card_b_border": "#C7E1CF",
    },
    "dourado": {
        "primary": "#765414",
        "primary_hover": "#553C0D",
        "primary_soft": "#F5EEDC",
        "sidebar_bg": "#D5BF8C",
        "card_operational_bg": "#E7D8B3",
        "card_operational_border": "#D8DADD",
        "card_institutional_bg": "#FBF9F2",
        "card_institutional_border": "#E8D9B6",
        "card_b_bg": "#F9F4E5",
        "card_b_border": "#E7D6A8",
    },
    "vermelho_escuro": {
        "primary": "#68131D",
        "primary_hover": "#4D0D15",
        "primary_soft": "#F0E4E6",
        "sidebar_bg": "#B88991",
        "card_operational_bg": "#DCC0C5",
        "card_operational_border": "#D8DADD",
        "card_institutional_bg": "#FAF4F5",
        "card_institutional_border": "#DFC7CC",
        "card_b_bg": "#F8ECEE",
        "card_b_border": "#DDBFC5",
    },
}

THEME_LABELS = {
    "vermelho": "Vermelho",
    "azul": "Azul",
    "verde": "Verde",
    "dourado": "Dourado",
    "vermelho_escuro": "Vermelho escuro",
}


def valid_theme(value):
    return value if value in THEMES else "vermelho"


def theme_tokens(value):
    return THEMES[valid_theme(value)]
