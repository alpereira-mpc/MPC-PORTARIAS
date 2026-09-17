"""Central visual language for the Ferramentas MPC-PB portal.

Tokens, one stylesheet, and small HTML helpers. No business rules, queries,
or Streamlit widget keys live here. CSS is injected once per script run.
"""

from html import escape

import streamlit as st

from services.branding import BRAND_RED

# --- Tokens (aligned with .streamlit/config.toml) ---
BRAND_RED_DARK = "#7E121C"
BRAND_RED_SOFT = "#F8EEF0"
SURFACE_WHITE = "#FFFFFF"
SURFACE_PAGE = "#F6F5F4"
SURFACE_SOFT = "#F7F6F5"
SURFACE_MUTED = "#F0EEEF"
BORDER_LIGHT = "#E6E2E3"
BORDER_MEDIUM = "#D5D0D1"
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

def _css():
    return f"""
:root{{
--mpc-brand:{BRAND_RED};
--mpc-brand-dark:{BRAND_RED_DARK};
--mpc-brand-soft:{BRAND_RED_SOFT};
--mpc-page:{SURFACE_PAGE};
--mpc-white:{SURFACE_WHITE};
--mpc-soft:{SURFACE_SOFT};
--mpc-muted-bg:{SURFACE_MUTED};
--mpc-border:{BORDER_LIGHT};
--mpc-border-md:{BORDER_MEDIUM};
--mpc-text:{TEXT_PRIMARY};
--mpc-text-2:{TEXT_SECONDARY};
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
--mpc-shadow:0 1px 2px rgba(32,40,50,.045);
}}
.stApp,[data-testid="stAppViewContainer"],[data-testid="stHeader"]{{
background:var(--mpc-page);
}}
[data-testid="stHeader"]{{background:rgba(246,245,244,.92);}}
section[data-testid="stMain"] [data-testid="stMainBlockContainer"]{{
padding-top:1.35rem;
}}
section[data-testid="stSidebar"]{{
border-right:1px solid var(--mpc-border);
}}
section[data-testid="stSidebar"] [data-testid="stCaption"]{{
color:var(--mpc-text-2);
}}
section[data-testid="stSidebar"] [data-testid="stRadio"] label{{
border-radius:8px;
transition:background .12s ease;
}}
section[data-testid="stSidebar"] [data-testid="stRadio"] label:hover{{
background:rgba(155,23,36,.05);
}}
section[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked){{
background:rgba(155,23,36,.09);
box-shadow:inset 3px 0 0 var(--mpc-brand);
font-weight:650;
}}
section[data-testid="stSidebar"] [data-testid="stExpander"]{{
background:var(--mpc-white);
border:1px solid var(--mpc-border);
border-radius:var(--mpc-radius);
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
[data-testid="stVerticalBlockBorderWrapper"]{{
background:var(--mpc-white);
border:1px solid var(--mpc-border) !important;
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
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-home-card-mark){{
border-left:3px solid var(--mpc-brand) !important;
background:var(--mpc-white);
transition:box-shadow .15s ease,border-color .15s ease;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-home-card-mark):hover{{
box-shadow:0 2px 10px rgba(32,40,50,.07);
border-color:var(--mpc-border-md) !important;
}}
[data-testid="stVerticalBlockBorderWrapper"]:has(.mpc-danger-zone){{
border-left:3px solid var(--mpc-danger) !important;
background:var(--mpc-danger-soft);
}}
[data-testid="stMetric"]{{
background:var(--mpc-white);
border:1px solid var(--mpc-border);
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
[data-testid="stExpander"]{{
background:var(--mpc-soft);
border:1px solid var(--mpc-border);
border-radius:var(--mpc-radius);
}}
[data-testid="stExpander"] details{{
border:0;
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
section[data-testid="stMain"] button[kind="primary"],
section[data-testid="stMain"] [data-testid="stBaseButton-primary"]{{
background:var(--mpc-brand) !important;
border-color:var(--mpc-brand) !important;
color:{SURFACE_WHITE} !important;
}}
section[data-testid="stMain"] button[kind="primary"]:hover:not(:disabled),
section[data-testid="stMain"] [data-testid="stBaseButton-primary"]:hover:not(:disabled){{
background:var(--mpc-brand-dark) !important;
border-color:var(--mpc-brand-dark) !important;
}}
section[data-testid="stMain"] button[kind="secondary"],
section[data-testid="stMain"] [data-testid="stBaseButton-secondary"]{{
background:var(--mpc-white) !important;
color:var(--mpc-text) !important;
border:1px solid var(--mpc-border-md) !important;
}}
section[data-testid="stMain"] button[kind="secondary"]:hover:not(:disabled),
section[data-testid="stMain"] [data-testid="stBaseButton-secondary"]:hover:not(:disabled){{
border-color:var(--mpc-brand) !important;
color:var(--mpc-brand-dark) !important;
}}
section[data-testid="stMain"] button[kind="primary"]:disabled,
section[data-testid="stMain"] [data-testid="stBaseButton-primary"]:disabled,
section[data-testid="stMain"] button[kind="secondary"]:disabled,
section[data-testid="stMain"] [data-testid="stBaseButton-secondary"]:disabled{{
opacity:.55;
cursor:not-allowed;
}}
button:focus-visible{{
outline:2px solid var(--mpc-brand) !important;
outline-offset:2px;
}}
div[class*="st-key-"][class*="delete"] button,
div[class*="st-key-"][class*="excluir"] button,
div[class*="st-key-acesso_delete"] button,
div[class*="st-key-task_cancel"] button,
div[class*="st-key-agenda_cancel"] button,
div[class*="st-key-agenda_leave_cancel"] button,
div[class*="st-key-agenda_delete"] button{{
background:var(--mpc-white) !important;
color:var(--mpc-danger) !important;
border:1px solid rgba(176,42,42,.35) !important;
}}
.mpc-record{{
margin:0;
padding:.05rem 0 .15rem;
}}
.mpc-stack .mpc-record,
.mpc-record-boxed{{
background:var(--mpc-white);
border:1px solid var(--mpc-border);
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
font-size:.95rem;
color:var(--mpc-text);
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
.mpc-badge--brand{{background:var(--mpc-brand-soft);color:var(--mpc-brand-dark);border-color:rgba(155,23,36,.16);}}
.mpc-badge--success{{background:var(--mpc-success-soft);color:var(--mpc-success);border-color:rgba(46,125,79,.18);}}
.mpc-badge--warning{{background:var(--mpc-warning-soft);color:var(--mpc-warning);border-color:rgba(181,129,18,.22);}}
.mpc-badge--danger{{background:var(--mpc-danger-soft);color:var(--mpc-danger);border-color:rgba(176,42,42,.2);}}
.mpc-badge--muted,.mpc-badge--neutral{{background:var(--mpc-muted-bg);color:var(--mpc-text-2);border-color:var(--mpc-border);}}
.mpc-badge--info{{background:var(--mpc-info-soft);color:var(--mpc-info);border-color:rgba(61,90,128,.18);}}
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
}}
""".strip()


def apply_theme():
    """Inject the portal stylesheet. Call once from the portal shell per run."""
    st.markdown("<style>" + _css() + "</style>", unsafe_allow_html=True)


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
):
    accent = _tone(accent)
    boxed_class = " mpc-record-boxed" if boxed else ""
    head = ['<div class="mpc-record-head">']
    head.append(f'<p class="mpc-record-title">{html_text(title)}</p>')
    if badges_html:
        head.append(f'<div class="mpc-record-badges">{badges_html}</div>')
    head.append("</div>")
    parts = [f'<div class="mpc-record mpc-record--{accent}{boxed_class}">', *head]
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
