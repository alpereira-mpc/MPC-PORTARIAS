"""Administrator-only page for one manual PDF summary.

The screen does not know the endpoint, the credential or the prompt.
"""

import logging

import streamlit as st

from services.access import require_permission
from services.ai_service import (
    MENSAGEM_NAO_CONFIGURADA,
    GeminiErro,
    GeminiNaoConfigurada,
    gemini_disponivel,
    resumir_documento_pdf,
)

LOGGER = logging.getLogger("mpc.ai")


def render(principal):
    require_permission(principal, "admin")
    st.subheader("Laboratório de IA")
    st.markdown(
        "Teste controlado de recursos de inteligência artificial. "
        "Utilize somente documentos fictícios ou autorizados nesta fase."
    )
    if not gemini_disponivel():
        st.warning(MENSAGEM_NAO_CONFIGURADA)
    uploaded = st.file_uploader(
        "Selecionar PDF para teste",
        type=["pdf"],
        key="ai_lab_pdf",
    )
    if uploaded is None:
        return
    st.text("Arquivo: " + uploaded.name)
    st.caption("Tamanho: " + str(uploaded.size) + " bytes")
    if not st.button("Gerar resumo com IA", key="ai_lab_gerar"):
        return
    if not gemini_disponivel():
        return
    try:
        with st.spinner("Analisando documento com IA..."):
            summary = resumir_documento_pdf(uploaded.getvalue())
    except GeminiNaoConfigurada as exc:
        st.warning(str(exc))
        return
    except GeminiErro as exc:
        st.error(str(exc))
        return
    except Exception as exc:
        LOGGER.error("Falha inesperada no laboratório de IA (%s).", type(exc).__name__)
        st.error("Não foi possível concluir a análise do documento.")
        return
    st.subheader("Resumo gerado por IA")
    st.markdown(summary)
    st.caption(
        "Conteúdo gerado por inteligência artificial. "
        "Confira as informações no documento original."
    )
