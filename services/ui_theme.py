"""Central visual language for the Ferramentas MPC-PB portal.

Tokens, one stylesheet, and small HTML helpers. No business rules, queries,
or Streamlit widget keys live here. CSS is injected once per script run.
"""

from contextlib import contextmanager
from html import escape

import streamlit as st

from services.themes import theme_tokens, valid_theme

# --- Tokens (aligned with .streamlit/config.toml) ---
_RED = theme_tokens("vermelho")
BRAND_RED_DARK = _RED["primary_hover"]
BRAND_RED_SOFT = _RED["primary_soft"]
CARD_OPERATIONAL_BG = _RED["card_operational_bg"]
CARD_OPERATIONAL_BORDER = _RED["card_operational_border"]
CARD_INSTITUTIONAL_BG = _RED["card_institutional_bg"]
CARD_INSTITUTIONAL_BORDER = _RED["card_institutional_border"]
SURFACE_PRIMARY_BG = CARD_INSTITUTIONAL_BG
SURFACE_PRIMARY_BORDER = CARD_INSTITUTIONAL_BORDER
CARD_SURFACE_A = CARD_OPERATIONAL_BG
CARD_SURFACE_B = _RED["card_b_bg"]
CARD_BORDER_A = CARD_OPERATIONAL_BORDER
CARD_BORDER_B = _RED["card_b_border"]
SIDEBAR_BG = _RED["sidebar_bg"]
EXPANDER_BG = "#F5F2F1"
EXPANDER_BORDER = "#D9D1CE"
EXPANDER_HOVER = "#EEE8E6"
CONTROL_BG = EXPANDER_BG
CONTROL_BORDER = EXPANDER_BORDER
CONTROL_HOVER = EXPANDER_HOVER
CONTROL_DISABLED = "#EEECEB"
EMPTY_STATE_BG = "#FAF6F7"
EMPTY_STATE_BORDER = "#E7D7DB"
SURFACE_WHITE = "#FFFFFF"
SURFACE_PAGE = "#F6F5F4"
SURFACE_SOFT = "#FFFFFF"
SURFACE_MUTED = "#E8E4E5"
BORDER_LIGHT = "#D8D2D3"
BORDER_MEDIUM = "#C4BDBE"
TEXT_PRIMARY = "#202832"
TEXT_SECONDARY = "#5C6570"
TEXT_MUTED = "#7A838E"
SUCCESS = "#2E7D4F"
SUCCESS_SOFT = "#E8F5EE"
WARNING = "#B58112"
WARNING_SOFT = "#F8F1DE"
DANGER = "#B02A2A"
DANGER_SOFT = "#F8EAEA"
INFO = "#3D5A80"
INFO_SOFT = "#EEF2F7"
NEUTRAL = "#5C6570"
NEUTRAL_SOFT = "#F0F1F3"

TONES = frozenset(
    {"brand", "success", "warning", "danger", "muted", "neutral", "info"}
)

_STATUS_SUCCESS = (
    "conclu",
    "realizad",
    "ativo",
    "respondido",
    "encerrad",
    "finalizad",
    "ok",
)
_STATUS_MUTED = ("cancel", "inativ", "arquiv", "baixa")
_STATUS_DANGER = (
    "urgent",
    "vencid",
    "crític",
    "critic",
    "atrasad",
    "erro",
)
_STATUS_WARNING = (
    "aguard",
    "aten",
    "hoje",
    "próxim",
    "proxim",
    "pendente",
    "provid",
)
_STATUS_BRAND = (
    "andamento",
    "rascunho",
    "enviado",
    "recebido",
    "agendado",
    "confirmado",
    "normal",
)

_PRIORITY_TONES = {
    "BAIXA": "muted",
    "NORMAL": "neutral",
    "ALTA": "brand",
    "URGENTE": "danger",
    "CRÍTICO": "danger",
    "ALTO": "warning",
    "ATENÇÃO": "warning",
    "INFORMATIVO": "info",
    "VENCIDA": "danger",
    "HOJE": "warning",
    "PRÓXIMA": "warning",
    "FUTURA": "muted",
    "SEM PRAZO": "muted",
}

def _css(theme_name="vermelho"):
    theme_name = valid_theme(theme_name)
    palette = theme_tokens(theme_name)
    BRAND_RED = palette["primary"]
    BRAND_RED_DARK = palette["primary_hover"]
    BRAND_RED_SOFT = palette["primary_soft"]
    CARD_OPERATIONAL_BG = palette["card_operational_bg"]
    CARD_OPERATIONAL_BORDER = palette["card_operational_border"]
    CARD_INSTITUTIONAL_BG = palette["card_institutional_bg"]
    CARD_INSTITUTIONAL_BORDER = palette["card_institutional_border"]
    SURFACE_PRIMARY_BG = CARD_INSTITUTIONAL_BG
    SURFACE_PRIMARY_BORDER = CARD_INSTITUTIONAL_BORDER
    CARD_SURFACE_A = CARD_OPERATIONAL_BG
    CARD_SURFACE_B = palette["card_b_bg"]
    CARD_BORDER_A = CARD_OPERATIONAL_BORDER
    CARD_BORDER_B = palette["card_b_border"]
    SIDEBAR_BG = palette["sidebar_bg"]
    primary_rgb = ",".join(
        str(int(BRAND_RED[index : index + 2], 16)) for index in (1, 3, 5)
    )
    css = f"""
:root{{
--mpc-brand:{BRAND_RED};
--mpc-red:{BRAND_RED};
--mpc-red-hover:{BRAND_RED_DARK};
--mpc-brand-dark:{BRAND_RED_DARK};
--mpc-brand-soft:{BRAND_RED_SOFT};
--mpc-card-a:{CARD_SURFACE_A};
--mpc-card-b:{CARD_SURFACE_B};
--mpc-card-border-a:{CARD_BORDER_A};
--mpc-card-border-b:{CARD_BORDER_B};
--mpc-card-institutional-bg:{CARD_INSTITUTIONAL_BG};
--mpc-card-institutional-border:{CARD_INSTITUTIONAL_BORDER};
--mpc-surface-primary-bg:var(--mpc-card-institutional-bg);
--mpc-surface-primary-border:var(--mpc-card-institutional-border);
--mpc-card-operational-bg:{CARD_OPERATIONAL_BG};
--mpc-card-operational-border:{CARD_OPERATIONAL_BORDER};
--mpc-sidebar:{SIDEBAR_BG};
--mpc-control-bg:{CONTROL_BG};
--mpc-control-border:{CONTROL_BORDER};
--mpc-control-hover:{CONTROL_HOVER};
--mpc-control-disabled:{CONTROL_DISABLED};
--mpc-themed-control-bg:{palette["themed_control_bg"]};
--mpc-themed-control-border:{palette["themed_control_border"]};
--mpc-themed-control-hover:{palette["themed_control_hover"]};
--mpc-themed-control-fg:{palette["themed_control_fg"]};
--mpc-themed-control-placeholder:{palette["themed_control_placeholder"]};
--mpc-themed-table-bg:{palette["themed_table_bg"]};
--mpc-themed-table-header-bg:{palette["themed_table_header_bg"]};
--mpc-themed-table-border:{palette["themed_table_border"]};
--mpc-themed-table-fg:{palette["themed_table_fg"]};
--mpc-surface-control-bg:var(--mpc-control-bg);
--mpc-surface-control-border:var(--mpc-control-border);
--mpc-surface-control-hover:var(--mpc-control-hover);
--mpc-empty-state-bg:{EMPTY_STATE_BG};
--mpc-empty-state-border:{EMPTY_STATE_BORDER};
--mpc-expander:var(--mpc-control-bg);
--mpc-expander-border:var(--mpc-control-border);
--mpc-expander-hover:var(--mpc-control-hover);
--mpc-page:{SURFACE_PAGE};
--mpc-white:{SURFACE_WHITE};
--mpc-soft:{SURFACE_SOFT};
--mpc-muted-bg:{SURFACE_MUTED};
--mpc-border:{BORDER_LIGHT};
--mpc-border-md:{BORDER_MEDIUM};
--mpc-text:{TEXT_PRIMARY};
--mpc-text-primary:{TEXT_PRIMARY};
--mpc-text-2:{TEXT_SECONDARY};
--mpc-text-secondary:{TEXT_SECONDARY};
--mpc-text-3:{TEXT_MUTED};
--mpc-success:{SUCCESS};
--mpc-success-soft:{SUCCESS_SOFT};
--mpc-warning:{WARNING};
--mpc-warning-soft:{WARNING_SOFT};
--mpc-danger:{DANGER};
--mpc-danger-soft:{DANGER_SOFT};
--mpc-info:{INFO};
--mpc-info-soft:{INFO_SOFT};
--mpc-radius:10px;
--mpc-shadow:0 1px 3px rgba(32,40,50,.07);
}}
.stApp,[data-testid="stAppViewContainer"]{{
background:var(--mpc-page);
}}
[data-testid="stHeader"]{{background:rgba(246,245,244,.92);}}
section[data-testid="stMain"] [data-testid="stMainBlockContainer"]{{
padding-top:1.35rem;
background:var(--mpc-page);
}}
html body .stApp [data-testid="stSidebar"],
html body .stApp [data-testid="stSidebar"] > div,
html body .stApp [data-testid="stSidebar"] [data-testid="stSidebarContent"],
html body .stApp [data-testid="stSidebar"] [data-testid="stSidebarHeader"],
html body .stApp [data-testid="stSidebar"] [data-testid="stSidebarUserContent"],
html body .stApp [data-testid="stSidebar"] [data-testid="stVerticalBlock"],
html body .stApp [data-testid="stSidebar"] [data-testid="stElementContainer"],
html body .stApp [data-testid="stSidebarCollapsedControl"]{{
background:{SIDEBAR_BG} !important;
background-color:{SIDEBAR_BG} !important;
}}
section[data-testid="stSidebar"]{{
border-right:1px solid var(--mpc-border);
background:{SIDEBAR_BG} !important;
background-color:{SIDEBAR_BG} !important;
}}
section[data-testid="stSidebar"] [data-testid="stSidebarContent"]:has(.st-key-portal_theme_footer){{
display:flex;
flex-direction:column;
}}
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"]:has(.st-key-portal_theme_footer){{
display:flex;
flex:1 0 auto;
flex-direction:column;
padding-bottom:calc(.75rem + env(safe-area-inset-bottom));
}}
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"]:has(.st-key-portal_theme_footer) > div{{
display:flex;
flex:1 0 auto;
flex-direction:column;
}}
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"]:has(.st-key-portal_theme_footer) > div > [data-testid="stVerticalBlock"]{{
flex:1 0 auto;
}}
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has(> [data-testid="stVerticalBlock"].st-key-portal_theme_footer){{
margin-top:auto;
flex-shrink:0;
}}
section[data-testid="stSidebar"] [class*="st-key-sidebar_home"],
section[data-testid="stSidebar"] [class*="st-key-sidebar_home"] [data-testid="stButton"]{{
background-color:{SIDEBAR_BG} !important;
box-shadow:none !important;
border:0 !important;
}}
section[data-testid="stSidebar"] [data-testid="stCaption"]{{
color:var(--mpc-text-2);
}}
section[data-testid="stSidebar"] [data-testid="stRadio"] label{{
border-radius:8px;
transition:background .12s ease;
}}
section[data-testid="stSidebar"] [data-testid="stRadio"] label:hover{{
background:rgba({primary_rgb},.05);
}}
section[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked){{
background:rgba({primary_rgb},.16);
box-shadow:inset 3px 0 0 var(--mpc-brand);
font-weight:700;
color:var(--mpc-brand-dark);
}}
section[data-testid="stSidebar"] [data-testid="stExpander"]{{
background:var(--mpc-control-bg) !important;
background-color:var(--mpc-control-bg) !important;
border:1px solid var(--mpc-control-border);
border-radius:var(--mpc-radius);
}}
section[data-testid="stSidebar"] [data-testid="stPopover"]{{
position:relative;
overflow:visible !important;
}}
section[data-testid="stSidebar"] [data-testid="stPopover"] [data-testid="stPopoverBody"],
section[data-testid="stSidebar"] [data-testid="stPopover"] [data-baseweb="popover"],
[data-testid="stPopoverBody"]{{
max-height:min(28rem, calc(100vh - 6.5rem)) !important;
overflow-x:hidden !important;
overflow-y:auto !important;
overscroll-behavior:contain;
}}
section[data-testid="stSidebar"] [data-testid="stPopover"] [data-testid="stPopoverBody"],
section[data-testid="stSidebar"] [data-testid="stPopover"] [data-baseweb="popover"]{{
position:absolute !important;
top:calc(100% + .4rem) !important;
bottom:auto !important;
left:0 !important;
right:0 !important;
transform:none !important;
width:100% !important;
z-index:10002 !important;
}}
[data-testid="stHeading"] h1,
[data-testid="stHeading"] h2,
[data-testid="stHeading"] h3{{
font-weight:700;
letter-spacing:.01em;
border-left:3px solid var(--mpc-brand);
padding-left:.7rem;
line-height:1.25;
}}
[data-testid="stCaption"]{{color:var(--mpc-text-2);}}
[data-testid="stTextInputRootElement"],
[data-testid="stTextAreaRootElement"],
[data-testid="stNumberInputContainer"],
[data-testid="stDateInputField"],
[data-testid="stTimeInputTimeDisplay"],
[data-testid="stTimeInput"] [data-baseweb="input"],
[data-testid="stTextInput"] > div > div,
[data-testid="stTextArea"] > div > div,
[data-testid="stSelectbox"] > div > div,
[data-testid="stMultiSelect"] > div > div,
[data-testid="stNumberInput"] > div > div,
[data-testid="stDateInput"] > div > div,
[data-testid="stTimeInput"] > div > div,
[data-testid="stTextInput"] [data-baseweb="input"],
[data-testid="stTextInput"] [data-baseweb="base-input"],
[data-testid="stNumberInput"] [data-baseweb="input"],
[data-testid="stNumberInput"] [data-baseweb="base-input"],
[data-testid="stDateInput"] [data-baseweb="input"],
[data-testid="stDateInput"] [data-baseweb="base-input"],
[data-testid="stTimeInput"] [data-baseweb="input"],
[data-testid="stTimeInput"] [data-baseweb="base-input"],
[data-testid="stTextArea"] [data-baseweb="textarea"],
[data-testid="stTextArea"] textarea,
[data-testid="stTimeInput"] [data-baseweb="select"] > div,
[data-testid="stSelectbox"] [data-baseweb="select"] > div,
[data-testid="stMultiSelect"] [data-baseweb="select"] > div{{
background:var(--mpc-control-bg) !important;
background-color:var(--mpc-control-bg) !important;
border-color:var(--mpc-control-border) !important;
box-shadow:inset 0 0 0 1px var(--mpc-control-border);
color:var(--mpc-text) !important;
transition:background-color .15s ease,border-color .15s ease,box-shadow .15s ease;
}}
[data-testid="stTextInputRootElement"]:hover,
[data-testid="stTextAreaRootElement"]:hover,
[data-testid="stNumberInputContainer"]:hover,
[data-testid="stDateInputField"]:hover,
[data-testid="stTimeInputTimeDisplay"]:hover,
[data-testid="stTextInput"] > div > div:hover,
[data-testid="stTextArea"] > div > div:hover,
[data-testid="stSelectbox"] > div > div:hover,
[data-testid="stMultiSelect"] > div > div:hover,
[data-testid="stNumberInput"] > div > div:hover,
[data-testid="stDateInput"] > div > div:hover,
[data-testid="stTimeInput"] > div > div:hover,
[data-testid="stSelectbox"] [data-baseweb="select"] > div:hover,
[data-testid="stMultiSelect"] [data-baseweb="select"] > div:hover{{
background:var(--mpc-control-hover) !important;
background-color:var(--mpc-control-hover) !important;
}}
[data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stTextAreaRootElement"]:focus-within,
[data-testid="stNumberInputContainer"]:focus-within,
[data-testid="stDateInputField"]:focus-within,
[data-testid="stTimeInput"]:focus-within,
[data-testid="stTextArea"] > div > div:focus-within,
[data-testid="stSelectbox"] > div > div:focus-within,
[data-testid="stMultiSelect"] > div > div:focus-within,
[data-testid="stNumberInput"] > div > div:focus-within,
[data-testid="stDateInput"] > div > div:focus-within,
[data-testid="stTimeInput"] > div > div:focus-within,
[data-testid="stSelectbox"] [data-baseweb="select"] > div:focus-within,
[data-testid="stMultiSelect"] [data-baseweb="select"] > div:focus-within,
[data-testid="stTextInput"] [data-baseweb="input"]:focus-within,
[data-testid="stNumberInput"] [data-baseweb="input"]:focus-within,
[data-testid="stDateInput"] [data-baseweb="input"]:focus-within,
[data-testid="stTimeInput"] [data-baseweb="input"]:focus-within,
[data-testid="stTextArea"] [data-baseweb="textarea"]:focus-within{{
background:var(--mpc-control-bg) !important;
border-color:var(--mpc-red) !important;
box-shadow:0 0 0 1px var(--mpc-red) !important;
outline:none !important;
}}
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stDateInput"] input,
[data-testid="stTimeInput"] input,
[data-testid="stTextArea"] textarea{{
background:transparent !important;
color:var(--mpc-text) !important;
caret-color:var(--mpc-text);
}}
[data-testid="stTextInput"] input::placeholder,
[data-testid="stNumberInput"] input::placeholder,
[data-testid="stDateInput"] input::placeholder,
[data-testid="stTimeInput"] input::placeholder,
[data-testid="stTextArea"] textarea::placeholder,
[data-testid="stMultiSelect"] input::placeholder{{
color:var(--mpc-text-3) !important;
opacity:1;
}}
[data-testid="stTextInput"] input:disabled,
[data-testid="stNumberInput"] input:disabled,
[data-testid="stDateInput"] input:disabled,
[data-testid="stTimeInput"] input:disabled,
[data-testid="stTextArea"] textarea:disabled,
[data-testid="stSelectbox"] [data-baseweb="select"][aria-disabled="true"] > div,
[data-testid="stMultiSelect"] [data-baseweb="select"][aria-disabled="true"] > div,
[data-testid="stTextInputRootElement"]:has(input:disabled),
[data-testid="stTextAreaRootElement"]:has(textarea:disabled),
[data-testid="stNumberInputContainer"]:has(input:disabled){{
background:var(--mpc-control-disabled) !important;
background-color:var(--mpc-control-disabled) !important;
opacity:.72;
color:var(--mpc-text-2) !important;
cursor:not-allowed;
}}
[data-testid="stTextInput"] svg,
[data-testid="stSelectbox"] svg,
[data-testid="stMultiSelect"] svg,
[data-testid="stDateInput"] svg,
[data-testid="stTimeInput"] svg,
[data-testid="stNumberInput"] svg,
[data-testid="stNumberInput"] button{{
color:var(--mpc-text-2);
}}
[data-testid="stVerticalBlockBorderWrapper"]{{
background:var(--mpc-control-bg);
border:1px solid var(--mpc-control-border) !important;
border-radius:var(--mpc-radius);
box-shadow:var(--mpc-shadow);
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-record--brand){{
border-left:3px solid var(--mpc-brand) !important;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-record--success){{
border-left:3px solid var(--mpc-success) !important;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-record--warning){{
border-left:3px solid var(--mpc-warning) !important;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-record--danger){{
border-left:3px solid var(--mpc-danger) !important;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-record--muted){{
border-left:3px solid var(--mpc-border-md) !important;
background:var(--mpc-soft);
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-record--neutral){{
border-left:3px solid var(--mpc-border-md) !important;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-record--info){{
border-left:3px solid var(--mpc-info) !important;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-home-card-mark),
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-institutional-card-mark),
section[data-testid="stMain"] [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .mpc-home-card-mark),
section[data-testid="stMain"] [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .mpc-institutional-card-mark){{
background:var(--mpc-card-institutional-bg) !important;
background-color:var(--mpc-card-institutional-bg) !important;
border:1px solid var(--mpc-card-institutional-border) !important;
border-radius:var(--mpc-radius);
transition:border-color .15s ease,box-shadow .15s ease;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-home-card-mark):hover,
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-institutional-card-mark):hover,
section[data-testid="stMain"] [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .mpc-home-card-mark):hover,
section[data-testid="stMain"] [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .mpc-institutional-card-mark):hover{{
border-color:rgba({primary_rgb},.22) !important;
box-shadow:0 1px 4px rgba(32,40,50,.06);
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-operational-card-mark),
section[data-testid="stMain"] [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .mpc-operational-card-mark){{
background:var(--mpc-card-operational-bg) !important;
background-color:var(--mpc-card-operational-bg) !important;
border:1px solid var(--mpc-card-operational-border) !important;
border-radius:var(--mpc-radius);
transition:border-color .15s ease,box-shadow .15s ease;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-operational-card-mark):hover,
section[data-testid="stMain"] [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .mpc-operational-card-mark):hover{{
border-color:rgba({primary_rgb},.22) !important;
box-shadow:0 1px 4px rgba(32,40,50,.06);
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-surface-brand){{
background:var(--mpc-brand-soft) !important;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-surface-success){{
background:var(--mpc-success-soft) !important;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-surface-warning){{
background:var(--mpc-warning-soft) !important;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-surface-danger){{
background:var(--mpc-danger-soft) !important;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-surface-muted){{
background:var(--mpc-muted-bg) !important;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-surface-neutral){{
background:var(--mpc-control-bg) !important;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-filter-mark),
section[data-testid="stMain"] [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .mpc-filter-mark){{
background:var(--mpc-control-bg) !important;
background-color:var(--mpc-control-bg) !important;
border:1px solid var(--mpc-control-border) !important;
border-radius:var(--mpc-radius);
box-shadow:none;
}}
section[data-testid="stMain"] [data-testid="stExpander"] [data-testid="stVerticalBlock"]:has(.mpc-filter-mark){{
background:transparent !important;
background-color:transparent !important;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-detail-mark),
section[data-testid="stMain"] [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .mpc-detail-mark){{
background:var(--mpc-card-operational-bg) !important;
background-color:var(--mpc-card-operational-bg) !important;
border:1px solid var(--mpc-card-operational-border) !important;
border-left:3px solid var(--mpc-brand) !important;
}}
[data-testid="stVerticalBlock"]:has(> div .mpc-kpi-mark--danger) [data-testid="stMetric"]{{
background:var(--mpc-surface-primary-bg);
border-left-color:var(--mpc-danger);
}}
[data-testid="stVerticalBlock"]:has(> div .mpc-kpi-mark--warning) [data-testid="stMetric"]{{
background:var(--mpc-surface-primary-bg);
border-left-color:var(--mpc-warning);
}}
[data-testid="stVerticalBlock"]:has(> div .mpc-kpi-mark--brand) [data-testid="stMetric"]{{
background:var(--mpc-surface-primary-bg);
border-left-color:var(--mpc-brand);
}}
[data-testid="stVerticalBlock"]:has(> div .mpc-kpi-mark--info) [data-testid="stMetric"]{{
background:var(--mpc-surface-primary-bg);
border-left-color:var(--mpc-info);
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-actions) [data-testid="stHorizontalBlock"]{{
justify-content:flex-start;
gap:.45rem;
flex-wrap:wrap;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-actions) [data-testid="stHorizontalBlock"]>div{{
flex:0 1 auto !important;
width:auto !important;
min-width:0;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-form-mark),
section[data-testid="stMain"] [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .mpc-form-mark){{
background:var(--mpc-control-bg) !important;
background-color:var(--mpc-control-bg) !important;
border:1px solid var(--mpc-control-border) !important;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-danger-zone){{
border-left:3px solid var(--mpc-danger) !important;
background:var(--mpc-danger-soft);
}}
[data-testid="stMetric"]{{
background:var(--mpc-surface-primary-bg);
border:1px solid var(--mpc-surface-primary-border);
border-left:3px solid var(--mpc-brand);
border-radius:var(--mpc-radius);
padding:.7rem .85rem .65rem;
box-shadow:var(--mpc-shadow);
}}
[data-testid="stMetricLabel"]{{
color:var(--mpc-text-3) !important;
font-size:.78rem !important;
letter-spacing:.04em;
text-transform:uppercase;
}}
[data-testid="stMetricValue"]{{
color:var(--mpc-text) !important;
font-weight:700 !important;
}}
section[data-testid="stMain"] [data-testid="stExpander"],
section[data-testid="stSidebar"] [data-testid="stExpander"]{{
background:transparent;
box-shadow:none;
}}
section[data-testid="stMain"] [data-testid="stExpander"] details,
section[data-testid="stSidebar"] [data-testid="stExpander"] details{{
background:var(--mpc-control-bg) !important;
border:1px solid var(--mpc-control-border) !important;
border-radius:var(--mpc-radius) !important;
box-shadow:none !important;
overflow:hidden;
}}
section[data-testid="stMain"] [data-testid="stExpander"] summary,
section[data-testid="stSidebar"] [data-testid="stExpander"] summary{{
background:var(--mpc-control-bg) !important;
color:var(--mpc-text) !important;
font-weight:600;
overflow-wrap:anywhere;
white-space:normal;
transition:background-color .15s ease !important;
}}
section[data-testid="stMain"] [data-testid="stExpander"] summary:hover,
section[data-testid="stMain"] [data-testid="stExpander"] summary:focus-visible,
section[data-testid="stMain"] [data-testid="stExpander"] summary:active,
section[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover,
section[data-testid="stSidebar"] [data-testid="stExpander"] summary:focus-visible,
section[data-testid="stSidebar"] [data-testid="stExpander"] summary:active{{
background:var(--mpc-control-hover) !important;
}}
section[data-testid="stMain"] [data-testid="stExpander"] details[open] > summary,
section[data-testid="stSidebar"] [data-testid="stExpander"] details[open] > summary{{
background:var(--mpc-control-bg) !important;
}}
section[data-testid="stMain"] [data-testid="stExpander"] details[open] > summary:hover,
section[data-testid="stMain"] [data-testid="stExpander"] details[open] > summary:focus-visible,
section[data-testid="stMain"] [data-testid="stExpander"] details[open] > summary:active,
section[data-testid="stSidebar"] [data-testid="stExpander"] details[open] > summary:hover,
section[data-testid="stSidebar"] [data-testid="stExpander"] details[open] > summary:focus-visible,
section[data-testid="stSidebar"] [data-testid="stExpander"] details[open] > summary:active{{
background:var(--mpc-control-hover) !important;
}}
section[data-testid="stMain"] [data-testid="stExpander"] summary p,
section[data-testid="stMain"] [data-testid="stExpander"] summary [data-testid="stMarkdownContainer"],
section[data-testid="stSidebar"] [data-testid="stExpander"] summary p,
section[data-testid="stSidebar"] [data-testid="stExpander"] summary [data-testid="stMarkdownContainer"]{{
color:var(--mpc-text) !important;
overflow-wrap:anywhere;
white-space:normal;
}}
section[data-testid="stMain"] [data-testid="stExpander"] summary svg,
section[data-testid="stMain"] [data-testid="stExpander"] svg[data-testid="stExpanderToggleIcon"],
section[data-testid="stSidebar"] [data-testid="stExpander"] summary svg,
section[data-testid="stSidebar"] [data-testid="stExpander"] svg[data-testid="stExpanderToggleIcon"]{{
color:var(--mpc-text) !important;
fill:currentColor !important;
flex-shrink:0;
}}
section[data-testid="stMain"] [data-testid="stExpander"] [data-testid="stExpanderDetails"],
section[data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stExpanderDetails"]{{
background:var(--mpc-control-bg) !important;
border-top:1px solid var(--mpc-control-border) !important;
}}
section[data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stExpanderDetails"] [data-testid="stVerticalBlock"]{{
background:var(--mpc-control-bg) !important;
background-color:var(--mpc-control-bg) !important;
}}
[data-testid="stDataFrame"],[data-testid="stDataFrameResizable"]{{
border:1px solid var(--mpc-border);
border-radius:var(--mpc-radius);
overflow:hidden;
background:var(--mpc-white);
}}
section[data-testid="stMain"] table{{
width:100%;
border-collapse:collapse;
background:var(--mpc-white);
}}
section[data-testid="stMain"] thead th{{
background:var(--mpc-brand-soft);
color:var(--mpc-text);
font-weight:650;
border-bottom:1px solid var(--mpc-border);
}}
section[data-testid="stMain"] tbody tr:nth-child(even){{
background:var(--mpc-soft);
}}
[data-testid="stRadio"] label:has(input:checked){{
font-weight:650;
}}
[data-testid="stButton"] button,
[data-testid="stDownloadButton"] button,
[data-testid="stFormSubmitButton"] button,
button[kind="primary"],
[data-testid="stBaseButton-primary"]{{
background-color:var(--mpc-red) !important;
border:1px solid var(--mpc-red) !important;
color:{SURFACE_WHITE} !important;
font-weight:600;
box-shadow:none !important;
}}
[data-testid="stButton"] button p,
[data-testid="stDownloadButton"] button p,
[data-testid="stFormSubmitButton"] button p,
button[kind="primary"] p,
[data-testid="stBaseButton-primary"] p{{
color:{SURFACE_WHITE} !important;
font-weight:600 !important;
}}
[class*="st-key-agenda_new"] button{{
white-space:nowrap;
min-width:max-content;
}}
[data-testid="stButton"] button:hover:not(:disabled),
[data-testid="stDownloadButton"] button:hover:not(:disabled),
[data-testid="stFormSubmitButton"] button:hover:not(:disabled),
button[kind="primary"]:hover:not(:disabled),
[data-testid="stBaseButton-primary"]:hover:not(:disabled){{
background-color:var(--mpc-red-hover) !important;
border-color:var(--mpc-red-hover) !important;
color:{SURFACE_WHITE} !important;
}}
[data-testid="stButton"] button:active:not(:disabled),
[data-testid="stDownloadButton"] button:active:not(:disabled),
[data-testid="stFormSubmitButton"] button:active:not(:disabled),
button[kind="primary"]:active:not(:disabled),
[data-testid="stBaseButton-primary"]:active:not(:disabled){{
background-color:var(--mpc-red-hover) !important;
border-color:var(--mpc-red-hover) !important;
}}
[data-testid="stButton"] button:disabled,
[data-testid="stDownloadButton"] button:disabled,
[data-testid="stFormSubmitButton"] button:disabled,
button[kind="primary"]:disabled,
[data-testid="stBaseButton-primary"]:disabled{{
opacity:.55;
cursor:not-allowed;
}}
section[data-testid="stSidebar"] [class*="st-key-sidebar_home"],
section[data-testid="stSidebar"] [class*="st-key-sidebar_home"] [data-testid="stButton"]{{
background-color:{SIDEBAR_BG} !important;
border:0 !important;
box-shadow:none !important;
}}
section[data-testid="stSidebar"] [class*="st-key-sidebar_home"] button,
section[data-testid="stSidebar"] [class*="st-key-sidebar_home"] button:hover,
section[data-testid="stSidebar"] [class*="st-key-sidebar_home"] button:hover:not(:disabled),
section[data-testid="stSidebar"] [class*="st-key-sidebar_home"] button:active:not(:disabled),
section[data-testid="stSidebar"] [class*="st-key-sidebar_home"] button:focus,
section[data-testid="stSidebar"] [class*="st-key-sidebar_home"] button:focus-visible,
section[data-testid="stSidebar"] [class*="st-key-sidebar_home"] button:disabled{{
background-color:{SIDEBAR_BG} !important;
border:0 !important;
box-shadow:none !important;
color:transparent !important;
opacity:1 !important;
min-height:0;
padding:0 !important;
}}
section[data-testid="stSidebar"] [class*="st-key-sidebar_home"] button p{{
color:transparent !important;
font-size:0 !important;
}}
[data-testid="stNumberInput"] button,
[data-testid="stNumberInputStepUp"],
[data-testid="stNumberInputStepDown"],
[data-testid="stSelectbox"] button,
[data-testid="stMultiSelect"] button,
[data-testid="stDateInput"] button,
[data-testid="stTimeInput"] button{{
background:transparent !important;
background-color:transparent !important;
border:0 !important;
box-shadow:none !important;
color:var(--mpc-text-2) !important;
min-height:auto;
padding:0 !important;
}}
[data-testid="stNumberInput"] button:hover:not(:disabled),
[data-testid="stNumberInputStepUp"]:hover:not(:disabled),
[data-testid="stNumberInputStepDown"]:hover:not(:disabled){{
background-color:var(--mpc-control-hover) !important;
color:var(--mpc-text) !important;
}}
button:focus-visible{{
outline:2px solid var(--mpc-red) !important;
outline-offset:2px;
}}
.mpc-empty-state{{
margin:.45rem 0 .75rem;
padding:.8rem 1rem .75rem;
background:var(--mpc-empty-state-bg);
border:1px solid var(--mpc-empty-state-border);
border-left:3px solid var(--mpc-red);
border-radius:var(--mpc-radius);
box-shadow:none;
color:var(--mpc-text);
}}
.mpc-empty-state p{{
margin:0;
color:var(--mpc-text);
font-size:.95rem;
line-height:1.45;
font-weight:450;
}}
section[data-testid="stMain"] [data-testid="stElementContainer"]:has(.mpc-empty-state),
section[data-testid="stMain"] [data-testid="stMarkdown"]:has(.mpc-empty-state),
section[data-testid="stMain"] [data-testid="stMarkdownContainer"]:has(.mpc-empty-state){{
background:transparent !important;
background-color:transparent !important;
border:0 !important;
box-shadow:none !important;
padding:0 !important;
}}
.mpc-record{{
margin:0;
padding:.05rem 0 .15rem;
}}
.mpc-stack .mpc-record,
.mpc-record-boxed{{
background:var(--mpc-card-operational-bg);
border:1px solid var(--mpc-card-operational-border);
border-left-width:3px;
border-left-style:solid;
border-radius:var(--mpc-radius);
box-shadow:var(--mpc-shadow);
padding:.75rem .9rem .7rem;
}}
.mpc-stack .mpc-record--brand,.mpc-record-boxed.mpc-record--brand{{border-left-color:var(--mpc-brand);}}
.mpc-stack .mpc-record--success,.mpc-record-boxed.mpc-record--success{{border-left-color:var(--mpc-success);}}
.mpc-stack .mpc-record--warning,.mpc-record-boxed.mpc-record--warning{{border-left-color:var(--mpc-warning);}}
.mpc-stack .mpc-record--danger,.mpc-record-boxed.mpc-record--danger{{border-left-color:var(--mpc-danger);}}
.mpc-stack .mpc-record--muted,.mpc-record-boxed.mpc-record--muted{{border-left-color:var(--mpc-border-md);background:var(--mpc-soft);}}
.mpc-stack .mpc-record--neutral,.mpc-record-boxed.mpc-record--neutral{{border-left-color:var(--mpc-border-md);}}
.mpc-stack .mpc-record--info,.mpc-record-boxed.mpc-record--info{{border-left-color:var(--mpc-info);}}
.mpc-stack .mpc-stripe-a,.mpc-stack .mpc-card-even,.mpc-record-boxed.mpc-stripe-a,.mpc-record-boxed.mpc-card-even{{background:var(--mpc-card-a);}}
.mpc-stack .mpc-stripe-b,.mpc-stack .mpc-card-odd,.mpc-record-boxed.mpc-stripe-b,.mpc-record-boxed.mpc-card-odd{{background:var(--mpc-card-b);border-color:var(--mpc-card-border-b);}}
.mpc-stack .mpc-surface-brand,.mpc-record-boxed.mpc-surface-brand{{background:var(--mpc-brand-soft);}}
.mpc-stack .mpc-surface-success,.mpc-record-boxed.mpc-surface-success{{background:var(--mpc-success-soft);}}
.mpc-stack .mpc-surface-warning,.mpc-record-boxed.mpc-surface-warning{{background:var(--mpc-warning-soft);}}
.mpc-stack .mpc-surface-danger,.mpc-record-boxed.mpc-surface-danger{{background:var(--mpc-danger-soft);}}
.mpc-stack .mpc-surface-muted,.mpc-record-boxed.mpc-surface-muted{{background:var(--mpc-muted-bg);}}
.mpc-def-block{{
margin:.35rem 0 .7rem;
padding:.7rem .85rem .6rem;
background:var(--mpc-brand-soft);
border:1px solid var(--mpc-border-md);
border-radius:8px;
}}
.mpc-def{{
display:grid;
grid-template-columns:minmax(7rem,11rem) 1fr;
gap:.2rem .75rem;
padding:.18rem 0;
border-bottom:1px solid rgba(230,226,227,.9);
}}
.mpc-def:last-child{{border-bottom:0;}}
.mpc-def-label{{
font-size:.75rem;
font-weight:700;
letter-spacing:.04em;
text-transform:uppercase;
color:var(--mpc-text-3);
}}
.mpc-def-value{{
font-size:.92rem;
color:var(--mpc-text);
overflow-wrap:anywhere;
}}
@media (max-width:768px){{
.mpc-def{{grid-template-columns:1fr;}}
}}
.mpc-record-head{{
display:flex;
align-items:flex-start;
justify-content:space-between;
gap:.75rem;
flex-wrap:wrap;
}}
.mpc-record-title{{
margin:0;
font-size:1.02rem;
font-weight:700;
color:var(--mpc-text);
line-height:1.35;
overflow-wrap:anywhere;
flex:1 1 12rem;
}}
.mpc-record-badges{{
display:flex;
flex-wrap:wrap;
gap:.3rem;
justify-content:flex-end;
}}
.mpc-record-secondary{{
margin:.28rem 0 0;
font-size:.9rem;
font-weight:500;
color:var(--mpc-text-2);
line-height:1.4;
overflow-wrap:anywhere;
}}
.mpc-record-meta{{
margin:.2rem 0 0;
font-size:.82rem;
color:var(--mpc-text-3);
line-height:1.4;
overflow-wrap:anywhere;
}}
.mpc-badge{{
display:inline-block;
padding:.12rem .48rem;
border-radius:999px;
font-size:.68rem;
font-weight:700;
letter-spacing:.04em;
line-height:1.3;
white-space:nowrap;
text-transform:uppercase;
border:1px solid transparent;
}}
.mpc-badge--brand{{background:var(--mpc-brand-soft);color:var(--mpc-brand-dark);border-color:rgba({primary_rgb},.16);}}
.mpc-badge--success{{background:var(--mpc-success-soft);color:var(--mpc-success);border-color:rgba(46,125,79,.18);}}
.mpc-badge--warning{{background:var(--mpc-warning-soft);color:var(--mpc-warning);border-color:rgba(181,129,18,.22);}}
.mpc-badge--danger{{background:var(--mpc-danger-soft);color:var(--mpc-danger);border-color:rgba(176,42,42,.2);}}
.mpc-badge--muted,.mpc-badge--neutral{{background:var(--mpc-muted-bg);color:var(--mpc-text-2);border-color:var(--mpc-border);}}
.mpc-badge--info{{background:#EEE8E6;color:var(--mpc-text);border-color:#DED5D2;}}
section[data-testid="stSidebar"] .mpc-record-boxed,
section[data-testid="stSidebar"] .mpc-record-boxed.mpc-surface-brand,
section[data-testid="stSidebar"] .mpc-record-boxed.mpc-surface-success,
section[data-testid="stSidebar"] .mpc-record-boxed.mpc-surface-warning,
section[data-testid="stSidebar"] .mpc-record-boxed.mpc-surface-danger,
section[data-testid="stSidebar"] .mpc-record-boxed.mpc-surface-muted,
section[data-testid="stSidebar"] .mpc-record-boxed.mpc-surface-info,
section[data-testid="stSidebar"] .mpc-record-boxed.mpc-stripe-a,
section[data-testid="stSidebar"] .mpc-record-boxed.mpc-stripe-b{{
background:#F5F2F1 !important;
border:1px solid #DED5D2 !important;
border-left:3px solid var(--mpc-red) !important;
box-shadow:none;
}}
section[data-testid="stSidebar"] .mpc-record-title,
section[data-testid="stSidebar"] .mpc-record-secondary,
section[data-testid="stSidebar"] .mpc-record-meta{{
color:var(--mpc-text);
}}
section[data-testid="stSidebar"] .mpc-record-secondary,
section[data-testid="stSidebar"] .mpc-record-meta{{
color:var(--mpc-text-2);
}}
html body .stApp .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed,
html body .stApp .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-surface-brand,
html body .stApp .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-surface-success,
html body .stApp .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-surface-warning,
html body .stApp .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-surface-danger,
html body .stApp .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-surface-muted,
html body .stApp .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-surface-info,
html body .stApp .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-stripe-a,
html body .stApp .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-stripe-b,
html body [data-testid="stPopoverBody"] .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed,
html body [data-testid="stPopoverBody"] .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-surface-brand,
html body [data-testid="stPopoverBody"] .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-surface-success,
html body [data-testid="stPopoverBody"] .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-surface-warning,
html body [data-testid="stPopoverBody"] .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-surface-danger,
html body [data-testid="stPopoverBody"] .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-surface-muted,
html body [data-testid="stPopoverBody"] .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-surface-info,
html body [data-testid="stPopoverBody"] .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-stripe-a,
html body [data-testid="stPopoverBody"] .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed.mpc-stripe-b{{
background:#FFF2BF !important;
background-color:#FFF2BF !important;
border:1px solid #E7C968 !important;
border-left:3px solid #C99800 !important;
box-shadow:none;
color:#2B2B2B !important;
}}
html body .stApp .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed:hover,
html body [data-testid="stPopoverBody"] .mpc-bell-alert .mpc-sidebar-alert-card.mpc-record-boxed:hover{{
background:#FFEBA6 !important;
background-color:#FFEBA6 !important;
}}
html body .stApp .mpc-bell-alert .mpc-sidebar-alert-card .mpc-record-title,
html body [data-testid="stPopoverBody"] .mpc-bell-alert .mpc-sidebar-alert-card .mpc-record-title{{
color:#2B2B2B !important;
}}
html body .stApp .mpc-bell-alert .mpc-sidebar-alert-card .mpc-record-secondary,
html body .stApp .mpc-bell-alert .mpc-sidebar-alert-card .mpc-record-meta,
html body [data-testid="stPopoverBody"] .mpc-bell-alert .mpc-sidebar-alert-card .mpc-record-secondary,
html body [data-testid="stPopoverBody"] .mpc-bell-alert .mpc-sidebar-alert-card .mpc-record-meta{{
color:#6E6040 !important;
}}
html body .stApp .mpc-bell-alert .mpc-sidebar-alert-card .mpc-badge--info,
html body [data-testid="stPopoverBody"] .mpc-bell-alert .mpc-sidebar-alert-card .mpc-badge--info{{
background:#F2D675 !important;
color:#594500 !important;
border-color:#E7C968 !important;
}}
.mpc-section-label{{
margin:1.05rem 0 .4rem;
font-size:.78rem;
font-weight:700;
letter-spacing:.06em;
text-transform:uppercase;
color:var(--mpc-text-3);
}}
.mpc-trip{{
margin:.45rem 0 0;
padding:.55rem .7rem;
background:var(--mpc-soft);
border:1px solid var(--mpc-border);
border-radius:8px;
}}
.mpc-trip-title{{
margin:0 0 .2rem;
font-size:.82rem;
font-weight:700;
color:var(--mpc-text-2);
}}
.mpc-filter-note{{
margin:0;
}}
.mpc-stack{{
display:flex;
flex-direction:column;
gap:.55rem;
}}
@media (max-width:768px){{
.mpc-record-head{{flex-direction:column;gap:.35rem;}}
.mpc-record-badges{{justify-content:flex-start;}}
/* Keep the bell panel inside the visible mobile viewport, including iOS safe areas. */
section[data-testid="stSidebar"] [data-testid="stPopover"] [data-baseweb="popover"]:has(.mpc-bell-alert),
[data-baseweb="popover"]:has(.mpc-bell-alert){{
position:fixed !important;
top:max(12px, env(safe-area-inset-top)) !important;
bottom:auto !important;
left:12px !important;
right:12px !important;
transform:none !important;
width:auto !important;
max-width:none !important;
z-index:10002 !important;
}}
section[data-testid="stSidebar"] [data-testid="stPopover"] [data-testid="stPopoverBody"]:has(.mpc-bell-alert),
[data-testid="stPopoverBody"]:has(.mpc-bell-alert){{
box-sizing:border-box;
width:100%;
max-width:100%;
height:auto !important;
min-height:0 !important;
max-height:calc(100dvh - 24px - env(safe-area-inset-top) - env(safe-area-inset-bottom)) !important;
overflow-x:hidden !important;
overflow-y:auto !important;
}}
[data-testid="stPopoverBody"]:has(.mpc-bell-alert) [data-testid="stVerticalBlock"]{{
gap:.5rem;
}}
[data-testid="stPopoverBody"] .mpc-bell-alert .mpc-sidebar-alert-card{{
box-sizing:border-box;
height:auto;
min-height:0;
padding:.55rem .65rem;
overflow-wrap:anywhere;
}}
[data-testid="stPopoverBody"] .mpc-bell-alert .mpc-record-title{{
flex:none;
}}
[data-testid="stPopoverBody"]:has(.mpc-bell-alert) button{{
min-height:2.5rem;
white-space:normal;
overflow-wrap:anywhere;
}}
section[data-testid="stMain"] [data-testid="stExpander"] summary{{
white-space:normal;
overflow-wrap:anywhere;
}}
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_operational"],
section[data-testid="stMain"] [class*="st-key-mpc_card_operational"] > div,
section[data-testid="stMain"] [class*="st-key-mpc_card_operational"] [data-testid="stVerticalBlockBorderWrapper"],
section[data-testid="stMain"] [class*="st-key-mpc_card_operational"] [data-testid="stVerticalBlock"],
section[data-testid="stMain"] [class*="st-key-mpc_card_a"],
section[data-testid="stMain"] [class*="st-key-mpc_card_a"] > div,
section[data-testid="stMain"] [class*="st-key-mpc_card_a"] [data-testid="stVerticalBlockBorderWrapper"],
section[data-testid="stMain"] [class*="st-key-mpc_card_a"] [data-testid="stVerticalBlock"]{{
background:{CARD_SURFACE_A} !important;
background-color:{CARD_SURFACE_A} !important;
border-color:{CARD_BORDER_A} !important;
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_b"],
section[data-testid="stMain"] [class*="st-key-mpc_card_b"] > div,
section[data-testid="stMain"] [class*="st-key-mpc_card_b"] [data-testid="stVerticalBlockBorderWrapper"],
section[data-testid="stMain"] [class*="st-key-mpc_card_b"] [data-testid="stVerticalBlock"]{{
background:{CARD_SURFACE_B} !important;
background-color:{CARD_SURFACE_B} !important;
border-color:{CARD_BORDER_B} !important;
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_danger"],
section[data-testid="stMain"] [class*="st-key-mpc_card_danger"] > div,
section[data-testid="stMain"] [class*="st-key-mpc_card_danger"] [data-testid="stVerticalBlockBorderWrapper"],
section[data-testid="stMain"] [class*="st-key-mpc_card_danger"] [data-testid="stVerticalBlock"]{{
background:{DANGER_SOFT} !important;
background-color:{DANGER_SOFT} !important;
border-color:rgba(176,42,42,.35) !important;
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_operational"]:hover,
section[data-testid="stMain"] [class*="st-key-mpc_card_a"]:hover,
section[data-testid="stMain"] [class*="st-key-mpc_card_b"]:hover{{
border-color:rgba({primary_rgb},.22) !important;
box-shadow:0 1px 4px rgba(32,40,50,.06);
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_detail"],
section[data-testid="stMain"] [class*="st-key-mpc_card_detail"] > div,
section[data-testid="stMain"] [class*="st-key-mpc_card_detail"] [data-testid="stVerticalBlockBorderWrapper"],
section[data-testid="stMain"] [class*="st-key-mpc_card_detail"] [data-testid="stVerticalBlock"]{{
background:{CARD_OPERATIONAL_BG} !important;
background-color:{CARD_OPERATIONAL_BG} !important;
border-color:{CARD_OPERATIONAL_BORDER} !important;
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-testid="stForm"],
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-testid="stForm"] form,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-form-mark),
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-testid="stVerticalBlock"]:has(.mpc-form-mark){{
background:var(--mpc-brand-soft) !important;
background-color:var(--mpc-brand-soft) !important;
border:1px solid var(--mpc-border-md) !important;
border-radius:8px;
box-shadow:none !important;
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="select"] > div,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="select"] > div > div,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="input"],
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="input"] > div,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="base-input"],
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="textarea"],
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-testid="stTextAreaRootElement"]{{
background:var(--mpc-themed-control-bg) !important;
background-color:var(--mpc-themed-control-bg) !important;
border-color:var(--mpc-themed-control-border) !important;
box-shadow:inset 0 0 0 1px var(--mpc-themed-control-border);
color:var(--mpc-themed-control-fg) !important;
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="select"] > div:hover,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="select"] > div > div:hover,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="input"]:hover,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="textarea"]:hover,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-testid="stTextAreaRootElement"]:hover{{
background:var(--mpc-themed-control-hover) !important;
background-color:var(--mpc-themed-control-hover) !important;
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="select"] > div:focus-within,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="input"]:focus-within,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="textarea"]:focus-within,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-testid="stTextAreaRootElement"]:focus-within{{
background:var(--mpc-themed-control-bg) !important;
border-color:var(--mpc-brand) !important;
box-shadow:0 0 0 1px var(--mpc-brand) !important;
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="input"] input,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="select"] input,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] textarea{{
background:transparent !important;
color:var(--mpc-themed-control-fg) !important;
caret-color:var(--mpc-themed-control-fg);
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="input"] input::placeholder,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] textarea::placeholder{{
color:var(--mpc-themed-control-placeholder) !important;
opacity:1;
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="select"] svg,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="input"] svg{{
color:var(--mpc-themed-control-fg);
fill:currentColor;
opacity:.82;
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="select"][aria-disabled="true"] > div,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] [data-baseweb="input"] input:disabled,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_acompanhamento_"] textarea:disabled{{
background:var(--mpc-themed-control-hover) !important;
background-color:var(--mpc-themed-control-hover) !important;
color:var(--mpc-themed-control-fg) !important;
opacity:.72;
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_historico_"] .mpc-oficios-historico-table table{{
width:100%;
border-collapse:collapse;
background:transparent;
color:var(--mpc-text);
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_historico_"] .mpc-oficios-historico-table th,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_historico_"] .mpc-oficios-historico-table td{{
padding:.55rem .7rem;
border-bottom:1px solid rgba(230,226,227,.9);
text-align:left;
color:var(--mpc-text);
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_historico_"] .mpc-oficios-historico-table thead th{{
background:var(--mpc-themed-table-header-bg);
color:var(--mpc-themed-table-fg);
font-weight:700;
border-bottom:1px solid var(--mpc-themed-table-border);
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_historico_"] .mpc-oficios-historico-table tbody tr:nth-child(even) td{{
background:var(--mpc-themed-control-hover);
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_historico_"] .mpc-oficios-historico-table tbody tr:nth-child(odd) td{{
background:transparent;
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_operational"] [data-testid="stHorizontalBlock"] > div,
section[data-testid="stMain"] [class*="st-key-mpc_card_a"] [data-testid="stHorizontalBlock"] > div,
section[data-testid="stMain"] [class*="st-key-mpc_card_b"] [data-testid="stHorizontalBlock"] > div,
section[data-testid="stMain"] [class*="st-key-mpc_card_danger"] [data-testid="stHorizontalBlock"] > div,
section[data-testid="stMain"] [class*="st-key-mpc_card_operational"] .mpc-record,
section[data-testid="stMain"] [class*="st-key-mpc_card_a"] .mpc-record,
section[data-testid="stMain"] [class*="st-key-mpc_card_b"] .mpc-record,
section[data-testid="stMain"] [class*="st-key-mpc_card_danger"] .mpc-record{{
background:transparent !important;
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_"] button,
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stBaseButton-primary"],
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stBaseButton-secondary"],
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stBaseButton-tertiary"],
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [class*="delete"] button,
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [class*="excluir"] button,
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [class*="cancel"] button{{
background:{BRAND_RED} !important;
background-color:{BRAND_RED} !important;
border:1px solid {BRAND_RED} !important;
color:{SURFACE_WHITE} !important;
font-weight:600 !important;
border-radius:.5rem !important;
padding:.4rem .9rem !important;
min-height:2.4rem;
box-shadow:none !important;
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_"] button p,
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stBaseButton-primary"] p,
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stBaseButton-secondary"] p,
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stBaseButton-tertiary"] p{{
color:{SURFACE_WHITE} !important;
font-weight:600 !important;
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_"] button:hover:not(:disabled),
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stBaseButton-primary"]:hover:not(:disabled),
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stBaseButton-secondary"]:hover:not(:disabled),
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stBaseButton-tertiary"]:hover:not(:disabled){{
background:{BRAND_RED_DARK} !important;
background-color:{BRAND_RED_DARK} !important;
border-color:{BRAND_RED_DARK} !important;
color:{SURFACE_WHITE} !important;
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_"] button:active:not(:disabled),
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stBaseButton-secondary"]:active:not(:disabled){{
background:{BRAND_RED_DARK} !important;
border-color:{BRAND_RED_DARK} !important;
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_"] button:disabled,
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stBaseButton-secondary"]:disabled{{
opacity:.55;
cursor:not-allowed;
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stExpander"] [data-testid="stVerticalBlock"],
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stExpander"] [data-testid="stVerticalBlockBorderWrapper"]{{
background:transparent !important;
background-color:transparent !important;
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stExpander"] details{{
background:var(--mpc-expander) !important;
background-color:var(--mpc-expander) !important;
border:1px solid var(--mpc-expander-border) !important;
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stExpander"] summary{{
background:var(--mpc-expander) !important;
background-color:var(--mpc-expander) !important;
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stExpander"] summary:hover,
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stExpander"] summary:focus-visible,
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stExpander"] summary:active{{
background:var(--mpc-expander-hover) !important;
background-color:var(--mpc-expander-hover) !important;
}}
section[data-testid="stMain"] [class*="st-key-mpc_card_"] [data-testid="stExpander"] [data-testid="stExpanderDetails"]{{
background:var(--mpc-control-bg) !important;
background-color:var(--mpc-control-bg) !important;
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_excluir_"] [data-testid="stExpander"] details,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_excluir_"] [data-testid="stExpander"] summary,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_excluir_"] [data-testid="stExpander"] [data-testid="stExpanderDetails"]{{
background:var(--mpc-brand-soft) !important;
background-color:var(--mpc-brand-soft) !important;
border-color:var(--mpc-border-md) !important;
color:var(--mpc-text) !important;
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_excluir_"] [data-testid="stExpander"] details{{
border:1px solid var(--mpc-border-md) !important;
border-radius:8px !important;
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_excluir_"] [data-testid="stExpander"] summary:hover,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_excluir_"] [data-testid="stExpander"] summary:focus-visible,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_excluir_"] [data-testid="stExpander"] summary:active,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_excluir_"] [data-testid="stExpander"] details[open] > summary,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_excluir_"] [data-testid="stExpander"] details[open] > summary:hover{{
background:var(--mpc-card-institutional-bg) !important;
background-color:var(--mpc-card-institutional-bg) !important;
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_excluir_"] [data-testid="stExpander"] summary p,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_excluir_"] [data-testid="stExpander"] summary [data-testid="stMarkdownContainer"]{{
color:var(--mpc-text) !important;
}}
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_excluir_"] [data-testid="stExpander"] summary svg,
section[data-testid="stMain"] [class*="st-key-oficios_recebidos_excluir_"] [data-testid="stExpander"] svg[data-testid="stExpanderToggleIcon"]{{
color:var(--mpc-text-2) !important;
fill:currentColor !important;
}}
""".strip()
    if theme_name == "vermelho":
        return css
    return css + """
[data-testid="stRadio"] input,
[data-testid="stCheckbox"] input,
[data-testid="stToggle"] input{
accent-color:var(--mpc-brand) !important;
}
label[data-baseweb="radio"]:has(input:checked) > div:first-child{
background-color:var(--mpc-brand) !important;
}
label[data-baseweb="checkbox"]:has(input:checked) > span:first-child{
background-color:var(--mpc-brand) !important;
border-color:var(--mpc-brand) !important;
}
[data-testid="stTabs"] button[aria-selected="true"]{
color:var(--mpc-brand) !important;
}
[data-testid="stTabs"] [data-baseweb="tab-highlight"]{
background-color:var(--mpc-brand) !important;
}
[data-testid="stMultiSelect"] [data-baseweb="tag"]{
background-color:var(--mpc-brand-soft) !important;
color:var(--mpc-brand-dark) !important;
}
"""


def apply_theme(theme_name="vermelho"):
    """Inject the portal stylesheet. Call once from the portal shell per run."""
    st.markdown("<style>" + _css(theme_name) + "</style>", unsafe_allow_html=True)


def html_text(value):
    return escape(str(value or "").strip())


def _tone(name):
    name = (name or "neutral").lower()
    return name if name in TONES else "neutral"


def badge(text, tone="neutral"):
    if not text:
        return ""
    return (
        f'<span class="mpc-badge mpc-badge--{_tone(tone)}">{html_text(text)}</span>'
    )


def badges(*items):
    parts = []
    for item in items:
        if not item:
            continue
        if isinstance(item, (tuple, list)):
            text, tone = item[0], item[1] if len(item) > 1 else "neutral"
        else:
            text, tone = item, "neutral"
        mark = badge(text, tone)
        if mark:
            parts.append(mark)
    return "".join(parts)


def status_tone(value):
    raw = str(value or "").strip()
    mapped = _PRIORITY_TONES.get(raw.upper())
    if mapped:
        return mapped
    text = raw.casefold()
    if not text:
        return "neutral"
    if any(token in text for token in _STATUS_DANGER):
        return "danger"
    if any(token in text for token in _STATUS_MUTED):
        return "muted"
    if any(token in text for token in _STATUS_SUCCESS):
        return "success"
    if any(token in text for token in _STATUS_WARNING):
        return "warning"
    if any(token in text for token in _STATUS_BRAND):
        return "brand"
    return "neutral"


def priority_tone(value):
    return _PRIORITY_TONES.get(str(value or "").upper(), "neutral")


def record_html(
    title,
    *,
    badges_html="",
    secondary="",
    meta="",
    accent="brand",
    extra="",
    boxed=False,
    surface=None,
    stripe=None,
):
    accent = _tone(accent)
    surface_class = f" mpc-surface-{_tone(surface)}" if surface else ""
    kind = stripe if stripe in ("a", "b") else ""
    stripe_class = f" mpc-stripe-{kind} mpc-card-{'even' if kind == 'a' else 'odd'}" if kind else ""
    boxed_class = " mpc-record-boxed" if boxed else ""
    head = ['<div class="mpc-record-head">']
    head.append(f'<p class="mpc-record-title">{html_text(title)}</p>')
    if badges_html:
        head.append(f'<div class="mpc-record-badges">{badges_html}</div>')
    head.append("</div>")
    parts = [
        f'<div class="mpc-record mpc-record--{accent}{surface_class}{stripe_class}{boxed_class}">',
        *head,
    ]
    if secondary:
        parts.append(f'<p class="mpc-record-secondary">{html_text(secondary)}</p>')
    if meta:
        parts.append(f'<p class="mpc-record-meta">{html_text(meta)}</p>')
    if extra:
        parts.append(extra)
    parts.append("</div>")
    return "".join(parts)


def render_html(markup):
    if markup:
        st.markdown(markup, unsafe_allow_html=True)


def render_record(*args, **kwargs):
    render_html(record_html(*args, **kwargs))


def render_records(blocks):
    if not blocks:
        return
    render_html('<div class="mpc-stack">' + "".join(blocks) + "</div>")


def section_label(text):
    render_html(f'<p class="mpc-section-label">{html_text(text)}</p>')


def trip_html(title="Logística de viagem", body=""):
    inner = f'<p class="mpc-trip-title">{html_text(title)}</p>'
    if body:
        inner += f'<p class="mpc-record-meta">{html_text(body)}</p>'
    return f'<div class="mpc-trip">{inner}</div>'


def empty_state(text):
    """Single institutional empty-result notice. Not a KPI card or st.info."""
    render_html(
        f'<div class="mpc-empty-state"><p>{html_text(text)}</p></div>'
    )


def filter_mark():
    render_html('<div class="mpc-filter-mark" hidden></div>')


def institutional_card_mark():
    render_html('<div class="mpc-institutional-card-mark mpc-home-card-mark" hidden></div>')


def operational_card_mark():
    render_html('<div class="mpc-operational-card-mark" hidden></div>')


def actions_mark():
    render_html('<div class="mpc-actions" hidden></div>')


def kpi_mark(tone="brand"):
    render_html(f'<div class="mpc-kpi-mark mpc-kpi-mark--{_tone(tone)}" hidden></div>')


def form_mark():
    render_html('<div class="mpc-form-mark" hidden></div>')


def stripe_index(index):
    return "b" if index % 2 else "a"


@contextmanager
def card_container(index, identity, *, critical=False, border=True):
    """Paint an operational listing card with positional A/B stripe.

    ``critical`` is kept for call-site compatibility; fill follows list order,
    not status. Streamlit exposes ``key`` as ``st-key-`` on the wrapper.
    """
    _ = critical
    variant = stripe_index(index)
    with st.container(border=border, key=f"mpc_card_{variant}_{identity}"):
        stripe_mark(index)
        yield


def stripe_mark(index):
    kind = stripe_index(index)
    parity = "even" if kind == "a" else "odd"
    render_html(
        f'<div class="mpc-stripe mpc-stripe-{kind} mpc-card-{parity}" hidden></div>'
    )


def detail_mark():
    render_html('<div class="mpc-detail-mark" hidden></div>')


def definition_block(title, rows):
    items = []
    for label, value in rows:
        if value in (None, ""):
            continue
        items.append(
            '<div class="mpc-def">'
            f'<span class="mpc-def-label">{html_text(label)}</span>'
            f'<span class="mpc-def-value">{html_text(value)}</span>'
            "</div>"
        )
    if not items:
        return
    heading = f'<p class="mpc-section-label">{html_text(title)}</p>' if title else ""
    render_html(f'<div class="mpc-def-block">{heading}{"".join(items)}</div>')
