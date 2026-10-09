"""Streamlit UI for Representações. Follows existing portal visual patterns."""

from datetime import date
import hashlib
import logging

import streamlit as st

LOGGER = logging.getLogger("mpc.representacoes.ui")
AVISO_RESUMO_IA = (
    "Resumo gerado por inteligência artificial a partir do documento protocolado. "
    "Consulte o documento original para conferência."
)
AVISO_PDF_ALTERADO = (
    "O documento oficial foi alterado desde a geração deste resumo. "
    "Gere novamente para atualizar."
)

from services.access import has_permission, require_permission
from services.branding import module_title
from services.date_format import format_date_br
from services.representacoes import (
    ANDAMENTOS,
    DOCUMENTOS,
    FASES,
    ORIGENS,
    PRIORIDADES,
    RELATORES,
    SITUACOES,
    add_document,
    add_progress,
    andamento_display,
    assessores,
    can_delete,
    create,
    delete,
    delete_blocked_reason,
    exclude_progress,
    atualizar_resumo_representacao,
    documents,
    download,
    get,
    hash_documento,
    pdf_oficial,
    resumo_desatualizado,
    resumo_ia,
    grouped_members,
    formatar_resumo_markdown,
    is_protocolled,
    kind_label,
    kind_saved_message,
    label,
    list_records,
    overview,
    people_context,
    people_index,
    procuradores,
    progress,
    reconcile_signatories,
    signatory_options,
    register_protocol,
    register_direct_protocol,
    relator_label,
    set_phase,
    set_status,
    update,
)
from services.ui_store import display_store
from services.representacoes_ai import match_people, match_person, match_relator
from services.ui_theme import (
    actions_mark,
    badges,
    definition_block,
    empty_state,
    filter_mark,
    form_mark,
    html_text,
    render_record,
    section_label,
)


_FILTER_ALL = "__todas__"
_LIST_FILTER_KEYS = (
    "rep_f_sit",
    "rep_f_fase",
    "rep_f_year",
    "rep_f_q",
    "rep_f_rep",
    "rep_f_tema",
    "rep_f_rel",
    "rep_f_proc",
    "rep_f_procud",
    "rep_f_ass",
)


_ADVANCED_FILTER_KEYS = tuple(
    key for key in _LIST_FILTER_KEYS if key not in {"rep_f_sit", "rep_f_q"}
)


class _BadgeItem(tuple):
    """Two-value badge item with an optional semantic CSS modifier.

    Keeping the public tuple shape avoids changing callers that consume the
    existing ``(text, tone)`` convention.
    """

    def __new__(cls, text, tone, semantic_class):
        item = super().__new__(cls, (text, tone))
        item.semantic_class = semantic_class
        return item


# Visual-only classification kept beside the card presenter. Codes remain the
# authoritative values from services.representacoes and unknown future values
# deliberately use the neutral variant.
_SITUACAO_BADGE_VARIANTS = {
    "IDEIA": "active",
    "PESQUISA": "active",
    "ELABORACAO": "active",
    "MINUTA_REVISAO": "active",
    "APROVADA": "approved",
    "AGUARDANDO_PROTOCOLO": "waiting",
    "PROTOCOLADA": "active",
    "EM_TRAMITACAO": "active",
    "JULGADA": "complete",
    "ENCERRADA": "closed",
    "ARQUIVADA": "closed",
    "SUSPENSA": "waiting",
    "CANCELADA": "cancelled",
}

_FASE_BADGE_VARIANTS = {
    "INSTRUCAO": "instruction",
    "AGUARDANDO_DEFESA": "instruction",
    "DEFESA_APRESENTADA": "instruction",
    "ANALISE_DEFESA": "instruction",
    "MPC": "instruction",
    "PAUTA": "agenda",
    "JULGAMENTO": "judgment",
    "POS_JULGAMENTO": "post",
}


def _detail_chrome(prefix):
    st.markdown(
        "<style>"
        f'div[class*="st-key-{prefix}_detail"]{{'
        "background:transparent!important;"
        "background-color:transparent!important;"
        "border:none!important;"
        "box-shadow:none!important;"
        "}"
        f'div[class*="st-key-{prefix}_toolbar"] [data-testid="stHorizontalBlock"]{{'
        "justify-content:flex-start;align-items:center;gap:.45rem;flex-wrap:wrap;"
        "}"
        f'div[class*="st-key-{prefix}_toolbar"] [data-testid="stHorizontalBlock"]>div{{'
        "flex:0 1 auto!important;width:auto!important;min-width:0;"
        "}"
        f'div[class*="st-key-{prefix}_note"]{{'
        "background:var(--mpc-info-soft)!important;"
        "background-color:var(--mpc-info-soft)!important;"
        "border-left:3px solid var(--mpc-info)!important;"
        "}"
        "@media (max-width:768px){"
        f'div[class*="st-key-{prefix}_toolbar"] [data-testid="stHorizontalBlock"]{{'
        "flex-wrap:wrap;"
        "}"
        "}"
        "</style>",
        unsafe_allow_html=True,
    )


def _filter_select(label, mapping, empty, key):
    chosen = st.selectbox(
        label,
        [_FILTER_ALL, *mapping],
        format_func=lambda x, names=mapping, blank=empty: (
            blank if x == _FILTER_ALL else names.get(x, str(x))
        ),
        placeholder=empty,
        key=key,
    )
    return None if chosen == _FILTER_ALL else chosen


def _has_filter_value(value):
    """Return whether a saved filter actually restricts the listing."""
    if value in (None, "", _FILTER_ALL):
        return False
    return bool(str(value).strip())


def _advanced_filter_count():
    return sum(
        _has_filter_value(st.session_state.get(key)) for key in _ADVANCED_FILTER_KEYS
    )


def _clear_list_filters():
    """Clear list widgets through their callback, before they are recreated."""
    for key in _LIST_FILTER_KEYS:
        st.session_state.pop(key, None)


def _toolbar(prefix, items):
    shown = [item for item in items if item]
    if not shown:
        return None
    with st.container(key=prefix):
        actions_mark()
        columns = st.columns(len(shown))
        pressed = None
        for column, (title, key, kind) in zip(columns, shown):
            if column.button(title, key=key, type=kind or "secondary"):
                pressed = key
        return pressed


def _done(message):
    st.session_state["representacoes_message"] = message
    st.rerun(scope="fragment")


def _clear_widget_state(*prefixes):
    for key in tuple(st.session_state):
        if any(key.startswith(prefix) for prefix in prefixes):
            st.session_state.pop(key, None)


def _queue_widget_cleanup(*prefixes):
    pending = list(st.session_state.get("_representacoes_widget_cleanup") or ())
    st.session_state["_representacoes_widget_cleanup"] = [
        *pending,
        *(prefix for prefix in prefixes if prefix not in pending),
    ]


def _consume_widget_cleanup():
    prefixes = st.session_state.pop("_representacoes_widget_cleanup", ())
    _clear_widget_state(*prefixes)


def _set_ui_state(key, value, *reset_prefixes):
    _clear_widget_state(*reset_prefixes)
    st.session_state[key] = value


def _clear_ui_state(key, *reset_prefixes):
    st.session_state.pop(key, None)
    _clear_widget_state(*reset_prefixes)


def _open_representation(identifier):
    for key in (
        "representacoes_protocol",
        "representacoes_progress",
        "representacoes_file",
        "representacoes_edit",
        "representacoes_confirm_delete",
        "representacoes_exclude_progress",
        "representacoes_download",
    ):
        st.session_state.pop(key, None)
    st.session_state["rep_detail_section_" + str(identifier)] = "Visão geral"
    st.session_state["representacoes_view"] = identifier


def _return_to_listing():
    st.session_state.pop("representacoes_view", None)
    for key in (
        "representacoes_protocol",
        "representacoes_progress",
        "representacoes_file",
        "representacoes_edit",
        "representacoes_confirm_delete",
        "representacoes_exclude_progress",
        "representacoes_download",
    ):
        st.session_state.pop(key, None)
    for key, value in st.session_state.get("_representacoes_list_filters", {}).items():
        st.session_state[key] = value


def _clear_forms():
    for key in (
        "representacoes_edit",
        "representacoes_view",
        "representacoes_protocol",
        "representacoes_direct",
        "representacoes_progress",
        "representacoes_file",
        "representacoes_confirm_delete",
        "representacoes_exclude_progress",
        "representacoes_download",
    ):
        st.session_state.pop(key, None)


def _people_options(store):
    members = procuradores(store)
    servers = assessores(store)
    return (
        {row["id"]: row["nome"] for row in members},
        {row["id"]: row["nome"] for row in servers},
    )


_DIRECT_AI_TEXT_KEYS = {
    "rep_direct_base_titulo",
    "rep_direct_base_objeto",
    "rep_direct_base_representado",
    "rep_direct_base_tema",
    "rep_direct_base_obs",
    "rep_direct_prot_num",
    "rep_direct_prot_obs",
}
_DIRECT_AI_LIST_KEYS = {"rep_direct_base_sign", "rep_direct_base_ass"}


def _mark_direct_dirty(key):
    dirty = set(st.session_state.get("rep_direct_ai_dirty") or ())
    dirty.add(key)
    st.session_state["rep_direct_ai_dirty"] = dirty


def _direct_change_kwargs(prefix, suffix):
    if prefix not in ("rep_direct_base_", "rep_direct_prot_"):
        return {}
    return {"on_change": _mark_direct_dirty, "args": (prefix + suffix,)}


def _direct_pdf_hash(upload):
    return hashlib.sha256(upload.getvalue()).hexdigest() if upload is not None else None


def _sync_direct_pdf(upload):
    current_hash = _direct_pdf_hash(upload)
    previous_hash = st.session_state.get("rep_direct_ai_file")
    if previous_hash and previous_hash != current_hash:
        dirty = set(st.session_state.get("rep_direct_ai_dirty") or ())
        for key, entry in (st.session_state.get("rep_direct_ai_applied") or {}).items():
            if key in dirty or st.session_state.get(key) != entry["after"]:
                continue
            if entry["present"]:
                st.session_state[key] = entry["before"]
            else:
                st.session_state.pop(key, None)
        for key in (
            "rep_direct_ai_file",
            "rep_direct_ai_pending",
            "rep_direct_ai_applied",
            "rep_direct_ai_missing",
            "rep_direct_ai_notice",
            "rep_direct_ai_changed",
            "rep_direct_ai_cautelar_status",
        ):
            st.session_state.pop(key, None)
    return current_hash


def _preencher_representacao_com_ia(upload):
    st.session_state.pop("rep_direct_ai_notice", None)
    if upload is None:
        st.warning("Selecione o PDF final antes de solicitar a análise.")
        return
    content = upload.getvalue()
    try:
        from services.ai_service import extrair_dados_representacao_pdf

        with st.spinner("Analisando o PDF da Representação..."):
            result = extrair_dados_representacao_pdf(content)
    except RuntimeError as exc:
        st.error(str(exc))
        return
    except Exception:
        LOGGER.exception("Falha inesperada na extração da Representação.")
        st.error("Não foi possível analisar o PDF. Tente novamente.")
        return
    if not isinstance(result, dict):
        st.error("A IA não retornou dados válidos para o formulário.")
        return
    st.session_state["rep_direct_ai_file"] = hashlib.sha256(content).hexdigest()
    st.session_state["rep_direct_ai_pending"] = result


def _representacao_ai_suggestions(store, data):
    people, servers = _people_options(store)
    suggestions = {}
    text_fields = {
        "titulo": "rep_direct_base_titulo",
        "objeto": "rep_direct_base_objeto",
        "representado": "rep_direct_base_representado",
        "tema": "rep_direct_base_tema",
        "observacoes_internas": "rep_direct_base_obs",
        "numero_processo": "rep_direct_prot_num",
        "observacoes_protocolo": "rep_direct_prot_obs",
    }
    for source, target in text_fields.items():
        value = data.get(source)
        if isinstance(value, str) and value.strip():
            suggestions[target] = value.strip()
    choices = {
        "origem": ("rep_direct_base_origem", ORIGENS),
        "prioridade": ("rep_direct_base_prioridade", PRIORIDADES),
        "fase_processual": ("rep_direct_prot_fase", FASES),
    }
    for source, (target, allowed) in choices.items():
        value = data.get(source)
        if isinstance(value, str) and value in allowed:
            suggestions[target] = value
    for source, target in (
        ("data_abertura", "rep_direct_base_abertura"),
        ("data_protocolo", "rep_direct_prot_data"),
    ):
        try:
            suggestions[target] = date.fromisoformat(data[source])
        except (KeyError, TypeError, ValueError):
            pass
    responsible = match_person(data.get("procurador_responsavel"), people)
    dirty = set(st.session_state.get("rep_direct_ai_dirty") or ())
    manual_signers = st.session_state.get("rep_direct_base_sign") or ()
    if responsible is not None and not (
        "rep_direct_base_sign" in dirty and responsible in manual_signers
    ):
        suggestions["rep_direct_base_resp"] = responsible
    relator = match_relator(data.get("relator"), RELATORES)
    if relator is not None:
        suggestions["rep_direct_prot_rel"] = relator
    signer_ids = match_people(data.get("procuradores_signatarios"), people)
    current_responsible = st.session_state.get(
        "rep_direct_base_resp", next(iter(people), None)
    )
    selected_responsible = (
        current_responsible
        if "rep_direct_base_resp" in dirty
        else suggestions.get("rep_direct_base_resp", current_responsible)
    )
    signer_ids = reconcile_signatories(
        signer_ids, signatory_options(people, selected_responsible)
    )
    if signer_ids:
        suggestions["rep_direct_base_sign"] = signer_ids
    assessor_ids = match_people(data.get("assessores"), servers)
    if assessor_ids:
        suggestions["rep_direct_base_ass"] = assessor_ids
    cautelar = data.get("possui_medida_cautelar")
    if cautelar in ("SIM", "NAO"):
        suggestions["rep_direct_prot_cautelar"] = cautelar == "SIM"
    missing = [
        label
        for key, label in (
            ("titulo", "Título"),
            ("numero_processo", "Número do processo"),
            ("procurador_responsavel", "Procurador responsável"),
            ("relator", "Relator"),
        )
        if key not in data
        or not data.get(key)
        or (key == "procurador_responsavel" and responsible is None)
        or (key == "relator" and relator is None)
    ]
    return suggestions, missing


def _apply_representacao_ai_prefill(store, upload):
    current_hash = _direct_pdf_hash(upload)
    if st.session_state.get("rep_direct_ai_file") != current_hash:
        st.session_state.pop("rep_direct_ai_pending", None)
        return
    data = st.session_state.pop("rep_direct_ai_pending", None)
    if not isinstance(data, dict):
        return
    suggestions, missing = _representacao_ai_suggestions(store, data)
    dirty = set(st.session_state.get("rep_direct_ai_dirty") or ())
    applied = dict(st.session_state.get("rep_direct_ai_applied") or {})
    changed = 0
    for key, value in suggestions.items():
        if key in dirty:
            continue
        previous = applied.get(key)
        current = st.session_state.get(key)
        if (
            previous is None
            and key in _DIRECT_AI_TEXT_KEYS | _DIRECT_AI_LIST_KEYS
            and current
        ):
            continue
        if previous is not None and current != previous["after"]:
            continue
        if current == value:
            continue
        applied[key] = {
            "present": (
                previous["present"] if previous is not None else key in st.session_state
            ),
            "before": previous["before"] if previous is not None else current,
            "after": value,
        }
        st.session_state[key] = value
        changed += 1
    st.session_state["rep_direct_ai_applied"] = applied
    st.session_state["rep_direct_ai_missing"] = missing
    st.session_state["rep_direct_ai_cautelar_status"] = data.get(
        "possui_medida_cautelar", "NAO_IDENTIFICADO"
    )
    st.session_state["rep_direct_ai_changed"] = bool(changed)
    st.session_state["rep_direct_ai_notice"] = (
        "Campos sugeridos pela IA. Confira o documento antes de salvar."
        if changed
        else "Nenhum campo foi alterado pela IA. Confira as informações antes de salvar."
    )


def _form_layout():
    st.markdown(
        "<style>"
        "@media(max-width:768px){"
        "div[class*='st-key-rep_form_general'] [data-testid='stHorizontalBlock'],"
        "div[class*='st-key-rep_form_team'] [data-testid='stHorizontalBlock'],"
        "div[class*='st-key-rep_protocol_fields'] [data-testid='stHorizontalBlock'],"
        "div[class*='st-key-rep_form_actions'] [data-testid='stHorizontalBlock']"
        "{flex-direction:column!important;align-items:stretch!important}"
        "div[class*='st-key-rep_form_general'] [data-testid='stHorizontalBlock']>div,"
        "div[class*='st-key-rep_form_team'] [data-testid='stHorizontalBlock']>div,"
        "div[class*='st-key-rep_protocol_fields'] [data-testid='stHorizontalBlock']>div,"
        "div[class*='st-key-rep_form_actions'] [data-testid='stHorizontalBlock']>div"
        "{width:100%!important;flex:1 1 auto!important;min-width:0!important}"
        "}"
        "</style>",
        unsafe_allow_html=True,
    )


def _form(store, current=None, *, prefix=None):
    prefix = prefix or "rep_form_" + str((current or {}).get("id") or "new")
    current = current or {}
    people, servers = _people_options(store)
    if not people:
        st.warning("Cadastre um Procurador ativo na base institucional.")
        return
    form_mark()
    _form_layout()
    section_label("INFORMAÇÕES GERAIS")
    title = st.text_input(
        "Título *",
        value=current.get("titulo") or "",
        key=prefix + "titulo",
        **_direct_change_kwargs(prefix, "titulo"),
    )
    objeto = st.text_area(
        "Objeto/resumo",
        value=current.get("objeto") or "",
        key=prefix + "objeto",
        **_direct_change_kwargs(prefix, "objeto"),
    )
    with st.container(key="rep_form_general"):
        left, right = st.columns(2)
        origem_keys = list(ORIGENS)
        origem = left.selectbox(
            "Origem",
            origem_keys,
            index=origem_keys.index(current.get("origem") or "DE_OFICIO"),
            format_func=ORIGENS.get,
            key=prefix + "origem",
            **_direct_change_kwargs(prefix, "origem"),
        )
        opening = right.date_input(
            "Data de abertura",
            (
                date.fromisoformat(current["data_abertura"])
                if current.get("data_abertura")
                else date.today()
            ),
            format="DD/MM/YYYY",
            key=prefix + "abertura",
            **_direct_change_kwargs(prefix, "abertura"),
        )
        representado_col, tema_col = st.columns([1.85, 1])
        representado = representado_col.text_input(
            "Representado",
            value=current.get("representado") or "",
            key=prefix + "representado",
            **_direct_change_kwargs(prefix, "representado"),
        )
        tema = tema_col.text_input(
            "Tema/área",
            value=current.get("tema") or "",
            key=prefix + "tema",
            **_direct_change_kwargs(prefix, "tema"),
        )
    existing = current.get("integrantes") or []
    responsible = next(
        (
            item["membro_id"]
            for item in existing
            if item["papel"] == "PROCURADOR_RESPONSAVEL"
        ),
        next(iter(people), None),
    )
    signatories = [
        item["membro_id"]
        for item in existing
        if item["papel"] == "PROCURADOR_SIGNATARIO"
    ]
    helpers = [item["membro_id"] for item in existing if item["papel"] == "ASSESSOR"]
    section_label("EQUIPE E GESTÃO")
    with st.container(key="rep_form_team"):
        responsavel_col, prioridade_col = st.columns([1.85, 1])
        procurador = responsavel_col.selectbox(
            "Procurador responsável *",
            list(people),
            index=list(people).index(responsible) if responsible in people else 0,
            format_func=people.get,
            key=prefix + "resp",
            **_direct_change_kwargs(prefix, "resp"),
        )
        prioridade_keys = list(PRIORIDADES)
        prioridade = prioridade_col.selectbox(
            "Prioridade",
            prioridade_keys,
            index=prioridade_keys.index(current.get("prioridade") or "NORMAL"),
            format_func=PRIORIDADES.get,
            key=prefix + "prioridade",
            **_direct_change_kwargs(prefix, "prioridade"),
        )
        sign_key = prefix + "sign"
        allowed_signatories = signatory_options(people, procurador)
        if sign_key in st.session_state:
            st.session_state[sign_key] = reconcile_signatories(
                st.session_state[sign_key], allowed_signatories
            )
            default_signatories = st.session_state[sign_key]
        else:
            default_signatories = reconcile_signatories(
                signatories, allowed_signatories
            )
        signatarios_col, assessores_col = st.columns(2)
        signatarios = signatarios_col.multiselect(
            "Procuradores signatários",
            allowed_signatories,
            default=default_signatories,
            format_func=people.get,
            key=sign_key,
            **_direct_change_kwargs(prefix, "sign"),
        )
        assessor_ids = assessores_col.multiselect(
            "Assessores",
            list(servers),
            default=[item for item in helpers if item in servers],
            format_func=servers.get,
            key=prefix + "ass",
            **_direct_change_kwargs(prefix, "ass"),
        )
        if current:
            situacao_keys = list(SITUACOES)
            situacao = st.selectbox(
                "Situação",
                situacao_keys,
                index=situacao_keys.index(current.get("situacao") or "IDEIA"),
                format_func=SITUACOES.get,
                key=prefix + "sit",
            )
        else:
            situacao = "IDEIA"
    observacoes = st.text_area(
        "Observações internas",
        value=current.get("observacoes") or "",
        key=prefix + "obs",
        **_direct_change_kwargs(prefix, "obs"),
    )
    return {
        "titulo": title,
        "objeto": objeto,
        "origem": origem,
        "data_abertura": opening.isoformat(),
        "representado": representado,
        "tema": tema,
        "prioridade": prioridade,
        "observacoes": observacoes,
        "procurador_responsavel": procurador,
        "procuradores_signatarios": signatarios,
        "assessores": assessor_ids,
        "situacao": situacao,
    }


def _listing_chrome():
    """Keep the home view compact without styling other portal modules."""
    st.markdown(
        """
        <style>
        section[data-testid="stMain"] .st-key-rep_kpis [data-testid="stMetric"] {
            min-height:5.1rem;
            padding:.62rem .72rem .56rem;
            background:var(--mpc-card-b)!important;
            border:1px solid var(--mpc-card-border-b)!important;
            border-radius:8px;
            box-shadow:none!important;
        }
        section[data-testid="stMain"] .st-key-rep_kpis [data-testid="stMetricLabel"] {
            color:var(--mpc-text-2)!important;
            font-size:.74rem!important;
            letter-spacing:.025em;
            text-transform:none;
        }
        section[data-testid="stMain"] .st-key-rep_kpis [data-testid="stMetricValue"] {
            color:var(--mpc-text)!important;
            font-size:1.55rem!important;
            line-height:1.1;
        }
        section[data-testid="stMain"] .st-key-rep_home_actions [data-testid="stHorizontalBlock"],
        section[data-testid="stMain"] [class*="st-key-rep_card_actions_"] [data-testid="stHorizontalBlock"] {
            justify-content:flex-start;
            align-items:center;
            gap:.45rem;
            flex-wrap:wrap;
        }
        section[data-testid="stMain"] .st-key-rep_home_actions [data-testid="stHorizontalBlock"]>div,
        section[data-testid="stMain"] [class*="st-key-rep_card_actions_"] [data-testid="stHorizontalBlock"]>div {
            flex:0 1 auto!important;
            width:auto!important;
            min-width:0;
        }
        section[data-testid="stMain"] .st-key-rep_home_actions button,
        section[data-testid="stMain"] [class*="st-key-rep_card_actions_"] button {
            min-height:2.35rem;
            padding-inline:.78rem;
        }
        section[data-testid="stMain"] .st-key-rep_filters [data-testid="stVerticalBlockBorderWrapper"] {
            padding:.16rem .72rem .62rem;
            box-shadow:none!important;
        }
        section[data-testid="stMain"] .st-key-rep_filters [data-testid="stExpander"] {
            margin-top:.08rem;
            border:0;
            background:transparent!important;
        }
        section[data-testid="stMain"] .st-key-rep_filters [data-testid="stExpanderDetails"] {
            padding-top:.35rem;
        }
        section[data-testid="stMain"] [class*="st-key-rep_list_"] [data-testid="stVerticalBlockBorderWrapper"] {
            background:var(--mpc-card-b)!important;
            border:1px solid var(--mpc-card-border-b)!important;
            border-radius:8px;
            box-shadow:none!important;
        }
        section[data-testid="stMain"] [class*="st-key-rep_list_"] .mpc-record-boxed {
            padding:0!important;
            background:transparent!important;
            border:0!important;
            box-shadow:none!important;
        }
        section[data-testid="stMain"] [class*="st-key-rep_list_"] .mpc-record-title {
            margin:0;
            font-size:1.02rem;
            line-height:1.35;
        }
        section[data-testid="stMain"] .rep-card-metadata {
            display:grid;
            grid-template-columns:repeat(3,minmax(0,1fr));
            gap:.38rem .9rem;
            margin:.55rem 0 .1rem;
        }
        section[data-testid="stMain"] .rep-card-meta-item { min-width:0; }
        section[data-testid="stMain"] .rep-card-meta-label {
            display:block;
            color:var(--mpc-text-3);
            font-size:.68rem;
            font-weight:700;
            letter-spacing:.035em;
            line-height:1.25;
            text-transform:uppercase;
        }
        section[data-testid="stMain"] .rep-card-meta-value {
            display:block;
            margin-top:.08rem;
            color:var(--mpc-text-2);
            font-size:.82rem;
            line-height:1.35;
            overflow-wrap:anywhere;
        }
        section[data-testid="stMain"] .rep-card-latest {
            margin:.52rem 0 0;
            color:var(--mpc-text-3);
            font-size:.78rem;
            line-height:1.35;
        }
        @media(max-width:768px) {
            section[data-testid="stMain"] .st-key-rep_kpis [data-testid="stHorizontalBlock"] {
                display:grid;
                grid-template-columns:repeat(2,minmax(0,1fr));
                gap:.45rem;
            }
            section[data-testid="stMain"] .st-key-rep_kpis [data-testid="stHorizontalBlock"]>div {
                width:auto!important;
                min-width:0;
            }
            section[data-testid="stMain"] .st-key-rep_home_actions [data-testid="stHorizontalBlock"]>div,
            section[data-testid="stMain"] [class*="st-key-rep_card_actions_"] [data-testid="stHorizontalBlock"]>div {
                flex:1 1 8.5rem!important;
            }
            section[data-testid="stMain"] .rep-card-metadata {
                grid-template-columns:repeat(2,minmax(0,1fr));
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _kpis(counts):
    items = (
        ("Em preparação", counts["preparacao"]),
        ("Aguardando protocolo", counts["aguardando_protocolo"]),
        ("Em tramitação", counts["tramitacao"]),
        ("Julgadas", counts["julgadas"]),
    )
    with st.container(key="rep_kpis"):
        columns = st.columns(len(items))
        for column, (title, value) in zip(columns, items):
            with column:
                st.metric(title, value)


def _filters():
    active_advanced = _advanced_filter_count()
    advanced_label = "Filtros avançados"
    if active_advanced:
        advanced_label += f" · Filtros ativos ({active_advanced})"
    has_filters = any(
        _has_filter_value(st.session_state.get(key)) for key in _LIST_FILTER_KEYS
    )
    with st.container(key="rep_filters"):
        filter_mark()
        search_col, situation_col, clear_col = st.columns([1.8, 1, 0.7])
        with search_col:
            pesquisa = st.text_input(
                "Pesquisa",
                placeholder="Título, processo ou palavra-chave",
                key="rep_f_q",
            )
        with situation_col:
            situacao = _filter_select("Situação", SITUACOES, "Todas", "rep_f_sit")
        with clear_col:
            st.write("")
            st.button(
                "Limpar filtros",
                key="rep_filters_clear",
                disabled=not has_filters,
                on_click=_clear_list_filters,
            )
        with st.expander(advanced_label, expanded=False):
            phase_col, year_col, represented_col = st.columns(3)
            with phase_col:
                fase = _filter_select("Fase processual", FASES, "Todas", "rep_f_fase")
            year_options = {
                year: str(year) for year in range(date.today().year, 2023, -1)
            }
            with year_col:
                year = _filter_select("Ano", year_options, "Todos", "rep_f_year")
            with represented_col:
                representado = st.text_input("Representado", key="rep_f_rep")
            theme_col, rapporteur_col, process_col = st.columns(3)
            with theme_col:
                tema = st.text_input("Tema", key="rep_f_tema")
            with rapporteur_col:
                relator = st.text_input("Relator", key="rep_f_rel")
            with process_col:
                processo = st.text_input("Nº do processo", key="rep_f_proc")
            people, servers = st.session_state.get("_rep_people") or ({}, {})
            prosecutor_col, advisor_col = st.columns(2)
            with prosecutor_col:
                procurador = _filter_select(
                    "Procurador responsável", people, "Todos", "rep_f_procud"
                )
            with advisor_col:
                assessor = _filter_select("Assessor", servers, "Todos", "rep_f_ass")
    return {
        "situacao": situacao,
        "fase_processual": fase,
        "representado": representado,
        "tema": tema,
        "relator": relator,
        "numero_processo": processo,
        "pesquisa": pesquisa,
        "ano": year,
        "procurador_id": procurador,
        "assessor_id": assessor,
    }


def _badge_items(record):
    situacao = record.get("situacao")
    chips = [
        _BadgeItem(kind_label(record), "neutral", "rep-type"),
        _BadgeItem(
            label(SITUACOES, situacao),
            "neutral",
            "rep-status rep-status-"
            + _SITUACAO_BADGE_VARIANTS.get(situacao, "unknown"),
        ),
    ]
    if record.get("numero_processo"):
        chips.insert(
            1, _BadgeItem(record["numero_processo"], "neutral", "rep-identifier")
        )
    if is_protocolled(record) and record.get("fase_processual"):
        fase = record["fase_processual"]
        chips.append(
            _BadgeItem(
                label(FASES, fase),
                "neutral",
                "rep-phase rep-phase-" + _FASE_BADGE_VARIANTS.get(fase, "unknown"),
            )
        )
    return chips


def _metadata_html(record, groups, last):
    """Render the listing facts without putting labels into a dense paragraph."""
    procurador = ", ".join(groups["PROCURADOR_RESPONSAVEL"]) or "—"
    signatarios = ", ".join(groups["PROCURADOR_SIGNATARIO"]) or "—"
    assessores = ", ".join(groups["ASSESSOR"]) or "—"
    details = [
        ("Representado", record.get("representado") or "—"),
        ("Procurador responsável", procurador),
        ("Procuradores signatários", signatarios),
        ("Assessor", assessores),
        ("Relator", record.get("relator") or "—"),
    ]
    if record.get("data_protocolo"):
        details.append(
            ("Protocolo", format_date_br(record.get("data_protocolo"), empty="—"))
        )
    items = "".join(
        '<div class="rep-card-meta-item">'
        f'<span class="rep-card-meta-label">{html_text(title)}</span>'
        f'<span class="rep-card-meta-value">{html_text(value)}</span>'
        "</div>"
        for title, value in details
    )
    latest = ""
    if last:
        latest = (
            '<p class="rep-card-latest"><strong>Último andamento:</strong> '
            + html_text(label(ANDAMENTOS, last.get("tipo"), last.get("tipo")))
            + " · "
            + html_text(format_date_br(last.get("data"), empty="—"))
            + "</p>"
        )
    return '<div class="rep-card-metadata">' + items + "</div>" + latest


def _card(record, index, procuradores_map, assessores_map):
    groups = grouped_members(record, procuradores_map, assessores_map)
    with st.container(border=True, key="rep_list_" + str(record["id"])):
        render_record(
            record["titulo"],
            badges_html=badges(*_badge_items(record)),
            extra=_metadata_html(record, groups, record.get("ultimo_andamento") or {}),
            boxed=True,
        )
        with st.container(
            key="rep_card_actions_" + str(record["id"]),
            horizontal=True,
            horizontal_alignment="left",
        ):
            st.button(
                "Abrir",
                type="primary",
                key="rep_open_" + str(record["id"]),
                on_click=_open_representation,
                args=(record["id"],),
            )
            st.button(
                "Andamento",
                key="rep_prg_" + str(record["id"]),
                on_click=_set_ui_state,
                args=(
                    "representacoes_progress",
                    record["id"],
                    "rep_prg_form_" + str(record["id"]),
                ),
            )
            st.button(
                "Anexar",
                key="rep_doc_" + str(record["id"]),
                on_click=_set_ui_state,
                args=(
                    "representacoes_file",
                    record["id"],
                    "rep_doc_form_" + str(record["id"]),
                ),
            )
            st.button(
                "Editar",
                key="rep_ed_" + str(record["id"]),
                on_click=_set_ui_state,
                args=(
                    "representacoes_edit",
                    record["id"],
                    "rep_form_" + str(record["id"]),
                ),
            )


def _progress_form(store, principal, identifier):
    section_label("Novo andamento")
    prefix = "rep_prg_form_" + str(identifier)
    tipo = st.selectbox(
        "Tipo",
        list(ANDAMENTOS),
        format_func=ANDAMENTOS.get,
        key=prefix + "tipo",
    )
    day = st.date_input("Data", date.today(), format="DD/MM/YYYY", key=prefix + "data")
    descricao = st.text_area("Descrição", key=prefix + "desc")
    if st.button("Registrar andamento", type="primary", key=prefix + "ok"):
        try:
            add_progress(
                store,
                identifier,
                {"tipo": tipo, "data": day.isoformat(), "descricao": descricao},
                principal,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("representacoes_progress", None)
            _queue_widget_cleanup(prefix)
            _done("Andamento registrado.")
    st.button(
        "Cancelar",
        key=prefix + "cancel",
        on_click=_clear_ui_state,
        args=("representacoes_progress", prefix),
    )


def _document_form(store, principal, identifier):
    section_label("Anexar documento")
    prefix = "rep_doc_form_" + str(identifier)
    tipo = st.selectbox(
        "Tipo",
        list(DOCUMENTOS),
        format_func=DOCUMENTOS.get,
        key=prefix + "tipo",
    )
    day = st.date_input(
        "Data do documento", date.today(), format="DD/MM/YYYY", key=prefix + "data"
    )
    descricao = st.text_input("Descrição", key=prefix + "desc")
    uploaded = st.file_uploader(
        "Arquivo (PDF ou DOCX, até 10 MB)", type=["pdf", "docx"], key=prefix + "file"
    )
    if st.button("Anexar", type="primary", key=prefix + "ok"):
        if uploaded is None:
            st.error("Selecione um arquivo.")
            return
        try:
            add_document(
                store,
                identifier,
                {
                    "tipo_documento": tipo,
                    "descricao": descricao,
                    "data_documento": day.isoformat(),
                },
                uploaded.name,
                uploaded.getvalue(),
                principal,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("representacoes_file", None)
            _queue_widget_cleanup(prefix)
            _done("Documento anexado.")
    st.button(
        "Cancelar",
        key=prefix + "cancel",
        on_click=_clear_ui_state,
        args=("representacoes_file", prefix),
    )


def _protocol_fields(prefix, *, upload=None, show_upload=True):
    with st.container(key="rep_protocol_fields"):
        number_col, day_col = st.columns([1.6, 1])
        number = number_col.text_input(
            "Número do processo *",
            key=prefix + "num",
            **_direct_change_kwargs(prefix, "num"),
        )
        day = day_col.date_input(
            "Data do protocolo",
            date.today(),
            format="DD/MM/YYYY",
            key=prefix + "data",
            **_direct_change_kwargs(prefix, "data"),
        )
        relator_col, fase_col = st.columns(2)
        relator = relator_col.selectbox(
            "Relator *",
            [_FILTER_ALL, *RELATORES],
            format_func=lambda name: (
                "Selecione o Relator atribuído no TRAMITA"
                if name == _FILTER_ALL
                else relator_label(name)
            ),
            placeholder="Selecione o Relator atribuído no TRAMITA",
            key=prefix + "rel",
            **_direct_change_kwargs(prefix, "rel"),
        )
        fase = fase_col.selectbox(
            "Fase processual inicial",
            list(FASES),
            format_func=FASES.get,
            key=prefix + "fase",
            **_direct_change_kwargs(prefix, "fase"),
        )
        cautelar = st.radio(
            "Possui pedido de medida cautelar?",
            (False, True),
            index=0,
            format_func=lambda value: "Sim" if value else "Não",
            horizontal=True,
            key=prefix + "cautelar",
            **_direct_change_kwargs(prefix, "cautelar"),
        )
        if show_upload:
            upload = st.file_uploader(
                "PDF final da Representação", type=["pdf"], key=prefix + "pdf"
            )
    observacoes = st.text_area(
        "Observações", key=prefix + "obs", **_direct_change_kwargs(prefix, "obs")
    )
    return {
        "numero_processo": number,
        "data_protocolo": day.isoformat(),
        "relator": None if relator == _FILTER_ALL else relator,
        "fase_processual": fase,
        "observacoes": observacoes,
        "possui_medida_cautelar": cautelar,
    }, ((upload.name, upload.getvalue()) if upload is not None else None)


def _protocol_form(store, principal, identifier):
    if not has_permission(principal, "representacoes_registrar_protocolo"):
        st.error("Acesso não autorizado para registrar protocolo.")
        return
    record = get(store, identifier)
    if record is None:
        st.error("Representação não encontrada.")
        return
    if is_protocolled(record):
        st.error("Esta representação já possui protocolo.")
        return
    section_label("Projeto de Representação")
    definition_block(
        "Dados que serão preservados",
        [
            ("Título", record.get("titulo")),
            ("Objeto/resumo", record.get("objeto")),
            ("Origem", label(ORIGENS, record.get("origem"))),
            ("Representado", record.get("representado")),
            ("Tema/área", record.get("tema")),
        ],
    )
    st.caption(
        "O protocolo será registrado neste mesmo projeto; equipe, documentos, andamentos e vínculos existentes serão mantidos."
    )
    section_label("Registrar protocolo")
    st.caption(
        "Informe os dados atribuídos pelo TRAMITA. Este projeto passa a ser tratado como Representação. "
        "Este sistema não realiza distribuição de Relator."
    )
    prefix = "rep_prot_" + str(identifier)
    values, upload = _protocol_fields(prefix)
    if st.button("Confirmar protocolo", type="primary", key=prefix + "ok"):
        try:
            register_protocol(store, identifier, values, principal, upload)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("representacoes_protocol", None)
            _queue_widget_cleanup(prefix)
            _done("Representação protocolada.")
    st.button(
        "Cancelar",
        key=prefix + "cancel",
        on_click=_clear_ui_state,
        args=("representacoes_protocol", prefix),
    )


def _direct_protocol_form(store, principal):
    st.subheader("Registrar Representação Protocolada")
    st.caption(
        "Cadastre a Representação já protocolada no TRAMITA e anexe o PDF final."
    )
    upload = st.file_uploader(
        "PDF final da Representação", type=["pdf"], key="rep_direct_prot_pdf"
    )
    _sync_direct_pdf(upload)
    if st.button("✨ Preencher com IA", key="rep_direct_ai_fill"):
        _preencher_representacao_com_ia(upload)
    _apply_representacao_ai_prefill(store, upload)
    if notice := st.session_state.get("rep_direct_ai_notice"):
        if st.session_state.get("rep_direct_ai_changed"):
            st.success(notice)
        else:
            st.info(notice)
    if missing := st.session_state.get("rep_direct_ai_missing"):
        st.caption(
            "A IA não identificou com segurança: "
            + ", ".join(missing)
            + ". Confira esses campos obrigatórios."
        )
    if st.session_state.get("rep_direct_ai_cautelar_status") == "NAO_IDENTIFICADO":
        st.caption(
            "A IA não identificou se há pedido de medida cautelar. "
            "Confira essa opção antes de salvar."
        )
    base = _form(store, prefix="rep_direct_base_")
    if base is None:
        st.button(
            "Cancelar",
            key="rep_direct_cancel",
            on_click=_clear_ui_state,
            args=(
                "representacoes_direct",
                "rep_direct_base_",
                "rep_direct_prot_",
                "rep_direct_ai_",
            ),
        )
        return
    section_label("DADOS DO PROTOCOLO")
    protocol, upload_tuple = _protocol_fields(
        "rep_direct_prot_", upload=upload, show_upload=False
    )
    with st.container(
        key="rep_form_actions", horizontal=True, horizontal_alignment="right"
    ):
        if st.button("Salvar", type="primary", key="rep_direct_save"):
            try:
                saved = register_direct_protocol(
                    store, base, protocol, principal, upload_tuple
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state.pop("representacoes_direct", None)
                _queue_widget_cleanup(
                    "rep_direct_base_", "rep_direct_prot_", "rep_direct_ai_"
                )
                st.session_state["representacoes_view"] = saved["id"]
                _done("Representação protocolada.")
        st.button(
            "Cancelar",
            key="rep_direct_cancel",
            on_click=_clear_ui_state,
            args=(
                "representacoes_direct",
                "rep_direct_base_",
                "rep_direct_prot_",
                "rep_direct_ai_",
            ),
        )


def _detail(store, principal, record):
    _detail_chrome("rep")
    with st.container(key="rep_detail"):
        _detail_compact(store, principal, record)


def _detail_summary(store, record):
    procuradores_map, assessores_map = people_index(store)
    groups = grouped_members(record, procuradores_map, assessores_map)
    render_record(
        record["titulo"], badges_html=badges(*_badge_items(record)), boxed=True
    )
    summary = [
        ("Representado", record.get("representado")),
        ("Situação", label(SITUACOES, record.get("situacao"))),
        ("Fase processual", label(FASES, record.get("fase_processual"))),
        ("Processo", record.get("numero_processo")),
        (
            "Relator",
            relator_label(record.get("relator")) if record.get("relator") else None,
        ),
        ("Responsável MPC", ", ".join(groups["PROCURADOR_RESPONSAVEL"]) or "—"),
    ]
    definition_block("Situação atual", summary)
    return groups


def _render_progress_item(store, principal, record, item, pending):
    heading, descricao = andamento_display(item)
    shown = format_date_br(item["data"])
    line = f"**{shown}** · {heading}"
    if descricao:
        line += f"  \n{descricao}"
    if pending == item["id"]:
        st.markdown(line)
        st.warning("Confirma a exclusão deste andamento?")
        st.caption(f"{shown} · {heading}")
        if descricao:
            st.write(descricao)
        motivo = st.text_input(
            "Motivo da exclusão",
            key=f"rep_exc_motivo_{item['id']}",
            placeholder="Lançamento realizado por engano.",
        )
        confirm, cancel = st.columns(2)
        if confirm.button(
            "Confirmar exclusão",
            type="primary",
            key=f"rep_exc_yes_{item['id']}",
        ):
            if not str(motivo or "").strip():
                st.error("Informe o motivo da exclusão.")
            else:
                try:
                    exclude_progress(store, record["id"], item["id"], motivo, principal)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.pop("representacoes_exclude_progress", None)
                    _done("Andamento excluído.")
        cancel.button(
            "Cancelar",
            key=f"rep_exc_no_{item['id']}",
            on_click=_clear_ui_state,
            args=("representacoes_exclude_progress", f"rep_exc_motivo_{item['id']}"),
        )
        return
    text, action = st.columns([5, 1.5])
    text.markdown(line)
    action.button(
        "Excluir andamento",
        key=f"rep_exc_{item['id']}",
        on_click=_set_ui_state,
        args=("representacoes_exclude_progress", item["id"]),
    )


def _official_sha256(store, official):
    token = (official["id"], official.get("tamanho"), official.get("criado_em"))
    cached = st.session_state.get("_rep_pdf_sha")
    if isinstance(cached, dict) and cached.get("token") == token:
        return cached["sha256"]
    digest = hash_documento(store, official["id"])
    st.session_state["_rep_pdf_sha"] = {"token": token, "sha256": digest}
    return digest


def _official_download_payload(store, principal, record, official):
    """Audit and return the stored protocol PDF. The file is not copied."""
    from services.audit import registrar_download

    registrar_download(
        store,
        modulo="representacoes",
        entidade_tipo="representacao",
        entidade_id=record["id"],
        arquivo=official.get("nome_arquivo"),
        formato=official.get("mime_type"),
        rotulo=record.get("titulo"),
        principal=principal,
    )
    return download(store, official["id"])


def _render_official_document(store, principal, record):
    """Shortcut for the protocol PDF already stored with the representation."""
    official = pdf_oficial(store, record["id"])
    if official is None:
        return
    saved = resumo_ia(store, record["id"])
    hidden_key = "representacoes_resumo_ia_oculto_" + str(record["id"])
    has_summary = bool(saved and str(saved.get("texto") or "").strip())
    hidden = bool(st.session_state.get(hidden_key))
    download_column, summary_column, hide_column = st.columns([2, 1.45, 1.25])

    def _read_official_pdf():
        return _official_download_payload(store, principal, record, official)[
            "conteudo"
        ]

    # Streamlit runs the callable on click and does not rerun the page.
    # The Documentos tab keeps its own prepare-then-download flow.
    download_column.download_button(
        "Baixar representação",
        data=_read_official_pdf,
        file_name=official.get("nome_arquivo") or "representacao.pdf",
        mime=official.get("mime_type") or "application/pdf",
        key="rep_visao_dl_" + official["id"],
        on_click="ignore",
    )
    summary_label = "↻ Atualizar resumo" if has_summary else "✨ Gerar resumo com IA"
    update_requested = False
    if has_summary and hidden:
        summary_column.button(
            "Exibir último resumo",
            key="rep_visao_resumo_show_" + str(record["id"]),
            on_click=_clear_ui_state,
            args=(hidden_key,),
        )
    else:
        update_requested = summary_column.button(
            summary_label, key="rep_visao_resumo_" + str(record["id"])
        )
        if has_summary:
            hide_column.button(
                "Ocultar resumo",
                key="rep_visao_resumo_hide_" + str(record["id"]),
                on_click=_set_ui_state,
                args=(hidden_key, True),
            )
    if update_requested:
        from services.ai_service import GeminiErro, GeminiNaoConfigurada

        try:
            with st.spinner("Gerando resumo da representação..."):
                saved = atualizar_resumo_representacao(store, record["id"], principal)
        except GeminiNaoConfigurada as exc:
            st.warning(str(exc))
        except GeminiErro as exc:
            st.error(str(exc))
        except ValueError as exc:
            st.error(str(exc))
        except Exception as exc:
            LOGGER.error(
                "Falha ao concluir o resumo da representação (%s).",
                type(exc).__name__,
            )
            if has_summary:
                st.error(
                    "Não foi possível salvar o resumo. O conteúdo anterior foi mantido."
                )
            else:
                st.error("Não foi possível concluir o resumo da representação.")
        else:
            st.session_state["_rep_pdf_sha"] = {
                "token": (
                    official["id"],
                    official.get("tamanho"),
                    official.get("criado_em"),
                ),
                "sha256": saved.get("sha256"),
            }
            st.session_state.pop(hidden_key, None)
    if not saved or not str(saved.get("texto") or "").strip():
        return
    try:
        current_sha = _official_sha256(store, official)
    except ValueError:
        current_sha = None
    if resumo_desatualizado(saved, current_sha):
        st.caption(AVISO_PDF_ALTERADO)
    if not hidden:
        st.markdown("### Resumo da representação — gerado por IA")
        st.markdown(formatar_resumo_markdown(saved["texto"]))
        st.caption(AVISO_RESUMO_IA)


def _detail_compact(store, principal, record):
    groups = _detail_summary(store, record)
    protocolled = is_protocolled(record)
    sections = ["Visão geral", "Andamentos", "Documentos", "Organização", "Gestão"]
    section = st.radio(
        "Seção da Representação",
        sections,
        horizontal=True,
        key="rep_detail_section_" + str(record["id"]),
        label_visibility="collapsed",
    )
    if section == "Visão geral":
        definition_block(
            "Identificação",
            (
                ("Objeto", record.get("objeto")),
                ("Origem", label(ORIGENS, record["origem"])),
                ("Abertura", format_date_br(record.get("data_abertura"))),
                ("Representado", record.get("representado")),
                ("Tema/área", record.get("tema")),
                ("Prioridade", label(PRIORIDADES, record["prioridade"])),
                ("Observações", record.get("observacoes")),
            ),
        )
        definition_block(
            "Equipe",
            (
                (
                    "Procurador responsável",
                    ", ".join(groups["PROCURADOR_RESPONSAVEL"]) or "—",
                ),
                ("Signatários", ", ".join(groups["PROCURADOR_SIGNATARIO"]) or "—"),
                ("Assessores", ", ".join(groups["ASSESSOR"]) or "—"),
            ),
        )
        if protocolled:
            definition_block(
                "Processo",
                (
                    ("Número", record.get("numero_processo")),
                    ("Protocolo", format_date_br(record.get("data_protocolo"))),
                    (
                        "Relator",
                        (
                            relator_label(record.get("relator"))
                            if record.get("relator")
                            else None
                        ),
                    ),
                    (
                        "Pedido de medida cautelar",
                        "Sim" if record.get("possui_medida_cautelar") else "Não",
                    ),
                ),
            )
            _render_official_document(store, principal, record)
            if record.get("registro_origem") != "DIRETO":
                with st.expander("Dados do projeto original"):
                    st.caption(
                        "Dados preservados do projeto que originou esta Representação."
                    )
                    st.write(record.get("objeto") or "—")
            try:
                from services.notification_ui import render_protocol_notice

                render_protocol_notice(store, principal, record, rerun_scope="fragment")
            except ValueError as exc:
                st.error(str(exc))
            except Exception:
                LOGGER.exception("Falha ao exibir a comunicação do protocolo")
                st.error("Não foi possível carregar a comunicação do protocolo.")
        else:
            st.caption("Este projeto ainda não foi protocolado no TRAMITA.")
    elif section == "Andamentos":
        st.button(
            "Novo andamento",
            key="rep_dt_prg",
            on_click=_set_ui_state,
            args=(
                "representacoes_progress",
                record["id"],
                "rep_prg_form_" + str(record["id"]),
            ),
        )
        timeline = progress(store, record["id"])
        if not timeline:
            empty_state("Nenhum andamento registrado.")
        show_all = len(timeline) <= 20 or st.checkbox(
            "Ver todos os andamentos", key="rep_all_progress_" + str(record["id"])
        )
        pending = st.session_state.get("representacoes_exclude_progress")
        for item in timeline if show_all else timeline[:20]:
            _render_progress_item(store, principal, record, item, pending)
        if len(timeline) > 20:
            st.caption(
                f"Exibidos {len(timeline) if show_all else 20} de {len(timeline)} andamentos."
            )
    elif section == "Documentos":
        st.button(
            "Anexar documento",
            key="rep_dt_doc",
            on_click=_set_ui_state,
            args=(
                "representacoes_file",
                record["id"],
                "rep_doc_form_" + str(record["id"]),
            ),
        )
        files = documents(store, record["id"])
        if not files:
            empty_state("Nenhum documento anexado.")
        pending = st.session_state.get("representacoes_download")
        for item in files:
            cols = st.columns([3, 2, 2, 2])
            cols[0].write(label(DOCUMENTOS, item["tipo_documento"]))
            cols[1].caption(item.get("descricao") or item["nome_arquivo"])
            cols[2].caption(format_date_br(item.get("data_documento"), empty=""))
            if pending == item["id"]:
                file = download(store, item["id"])
                cols[3].download_button(
                    "Baixar",
                    file["conteudo"],
                    file["nome"],
                    file["tipo"],
                    key="rep_dl_" + item["id"],
                )
            elif cols[3].button("Preparar download", key="rep_prep_" + item["id"]):
                from services.audit import registrar_download

                registrar_download(
                    store,
                    modulo="representacoes",
                    entidade_tipo="representacao",
                    entidade_id=record["id"],
                    arquivo=item.get("nome_arquivo"),
                    formato=item.get("mime_type"),
                    rotulo=record.get("titulo"),
                    principal=principal,
                )
                st.session_state["representacoes_download"] = item["id"]
                st.rerun(scope="fragment")
    elif section == "Organização":
        from services.internal_collaboration_ui import render_internal_collaboration
        from services.record_engagement_ui import render_origin_tools

        render_internal_collaboration(store, principal, "representacao", record["id"])
        official = pdf_oficial(store, record["id"])
        render_origin_tools(
            store,
            principal,
            "representacao",
            record["id"],
            record["titulo"],
            pdf_supplier=(
                (lambda: download(store, official["id"])["conteudo"])
                if official
                else None
            ),
            ai_context={
                "tipo": "Representação",
                "numero": record.get("numero") or "",
                "assunto": record.get("titulo") or "",
                "objeto": record.get("objeto") or "",
                "origem": record.get("origem") or "",
                "identificacao": record.get("titulo") or "",
            },
            rerun_scope="fragment",
        )
    else:
        flow = _toolbar(
            "rep_toolbar_flow",
            [
                (
                    ("Registrar protocolo", "rep_dt_prot", "primary")
                    if not protocolled
                    and has_permission(principal, "representacoes_registrar_protocolo")
                    else None
                ),
                ("Editar", "rep_dt_ed", "secondary"),
            ],
        )
        if flow == "rep_dt_prot":
            st.session_state["representacoes_protocol"] = record["id"]
            _clear_widget_state("rep_prot_" + str(record["id"]))
            st.rerun(scope="fragment")
        if flow == "rep_dt_ed":
            st.session_state["representacoes_edit"] = record["id"]
            _clear_widget_state("rep_form_" + str(record["id"]))
            st.rerun(scope="fragment")
        if protocolled:
            fase_keys = list(FASES)
            chosen = st.selectbox(
                "Atualizar fase",
                fase_keys,
                index=(
                    fase_keys.index(record.get("fase_processual"))
                    if record.get("fase_processual") in FASES
                    else 0
                ),
                format_func=FASES.get,
                key="rep_dt_fase_" + str(record["id"]),
            )
            situacao = st.selectbox(
                "Situação institucional",
                ["EM_TRAMITACAO", "JULGADA", "ENCERRADA", "SUSPENSA", "CANCELADA"],
                index=(
                    [
                        "EM_TRAMITACAO",
                        "JULGADA",
                        "ENCERRADA",
                        "SUSPENSA",
                        "CANCELADA",
                    ].index(record["situacao"])
                    if record.get("situacao")
                    in {
                        "EM_TRAMITACAO",
                        "JULGADA",
                        "ENCERRADA",
                        "SUSPENSA",
                        "CANCELADA",
                    }
                    else 0
                ),
                format_func=SITUACOES.get,
                key="rep_dt_sit_" + str(record["id"]),
            )
            if st.button("Salvar fase", key="rep_dt_fase_ok"):
                set_phase(store, record["id"], chosen, principal)
                _done("Fase processual atualizada.")
            if st.button("Atualizar situação", key="rep_dt_sit_ok"):
                set_status(store, record["id"], situacao, principal)
                _done("Situação atualizada.")
        reason = delete_blocked_reason(record)
        if reason:
            st.caption(reason)
        elif st.button("Excluir definitivamente", key="rep_del"):
            st.session_state["representacoes_confirm_delete"] = record["id"]
            st.rerun(scope="fragment")
        if st.session_state.get("representacoes_confirm_delete") == record["id"]:
            st.warning(
                "Tem certeza de que deseja excluir definitivamente este Projeto de Representação?"
            )
            if st.button("Confirmar exclusão", type="primary", key="rep_del_yes"):
                delete(store, record["id"], principal)
                _clear_forms()
                _done("Projeto de Representação excluído.")
    st.button(
        "Voltar",
        key="rep_dt_back",
        on_click=_return_to_listing,
    )


def _detail_body(store, principal, record):
    procuradores_map, assessores_map = people_index(store)
    groups = grouped_members(record, procuradores_map, assessores_map)
    st.subheader(kind_label(record))
    render_record(
        record["titulo"],
        badges_html=badges(*_badge_items(record)),
        boxed=True,
    )
    definition_block(
        "Identificação",
        (
            ("Objeto", record.get("objeto")),
            ("Origem", label(ORIGENS, record["origem"])),
            ("Abertura", format_date_br(record.get("data_abertura"))),
            ("Representado", record.get("representado")),
            ("Tema/área", record.get("tema")),
            ("Prioridade", label(PRIORIDADES, record["prioridade"])),
            ("Observações", record.get("observacoes")),
        ),
    )
    if record.get("origem") == "OUVIDORIA":
        linked = None
        if has_permission(principal, "ouvidoria"):
            from services.ouvidoria import by_representation

            linked = by_representation(store, record["id"])
        if linked:
            definition_block(
                "Notícia de fato da Ouvidoria",
                (("Notícia de fato", linked["numero_interno"]),),
            )
            if st.button("Abrir notícia de fato", key="rep_open_ouvi"):
                from portal import request_portal_navigation

                request_portal_navigation("Ouvidoria", ouvidoria_open_id=linked["id"])
        else:
            st.caption(
                "Origem institucional: Ouvidoria. Os dados internos da notícia de fato não estão disponíveis nesta conta."
            )
    definition_block(
        "Equipe",
        (
            (
                "Procurador responsável",
                ", ".join(groups["PROCURADOR_RESPONSAVEL"]) or "—",
            ),
            ("Signatários", ", ".join(groups["PROCURADOR_SIGNATARIO"]) or "—"),
            ("Assessores", ", ".join(groups["ASSESSOR"]) or "—"),
        ),
    )
    if record.get("numero_processo"):
        definition_block(
            "Processo",
            (
                ("Número", record.get("numero_processo")),
                ("Protocolo", format_date_br(record.get("data_protocolo"))),
                (
                    "Relator",
                    (
                        relator_label(record.get("relator"))
                        if record.get("relator")
                        else None
                    ),
                ),
                (
                    "Pedido de medida cautelar",
                    "Sim" if record.get("possui_medida_cautelar") else "Não",
                ),
                ("Situação", label(SITUACOES, record["situacao"])),
                ("Fase", label(FASES, record.get("fase_processual"))),
            ),
        )
    else:
        st.caption("Este projeto ainda não foi protocolado no TRAMITA.")
    section_label("Andamentos")
    timeline = progress(store, record["id"])
    if not timeline:
        empty_state("Nenhum andamento registrado.")
    for item in timeline:
        heading, descricao = andamento_display(item)
        line = f"**{format_date_br(item['data'])}** · {heading}"
        if descricao:
            line += f"  \n{descricao}"
        st.markdown(line)
    section_label("Documentos")
    files = documents(store, record["id"])
    if not files:
        empty_state("Nenhum documento anexado.")
    pending = st.session_state.get("representacoes_download")
    for item in files:
        cols = st.columns([3, 2, 2, 2])
        cols[0].write(label(DOCUMENTOS, item["tipo_documento"]))
        cols[1].caption(item.get("descricao") or item["nome_arquivo"])
        cols[2].caption(format_date_br(item.get("data_documento"), empty=""))
        if pending == item["id"]:
            file = download(store, item["id"])
            cols[3].download_button(
                "Baixar",
                file["conteudo"],
                file["nome"],
                file["tipo"],
                key="rep_dl_" + item["id"],
            )
        elif cols[3].button("Preparar download", key="rep_prep_" + item["id"]):
            from services.audit import registrar_download

            registrar_download(
                store,
                modulo="representacoes",
                entidade_tipo="representacao",
                entidade_id=record["id"],
                arquivo=item.get("nome_arquivo"),
                formato=item.get("mime_type"),
                rotulo=record.get("titulo"),
                principal=principal,
            )
            st.session_state["representacoes_download"] = item["id"]
            st.rerun()
    from services.internal_collaboration_ui import render_internal_collaboration

    render_internal_collaboration(store, principal, "representacao", record["id"])
    from services.record_engagement_ui import render_origin_tools

    official = pdf_oficial(store, record["id"])
    render_origin_tools(
        store,
        principal,
        "representacao",
        record["id"],
        record["titulo"],
        pdf_supplier=(
            (lambda: download(store, official["id"])["conteudo"]) if official else None
        ),
        ai_context={
            "tipo": "Representação",
            "numero": record.get("numero") or "",
            "assunto": record.get("titulo") or "",
            "objeto": record.get("objeto") or "",
            "origem": record.get("origem") or "",
            "identificacao": record.get("titulo") or "",
        },
        rerun_scope="fragment",
    )
    protocolled = is_protocolled(record)
    flow = _toolbar(
        "rep_toolbar_flow",
        [
            (
                ("Registrar protocolo", "rep_dt_prot", "primary")
                if not protocolled
                and has_permission(principal, "representacoes_registrar_protocolo")
                else None
            ),
            ("Andamento", "rep_dt_prg", "secondary"),
            ("Anexar documento", "rep_dt_doc", "secondary"),
            ("Editar", "rep_dt_ed", "secondary"),
        ],
    )
    if flow == "rep_dt_prot":
        st.session_state["representacoes_protocol"] = record["id"]
        st.rerun()
    elif flow == "rep_dt_prg":
        st.session_state["representacoes_progress"] = record["id"]
        st.rerun()
    elif flow == "rep_dt_doc":
        st.session_state["representacoes_file"] = record["id"]
        st.rerun()
    elif flow == "rep_dt_ed":
        st.session_state["representacoes_edit"] = record["id"]
        st.rerun()
    if protocolled:
        section_label("Fase processual")
        fase_keys = list(FASES)
        chosen = st.selectbox(
            "Atualizar fase",
            fase_keys,
            index=(
                fase_keys.index(record["fase_processual"])
                if record.get("fase_processual") in FASES
                else 0
            ),
            format_func=FASES.get,
            key="rep_dt_fase",
        )
        inst = st.selectbox(
            "Situação institucional",
            ["EM_TRAMITACAO", "JULGADA", "ENCERRADA", "SUSPENSA", "CANCELADA"],
            format_func=SITUACOES.get,
            key="rep_dt_sit",
        )
        state = _toolbar(
            "rep_toolbar_state",
            [
                ("Salvar fase", "rep_dt_fase_ok", "secondary"),
                ("Atualizar situação", "rep_dt_sit_ok", "secondary"),
            ],
        )
        if state == "rep_dt_fase_ok":
            try:
                set_phase(store, record["id"], chosen, principal)
            except ValueError as exc:
                st.error(str(exc))
            else:
                _done("Fase processual atualizada.")
        elif state == "rep_dt_sit_ok":
            try:
                set_status(store, record["id"], inst, principal)
            except ValueError as exc:
                st.error(str(exc))
            else:
                _done("Situação atualizada.")
    reason = delete_blocked_reason(record)
    if reason:
        with st.container(key="rep_note"):
            st.caption(reason)
    elif st.session_state.get("representacoes_confirm_delete") == record["id"]:
        warning = (
            "Tem certeza de que deseja excluir definitivamente este Projeto de Representação? "
            "Esta ação não poderá ser desfeita."
        )
        if record.get("origem") == "OUVIDORIA":
            warning += " O vínculo com a Notícia de Fato será removido, mas a Notícia de Fato permanecerá cadastrada."
        st.warning(warning)
        confirm = _toolbar(
            "rep_toolbar_confirm",
            [
                ("Excluir definitivamente", "rep_del_yes", "primary"),
                ("Cancelar", "rep_del_no", "secondary"),
            ],
        )
        if confirm == "rep_del_yes":
            try:
                delete(store, record["id"], principal)
            except ValueError as exc:
                st.error(str(exc))
            else:
                _clear_forms()
                _done("Projeto de Representação excluído.")
        elif confirm == "rep_del_no":
            st.session_state.pop("representacoes_confirm_delete", None)
            st.rerun()
    extra = _toolbar(
        "rep_toolbar_more",
        [
            (
                None
                if reason
                or st.session_state.get("representacoes_confirm_delete") == record["id"]
                else ("Excluir definitivamente", "rep_del", "secondary")
            ),
            ("Voltar", "rep_dt_back", "secondary"),
        ],
    )
    if extra == "rep_del":
        st.session_state["representacoes_confirm_delete"] = record["id"]
        st.rerun()
    elif extra == "rep_dt_back":
        st.session_state.pop("representacoes_view", None)
        st.rerun()


def _home_actions(principal):
    with st.container(key="rep_home_actions", horizontal=True):
        st.button(
            "+ Novo projeto de Representação",
            type="primary",
            key="rep_new",
            on_click=_set_ui_state,
            args=("representacoes_edit", {}, "rep_form_new"),
        )
        if has_permission(principal, "representacoes_registrar_protocolo"):
            st.button(
                "Registrar Representação Protocolada",
                key="rep_direct_new",
                on_click=_set_ui_state,
                args=(
                    "representacoes_direct",
                    True,
                    "rep_direct_base_",
                    "rep_direct_prot_",
                    "rep_direct_ai_",
                ),
            )


def render(store, principal):
    _consume_widget_cleanup()
    require_permission(principal, "representacoes")
    st.title(module_title("representacoes", "REPRESENTAÇÕES"))
    st.caption(
        "Acompanhamento interno das Representações protocoladas no TRAMITA desde janeiro de 2026."
    )
    _listing_chrome()
    if message := st.session_state.pop("representacoes_message", None):
        st.success(message)
    counts = overview(store)
    _kpis(counts)
    view_id = st.session_state.get("representacoes_view")
    edit_id = st.session_state.get("representacoes_edit")
    if st.session_state.get("representacoes_protocol"):
        _protocol_form(store, principal, st.session_state["representacoes_protocol"])
        return
    if st.session_state.get("representacoes_direct"):
        _direct_protocol_form(store, principal)
        return
    if st.session_state.get("representacoes_progress"):
        _progress_form(store, principal, st.session_state["representacoes_progress"])
        return
    if st.session_state.get("representacoes_file"):
        _document_form(store, principal, st.session_state["representacoes_file"])
        return
    if edit_id is not None:
        current = None if edit_id == {} else get(store, edit_id)
        if current and is_protocolled(current):
            st.subheader("Editar Representação")
        elif current:
            st.subheader("Editar projeto de Representação")
        else:
            st.subheader("Novo projeto de Representação")
        payload = _form(store, current)
        with st.container(
            key="rep_form_actions", horizontal=True, horizontal_alignment="right"
        ):
            if payload and st.button("Salvar", type="primary", key="rep_save"):
                try:
                    if current:
                        saved = update(store, current["id"], payload, principal)
                        if (
                            payload.get("situacao")
                            and payload["situacao"] != current["situacao"]
                        ):
                            set_status(
                                store, current["id"], payload["situacao"], principal
                            )
                    else:
                        saved = create(store, payload, principal)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["representacoes_edit"] = None
                    st.session_state.pop("representacoes_edit", None)
                    _queue_widget_cleanup(
                        "rep_form_" + str(current["id"] if current else "new")
                    )
                    st.session_state["representacoes_view"] = saved["id"]
                    _done(kind_saved_message(saved))
            st.button(
                "Cancelar",
                key="rep_cancel",
                on_click=_clear_ui_state,
                args=(
                    "representacoes_edit",
                    "rep_form_" + str(current["id"] if current else "new"),
                ),
            )
        return
    if view_id:
        record = get(store, view_id)
        if record is None:
            st.session_state.pop("representacoes_view", None)
            st.warning("Registro não encontrado.")
        else:
            _detail(store, principal, record)
            return
    _home_actions(principal)
    people, servers, procuradores_map, assessores_map = people_context(
        display_store(store)
    )
    st.session_state["_rep_people"] = (people, servers)
    filters = _filters()
    st.session_state["_representacoes_list_filters"] = {
        key: st.session_state[key]
        for key in _LIST_FILTER_KEYS
        if key in st.session_state
    }
    rows = list_records(store, filters)
    if not rows:
        empty_state("Nenhum projeto de Representação encontrado.")
        return
    for index, record in enumerate(rows):
        _card(record, index, procuradores_map, assessores_map)
