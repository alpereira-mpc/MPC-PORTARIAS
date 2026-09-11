"""Single source of truth for MPC-PB institutional chrome."""

from pathlib import Path

import streamlit as st

from services.ui_store import asset

ROOT = Path(__file__).resolve().parents[1]
SIDEBAR_LOGO = ROOT / "assets" / "mpcpb_logo_sidebar.png"
HEADER_IMAGE = ROOT / "assets" / "mpcpb_header_horizontal.png"
SIDEBAR_LOGO_WIDTH = 160
HEADER_WIDTH = 720


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
        "section[data-testid='stMain'] [data-testid='stHeading']{"
        "margin-top:.15rem;"
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
