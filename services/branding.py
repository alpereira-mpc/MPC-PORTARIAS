"""Single source of truth for MPC-PB institutional chrome."""

from pathlib import Path

import streamlit as st

from services.ui_store import asset

ROOT = Path(__file__).resolve().parents[1]
SIDEBAR_LOGO = ROOT / "assets" / "mpcpb_logo_sidebar.png"
HEADER_IMAGE = ROOT / "assets" / "mpcpb_header_horizontal.png"
SIDEBAR_LOGO_WIDTH = 160
HEADER_WIDTH = 720
APP_NAME = "Ferramentas MPC-PB"
APP_SHORT_SUBTITLE = "Portal Integrado de Gestão e Apoio Operacional"
APP_SUBTITLE = (
    APP_SHORT_SUBTITLE + " do Ministério Público de Contas da Paraíba"
)


def _brand_styles():
    st.markdown(
        "<style>"
        "section[data-testid='stSidebar'] [data-testid='stImage']{"
        "display:flex;justify-content:center;"
        "}"
        "section[data-testid='stSidebar'] [data-testid='stImage'] img{"
        "margin:.4rem auto .9rem auto;height:auto;object-fit:contain;"
        "}"
        "section[data-testid='stMain'] [data-testid='stMainBlockContainer'] "
        "> div > [data-testid='stImage']:first-child{"
        "margin:0 0 1.1rem 0;"
        "}"
        "section[data-testid='stMain'] [data-testid='stMainBlockContainer'] "
        "> div > [data-testid='stImage']:first-child img{"
        "display:block;width:min(45rem,100%);max-width:100%;height:auto;"
        "margin:0;"
        "}"
        ".mpc-identity{"
        "max-width:100%;margin:.9rem 0 1rem 0;padding:0;text-align:left;"
        "}"
        ".mpc-identity--login{"
        "max-width:min(50rem,100%);margin:1.5rem 0 1.35rem 0;"
        "}"
        ".mpc-identity--login .mpc-identity-name{"
        "margin:0 0 .625rem 0;"
        "font-size:clamp(2rem,1.4rem + 2.5vw,3.125rem);"
        "font-weight:700;line-height:1.1;"
        "}"
        ".mpc-identity--login .mpc-identity-subtitle{"
        "max-width:100%;"
        "font-size:clamp(1.0625rem,.9rem + .55vw,1.375rem);"
        "font-weight:500;line-height:1.4;"
        "color:rgba(49,51,63,.9);"
        "}"
        ".mpc-identity--login .mpc-identity-action{"
        "margin:2.1rem 0 0 0;"
        "font-size:clamp(1.375rem,1.2rem + .5vw,1.75rem);"
        "font-weight:700;line-height:1.25;"
        "}"
        ".mpc-identity--home{"
        "margin:.85rem 0 .95rem 0;"
        "}"
        ".mpc-identity-name{"
        "margin:0 0 .65rem 0;"
        "font-size:clamp(1.75rem,1.15rem + 2.4vw,2.625rem);"
        "font-weight:700;line-height:1.15;"
        "overflow-wrap:break-word;"
        "}"
        ".mpc-identity--home .mpc-identity-name{"
        "font-size:clamp(1.75rem,1.2rem + 2vw,2.375rem);"
        "}"
        ".mpc-identity-subtitle{"
        "margin:0;max-width:42rem;"
        "font-size:clamp(1rem,.75rem + .9vw,1.25rem);"
        "font-weight:500;line-height:1.4;"
        "color:rgba(49,51,63,.88);"
        "overflow-wrap:break-word;"
        "}"
        ".mpc-identity-prompt{"
        "margin:.7rem 0 0 0;"
        "font-size:clamp(.875rem,.8rem + .3vw,1rem);"
        "line-height:1.4;font-weight:400;"
        "color:rgba(49,51,63,.72);"
        "}"
        ".mpc-identity-action{"
        "margin:1.85rem 0 0 0;"
        "font-size:clamp(1.25rem,1.05rem + .6vw,1.5rem);"
        "font-weight:650;line-height:1.25;"
        "}"
        "</style>",
        unsafe_allow_html=True,
    )


def render_sidebar_brand():
    """Sidebar mark matched to the navigation background. Call inside st.sidebar."""
    _brand_styles()
    st.image(asset(SIDEBAR_LOGO), width=SIDEBAR_LOGO_WIDTH)


def render_institutional_header():
    """Horizontal institutional banner at the top of the main pane."""
    _brand_styles()
    st.image(asset(HEADER_IMAGE), width=HEADER_WIDTH)


def render_app_identity(*, variant="presentation", prompt=None, action=None):
    """System identity under the organ header. Variants: login, home, presentation."""
    _brand_styles()
    short = variant == "home"
    subtitle = APP_SHORT_SUBTITLE if short else APP_SUBTITLE
    extra = ""
    if variant == "login":
        extra = " mpc-identity--login"
    elif variant == "home":
        extra = " mpc-identity--home"
    parts = [
        f'<div class="mpc-identity{extra}">',
        f'<p class="mpc-identity-name">{APP_NAME}</p>',
        f'<p class="mpc-identity-subtitle">{subtitle}</p>',
    ]
    if prompt:
        parts.append(f'<p class="mpc-identity-prompt">{prompt}</p>')
    if action:
        parts.append(f'<p class="mpc-identity-action">{action}</p>')
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_portal_identity(*, short=False, hero=False, prompt=None, action=None):
    if hero:
        variant = "login"
    elif short:
        variant = "home"
    else:
        variant = "presentation"
    render_app_identity(variant=variant, prompt=prompt, action=action)
