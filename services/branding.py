"""Single source of truth for MPC-PB institutional chrome."""

import base64
from pathlib import Path

import streamlit as st

from services.ui_store import asset
from services.themes import theme_tokens

ROOT = Path(__file__).resolve().parents[1]
SIDEBAR_LOGO = ROOT / "assets" / "mpcpb_logo_sidebar_transparent.png"
HEADER_IMAGE = ROOT / "assets" / "mpcpb_header_horizontal_transparent.png"
SIDEBAR_LOGO_WIDTH = 160
HEADER_WIDTH = 720
APP_NAME = "Ferramentas MPC-PB"
APP_SHORT_SUBTITLE = "Portal Integrado de Gestão e Apoio Operacional"
# Same value as .streamlit/config.toml primaryColor — the institutional MPC-PB red.
BRAND_RED = theme_tokens("vermelho")["primary"]
APP_SUBTITLE = (
    APP_SHORT_SUBTITLE + " do Ministério Público de Contas da Paraíba"
)
MODULE_ICONS = {
    "portarias": "description",
    "memorandos": "article",
    "oficios": "mail",
    "agenda": "calendar_month",
    "tarefas": "check_circle",
    "relatorios": "bar_chart",
    "admin": "manage_accounts",
}


def module_title(module, title):
    """Format a heading with the same Material icon used by the Home card."""
    return f":material/{MODULE_ICONS[module]}: {title}"


def _brand_styles():
    st.markdown(
        "<style>"
        ".mpc-sidebar-brand{"
        "display:flex;justify-content:center;margin:.4rem 0 .9rem 0;"
        "background:var(--mpc-sidebar);"
        "}"
        ".mpc-sidebar-brand img{"
        "display:block;width:10rem;height:auto;object-fit:contain;"
        "background:transparent;"
        "}"
        "[data-testid='stMarkdown']:has(.mpc-institutional-header),"
        "[data-testid='stMarkdownContainer']:has(.mpc-institutional-header),"
        ".element-container:has(.mpc-institutional-header),"
        "div:has(> .mpc-institutional-header){"
        "overflow:visible !important;max-height:none !important;"
        "}"
        ".mpc-institutional-header,.mpc-home-institutional-header{"
        "display:block;margin:.15rem 0 1rem 0;padding:.2rem 0 .35rem;"
        "background:transparent;overflow:visible;line-height:0;"
        "}"
        ".mpc-home-institutional-header{"
        "margin:.1rem 0 1.15rem 0;"
        "}"
        ".mpc-institutional-header img,.mpc-home-institutional-header img{"
        "display:block;width:auto;max-width:min(56rem,100%);height:auto;"
        "max-height:9.5rem;object-fit:contain;object-position:left center;"
        "background:transparent;mix-blend-mode:multiply;"
        "}"
        "section[data-testid='stMain'] [data-testid='stMainBlockContainer'] "
        "> div > [data-testid='stImage']:first-child{"
        "margin:.15rem 0 1.1rem 0;background:transparent;overflow:visible;"
        "}"
        "section[data-testid='stMain'] [data-testid='stMainBlockContainer'] "
        "> div > [data-testid='stImage']:first-child img{"
        "display:block;width:auto;max-width:min(56rem,100%);height:auto;max-height:9.5rem;"
        "object-fit:contain;object-position:left center;"
        "margin:0;background:transparent;mix-blend-mode:multiply;"
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
        "margin:2.725rem 0 .95rem 0;"
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


def render_sidebar_brand(on_click=None):
    """Render the sidebar mark, optionally as an internal-navigation control."""
    _brand_styles()
    logo = base64.b64encode(asset(SIDEBAR_LOGO)).decode("ascii")
    if on_click is not None:
        st.markdown(
            "<style>"
            "section[data-testid='stSidebar'] [class*='st-key-sidebar_home']{"
            "display:block;width:100%;margin:.4rem 0 .9rem 0;"
            "background-color:var(--mpc-sidebar) !important;overflow:visible !important;"
            "}"
            "section[data-testid='stSidebar'] [class*='st-key-sidebar_home'] [data-testid='stButton']{"
            "background-color:var(--mpc-sidebar) !important;border:0 !important;box-shadow:none !important;"
            "}"
            "section[data-testid='stSidebar'] [class*='st-key-sidebar_home'] button,"
            "section[data-testid='stSidebar'] [class*='st-key-sidebar_home'] button:hover,"
            "section[data-testid='stSidebar'] [class*='st-key-sidebar_home'] button:hover:not(:disabled),"
            "section[data-testid='stSidebar'] [class*='st-key-sidebar_home'] button:active:not(:disabled),"
            "section[data-testid='stSidebar'] [class*='st-key-sidebar_home'] button:focus,"
            "section[data-testid='stSidebar'] [class*='st-key-sidebar_home'] button:focus-visible{"
            "display:block;width:10rem;max-width:100%;height:12rem;margin:0 auto;padding:0 !important;"
            "border:0 !important;box-shadow:none !important;background-color:var(--mpc-sidebar) !important;"
            "background-image:url('data:image/png;base64,"
            + logo
            + "') !important;background-size:contain !important;background-repeat:no-repeat !important;"
            "background-position:center !important;color:transparent !important;font-size:0 !important;"
            "cursor:pointer;opacity:1 !important;"
            "}"
            "section[data-testid='stSidebar'] [class*='st-key-sidebar_home'] button p{"
            "color:transparent !important;font-size:0 !important;"
            "}"
            "section[data-testid='stSidebar'] [class*='st-key-sidebar_home'] button:focus-visible{"
            "outline:2px solid currentColor;outline-offset:3px;"
            "}"
            "</style>",
            unsafe_allow_html=True,
        )
        st.button(
            "Início",
            key="sidebar_home",
            on_click=on_click,
        )
        return
    st.markdown(
        '<div class="mpc-sidebar-brand"><img src="data:image/png;base64,'
        + logo
        + '" alt="MPC-PB"></div>',
        unsafe_allow_html=True,
    )


def render_institutional_header(*, home=False):
    """Horizontal institutional banner at the top of the main pane."""
    _brand_styles()
    logo = base64.b64encode(asset(HEADER_IMAGE)).decode("ascii")
    klass = "mpc-institutional-header mpc-home-institutional-header" if home else "mpc-institutional-header"
    st.markdown(
        f'<div class="{klass}" style="background:var(--mpc-page,#F6F5F4)"><img src="data:image/png;base64,'
        + logo
        + '" alt="MPC-PB — Ministério Público de Contas do Estado da Paraíba"></div>',
        unsafe_allow_html=True,
    )


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
        f'<p class="mpc-identity-name">{APP_NAME if variant != "home" else "FERRAMENTAS MPC-PB - TESTE DE RECOVERY"}</p>',
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
