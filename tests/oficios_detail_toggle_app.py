"""AppTest helper: independent Abrir/Ocultar detalhes state per ofício."""

import streamlit as st

from services.oficios_ui import _oficio_detail_is_open, _toggle_oficio_detail

for oid in ("A", "B", "C"):
    is_open = _oficio_detail_is_open(oid)
    st.button(
        "Ocultar detalhes" if is_open else "Abrir detalhes",
        key="open_oficio_" + oid,
        on_click=_toggle_oficio_detail,
        args=(oid,),
    )
    if is_open:
        st.write("DETAIL_" + oid)
