"""Backend selection without exposing credentials or touching local data."""

import os


def database_url():
    value = os.environ.get("DATABASE_URL")
    if value is None:
        import streamlit as st

        try:
            value = st.secrets.get("DATABASE_URL")
        except FileNotFoundError as exc:
            if exc.__cause__ is not None:
                raise ValueError(
                    "Não foi possível ler os Streamlit Secrets. Confira sua configuração."
                ) from None
            value = None
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("DATABASE_URL está vazia ou inválida. Confira os Secrets.")
    return value.strip()
