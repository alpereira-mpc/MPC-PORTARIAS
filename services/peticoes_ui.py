"""UI for Petições, using only peticoes-prefixed session keys."""

from datetime import date
import hashlib
import unicodedata

import streamlit as st

from database.peticoes import PeticoesStore
from services.access import has_permission, require_permission
from services.branding import module_title
from services.date_format import format_date_br
from services.peticoes import (
    NATUREZAS,
    RESULTADOS,
    SITUACOES,
    SITUACOES_ABERTAS,
    add_progress,
    bloco_pedidos,
    conclude,
    create,
    remove_progress,
    save_resultado,
    texto_pedidos,
    texto_pedidos_ia,
    update,
)
from services.ui_theme import (
    actions_mark,
    badges,
    card_container,
    definition_block,
    empty_state,
    render_record,
    status_tone,
)

DESTINOS = (
    "Presidente do TCE-PB",
    "Conselheiro Relator",
    "Outro destinatário institucional",
)
SECOES = ("Acompanhamento", "Cadastrar Petição", "Histórico")
FILTROS_SITUACAO = {
    "Em aberto": SITUACOES_ABERTAS,
    "Protocolada": ("PROTOCOLADA",),
    "Em acompanhamento": ("EM_ACOMPANHAMENTO",),
    "Concluída": ("CONCLUIDA",),
    "Todas": None,
}
PAGE_SIZE = 15
PREVIEW_LIMIT = 280


def _remember(key, default):
    if key not in st.session_state:
        st.session_state[key] = default


def _file_hash(upload):
    getter = getattr(upload, "getvalue", None)
    if not callable(getter):
        return ""
    return hashlib.sha256(getter()).hexdigest()


def _apply_ai_prefill(store):
    data = st.session_state.pop("peticoes_ai_pending", None)
    if not data:
        return
    source = st.session_state.get("peticoes_ai_file")
    if source and _file_hash(st.session_state.get("peticoes_new_pdf")) != source:
        return
    mapping = {
        "numero_tramita": "peticoes_new_numero",
        "destinatario": "peticoes_new_destinatario",
        "natureza": "peticoes_new_natureza",
        "assunto": "peticoes_new_assunto",
        "objeto": "peticoes_new_objeto",
        "origem": "peticoes_new_origem",
        "processo_tc": "peticoes_new_processo",
    }
    for source_name, target in mapping.items():
        if source_name == "natureza" and data.get(source_name) not in NATUREZAS:
            continue
        if data.get(source_name) and not st.session_state.get(target):
            st.session_state[target] = data[source_name]
    if data.get("data_protocolo") and not st.session_state.get("peticoes_new_data"):
        try:
            st.session_state["peticoes_new_data"] = date.fromisoformat(
                data["data_protocolo"]
            )
        except ValueError:
            pass

    def normalized(value):
        decomposed = unicodedata.normalize("NFKD", value or "")
        return " ".join(
            "".join(char for char in decomposed if not unicodedata.combining(char))
            .casefold()
            .split()
        )

    people = {
        normalized(person["nome"]): person["id"]
        for person in store.catalog("procuradores")
        if person.get("ativo")
    }
    matched = [people.get(normalized(name)) for name in data.get("signatarios", [])]
    matched = [value for value in matched if value]
    if matched and not st.session_state.get("peticoes_new_signatarios"):
        st.session_state["peticoes_new_signatarios"] = matched
    if data.get("pedidos") and not st.session_state.get("peticoes_new_pedidos"):
        block = texto_pedidos_ia(data["pedidos"])
        if block:
            st.session_state["peticoes_new_pedidos"] = block


def _preencher_com_ia(upload):
    if not upload:
        st.warning("Selecione o PDF protocolado antes de solicitar a análise.")
        return
    content = upload.getvalue()
    file_hash = hashlib.sha256(content).hexdigest()
    try:
        from services.ai_service import analisar_peticao_pdf

        result = analisar_peticao_pdf(content)
    except RuntimeError as exc:
        st.error(str(exc))
        return
    st.session_state["peticoes_ai_result_" + file_hash] = result
    st.session_state["peticoes_ai_pending"] = result
    st.session_state["peticoes_ai_file"] = file_hash
    st.success(
        "Campos preenchidos com IA. Revise as informações antes de cadastrar a Petição."
    )


def _data_form(store, record=None):
    record = record or {}
    people = [person for person in store.catalog("procuradores") if person.get("ativo")]
    choices = {person["id"]: person["nome"] for person in people}
    prefix = "peticoes_edit_" if record else "peticoes_new_"
    _remember(prefix + "numero", record.get("numero_tramita") or "")
    number = st.text_input("Número do documento Tramita *", key=prefix + "numero")
    default_day = (
        date.fromisoformat(record["data_protocolo"])
        if record.get("data_protocolo")
        else date.today()
    )
    _remember(prefix + "data", default_day)
    protocol = st.date_input(
        "Data do protocolo *", format="DD/MM/YYYY", key=prefix + "data"
    )
    _remember(prefix + "tipo", record.get("destinatario_tipo") or DESTINOS[0])
    destination_type = st.selectbox(
        "Tipo de destinatário", list(DESTINOS), key=prefix + "tipo"
    )
    _remember(prefix + "destinatario", record.get("destinatario") or "")
    destination = st.text_input("Destinatário *", key=prefix + "destinatario")
    natureza = record.get("natureza") or "PROVIDENCIAS"
    if natureza not in NATUREZAS:
        natureza = "PROVIDENCIAS"
    _remember(prefix + "natureza", natureza)
    nature = st.selectbox(
        "Natureza *",
        list(NATUREZAS),
        format_func=NATUREZAS.get,
        key=prefix + "natureza",
    )
    _remember(prefix + "assunto", record.get("assunto") or "")
    subject = st.text_input("Assunto *", key=prefix + "assunto")
    _remember(prefix + "objeto", record.get("objeto") or "")
    obj = st.text_area("Objeto *", key=prefix + "objeto")
    _remember(prefix + "origem", record.get("origem") or "")
    origin = st.text_input("Origem", key=prefix + "origem")
    _remember(prefix + "processo", record.get("processo_tc") or "")
    process = st.text_input("Processo TC relacionado", key=prefix + "processo")
    _remember(
        prefix + "signatarios",
        [item for item in record.get("signatarios", []) if item in choices],
    )
    signers = st.multiselect(
        "Procuradores signatários *",
        list(choices),
        format_func=choices.get,
        key=prefix + "signatarios",
    )
    return {
        "numero_tramita": number,
        "data_protocolo": protocol.isoformat(),
        "destinatario_tipo": destination_type,
        "destinatario": destination,
        "natureza": nature,
        "assunto": subject,
        "objeto": obj,
        "origem": origin,
        "processo_tc": process,
        "signatarios": signers,
    }


def _bind_editor(record, choices):
    if st.session_state.get("peticoes_edit_loaded") == record["id"]:
        return
    prefix = "peticoes_edit_"
    st.session_state[prefix + "numero"] = record.get("numero_tramita") or ""
    st.session_state[prefix + "data"] = date.fromisoformat(record["data_protocolo"])
    tipo = record.get("destinatario_tipo") or DESTINOS[0]
    st.session_state[prefix + "tipo"] = tipo if tipo in DESTINOS else DESTINOS[0]
    st.session_state[prefix + "destinatario"] = record.get("destinatario") or ""
    natureza = record.get("natureza") or "PROVIDENCIAS"
    st.session_state[prefix + "natureza"] = (
        natureza if natureza in NATUREZAS else "PROVIDENCIAS"
    )
    st.session_state[prefix + "assunto"] = record.get("assunto") or ""
    st.session_state[prefix + "objeto"] = record.get("objeto") or ""
    st.session_state[prefix + "origem"] = record.get("origem") or ""
    st.session_state[prefix + "processo"] = record.get("processo_tc") or ""
    st.session_state[prefix + "signatarios"] = [
        item for item in record.get("signatarios", []) if item in choices
    ]
    st.session_state["peticoes_edit_pedidos"] = texto_pedidos(record.get("pedidos"))
    st.session_state["peticoes_edit_loaded"] = record["id"]


def _close_editor():
    st.session_state.pop("peticoes_edit_id", None)
    st.session_state.pop("peticoes_edit_loaded", None)


def _render_editor(store, db, principal):
    if not has_permission(principal, "peticoes_editar"):
        raise ValueError("Acesso não autorizado a esta ação.")
    record = db.get(st.session_state["peticoes_edit_id"])
    if not record:
        _close_editor()
        st.warning("Petição não encontrada.")
        return
    people = [person for person in store.catalog("procuradores") if person.get("ativo")]
    choices = {person["id"]: person["nome"] for person in people}
    _bind_editor(record, choices)
    st.subheader("Editar Petição")
    original = texto_pedidos(record.get("pedidos"))
    with st.form("peticoes_edit_form"):
        data = _data_form(store, record)
        requests = st.text_area("Pedidos da Petição *", key="peticoes_edit_pedidos")
        upload = st.file_uploader(
            "Substituir PDF protocolado (opcional)",
            type=["pdf"],
            key="peticoes_edit_pdf",
        )
        saved = st.form_submit_button("Salvar alterações", type="primary")
    if saved:
        try:
            if requests.strip() != original.strip():
                data["pedidos"] = bloco_pedidos(requests)
            update(
                store,
                record["id"],
                data,
                principal,
                (
                    (upload.name, upload.type or "application/pdf", upload.getvalue())
                    if upload
                    else None
                ),
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            _close_editor()
            st.success("Petição atualizada.")
            st.rerun()
    anteriores = db.resultados_anteriores(record["id"])
    if anteriores:
        st.caption("Histórico de resultados anteriores")
        _render_resultados_anteriores(anteriores)
    if st.button("Cancelar", key="peticoes_edit_cancel"):
        _close_editor()
        st.rerun()


def _render_cadastro(store, principal):
    if not has_permission(principal, "peticoes_cadastrar"):
        st.info("Sua conta possui somente permissão de visualização.")
        return
    upload = st.file_uploader("PDF protocolado *", type=["pdf"], key="peticoes_new_pdf")
    if st.button("✨ Preencher com IA", key="peticoes_ai_fill"):
        _preencher_com_ia(upload)
    _apply_ai_prefill(store)
    data = _data_form(store)
    requests = st.text_area("Pedidos da Petição *", key="peticoes_new_pedidos")
    if st.button("Cadastrar Petição", type="primary", key="peticoes_create"):
        try:
            data["pedidos"] = bloco_pedidos(requests)
            create(
                store,
                data,
                principal,
                (
                    (upload.name, upload.type or "application/pdf", upload.getvalue())
                    if upload
                    else None
                ),
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.success("Petição protocolada cadastrada.")
            st.rerun()


def _painel():
    panel = st.session_state.get("peticoes_painel")
    if not isinstance(panel, dict):
        panel = {}
        st.session_state["peticoes_painel"] = panel
    return panel


def _toggle(name):
    panel = dict(_painel())
    panel[name] = not panel.get(name, False)
    st.session_state["peticoes_painel"] = panel
    st.rerun()


def _preview(text):
    compact = " ".join(str(text or "").split())
    if len(compact) <= PREVIEW_LIMIT:
        return compact
    return compact[: PREVIEW_LIMIT - 1].rstrip() + "…"


def _registrar_andamento(store, principal, identifier, date_key, text_key, error_key):
    try:
        add_progress(
            store,
            identifier,
            st.session_state[date_key].isoformat(),
            st.session_state.get(text_key) or "",
            principal,
        )
    except ValueError as exc:
        st.session_state[error_key] = str(exc)
    else:
        st.session_state[text_key] = ""
        st.session_state.pop(error_key, None)


def _salvar_resultado(store, principal, identifier, key, error_key):
    try:
        save_resultado(store, identifier, st.session_state.get(key) or "", principal)
    except ValueError as exc:
        st.session_state[error_key] = str(exc)
    else:
        st.session_state.pop(error_key, None)
        st.session_state["peticoes_resultado_ok_" + str(identifier)] = True


def _concluir_peticao(store, principal, identifier, key, error_key):
    try:
        conclude(store, identifier, st.session_state.get(key) or "", principal)
    except ValueError as exc:
        st.session_state[error_key] = str(exc)
    else:
        st.session_state.pop(error_key, None)


def _render_resultados_anteriores(items):
    for item in items:
        status = RESULTADOS.get(item["situacao_resultado"], item["situacao_resultado"])
        when = format_date_br(item.get("data_resultado") or "", empty="")
        caption = status + (" · " + when if when else "")
        st.caption(caption)
        if item.get("descricao"):
            st.write(item["descricao"])
        if str(item.get("resultado") or "").strip():
            st.write(item["resultado"])


def _card(store, db, principal, record, index, *, acompanhar):
    identifier = record["id"]
    panel = _painel()
    last = record.get("ultimo_andamento") or {}
    last_label = ""
    if last:
        last_label = f"{format_date_br(last.get('data'))} — {last.get('descricao')}"
    names = " • ".join(
        item["nome"] for item in record.get("signatarios") or [] if item.get("nome")
    )
    with card_container(index, f"pet_{identifier}"):
        render_record(
            f"{record['numero_tramita']} — {record['assunto']}",
            badges_html=badges(
                (
                    SITUACOES.get(record["situacao"], record["situacao"]),
                    status_tone(SITUACOES.get(record["situacao"], "")),
                ),
                (format_date_br(record["data_protocolo"]), "neutral"),
            ),
            secondary="Natureza: "
            + NATUREZAS.get(record["natureza"], record["natureza"]),
            meta="Destinatário: " + (record.get("destinatario") or "—"),
        )
        definition_block(
            "",
            (
                ("Objeto", _preview(record.get("objeto"))),
                ("Signatários", names),
                ("Último andamento", last_label),
                ("Resultado da Petição", record.get("resultado_global") or ""),
            ),
        )
        if acompanhar and has_permission(principal, "peticoes_registrar_andamento"):
            with st.expander("Registrar andamento"):
                st.date_input(
                    "Data",
                    format="DD/MM/YYYY",
                    key=f"peticoes_andamento_data_{identifier}",
                )
                st.text_area(
                    "Descrição do andamento",
                    key=f"peticoes_andamento_texto_{identifier}",
                )
                error_key = f"peticoes_andamento_erro_{identifier}"
                st.button(
                    "Registrar",
                    key=f"peticoes_andamento_salvar_{identifier}",
                    on_click=_registrar_andamento,
                    args=(
                        store,
                        principal,
                        identifier,
                        f"peticoes_andamento_data_{identifier}",
                        f"peticoes_andamento_texto_{identifier}",
                        error_key,
                    ),
                )
                if message := st.session_state.get(error_key):
                    st.error(message)
        can_result = has_permission(principal, "peticoes_registrar_resultado")
        can_finish = (
            has_permission(principal, "peticoes_concluir")
            and record["situacao"] != "CONCLUIDA"
        )
        if can_result or can_finish:
            with st.expander("Resultado da Petição"):
                result_key = f"peticoes_resultado_{identifier}"
                _remember(result_key, record.get("resultado_global") or "")
                st.text_area("Resultado da Petição", key=result_key)
                error_key = f"peticoes_resultado_erro_{identifier}"
                if can_result:
                    st.button(
                        "Salvar resultado",
                        key=f"peticoes_resultado_salvar_{identifier}",
                        on_click=_salvar_resultado,
                        args=(store, principal, identifier, result_key, error_key),
                    )
                if can_finish:
                    st.button(
                        "Concluir Petição",
                        key=f"peticoes_concluir_{identifier}",
                        on_click=_concluir_peticao,
                        args=(store, principal, identifier, result_key, error_key),
                    )
                if message := st.session_state.get(error_key):
                    st.error(message)
                if st.session_state.pop(f"peticoes_resultado_ok_{identifier}", None):
                    st.success("Resultado da Petição registrado.")
        actions_mark()
        left, right = st.columns(2)
        detail_open = bool(panel.get(f"d{identifier}"))
        history_open = bool(panel.get(f"h{identifier}"))
        if left.button(
            "Ocultar detalhes" if detail_open else "Ver detalhes",
            key=f"peticoes_detalhe_btn_{identifier}",
        ):
            _toggle(f"d{identifier}")
        if right.button(
            (
                "Ocultar histórico de andamentos"
                if history_open
                else "Ver histórico de andamentos"
            ),
            key=f"peticoes_hist_btn_{identifier}",
        ):
            _toggle(f"h{identifier}")
        extra_left, extra_right = st.columns(2)
        if extra_left.button("PDF", key=f"peticoes_pdf_btn_{identifier}"):
            opened = dict(_painel())
            opened[f"p{identifier}"] = True
            st.session_state["peticoes_painel"] = opened
            st.rerun()
        if has_permission(principal, "peticoes_editar") and extra_right.button(
            "Editar", key=f"peticoes_edit_open_{identifier}"
        ):
            st.session_state["peticoes_edit_id"] = identifier
            st.rerun()
        if detail_open:
            st.markdown("**Objeto**")
            st.text(record.get("objeto") or "—")
            st.markdown("**Pedidos da Petição**")
            st.text(texto_pedidos(record.get("pedidos")) or "—")
            if record.get("origem"):
                st.caption("Origem: " + record["origem"])
            if record.get("processo_tc"):
                st.caption("Processo TC relacionado: " + record["processo_tc"])
            if record.get("resultados_anteriores"):
                st.caption("Histórico de resultados anteriores")
                _render_resultados_anteriores(record["resultados_anteriores"])
        if history_open:
            items = db.progress(identifier)
            if not items:
                st.caption("Nenhum andamento registrado.")
            for item in items:
                st.write(f"{format_date_br(item['data'])} — {item['descricao']}")
                if has_permission(
                    principal, "peticoes_registrar_andamento"
                ) and st.button(
                    "Excluir logicamente", key=f"peticoes_remove_{item['id']}"
                ):
                    remove_progress(
                        store,
                        item["id"],
                        "Exclusão registrada pelo usuário.",
                        principal,
                    )
                    st.rerun()
        if panel.get(f"p{identifier}"):
            document = db.document(identifier)
            if document:
                st.download_button(
                    "Baixar PDF protocolado",
                    document["arquivo"],
                    document["nome"],
                    document["mime_type"],
                    key=f"peticoes_pdf_ready_{identifier}",
                )
            else:
                st.warning("PDF protocolado não encontrado.")


def _filters(store, prefix, default):
    people = [person for person in store.catalog("procuradores") if person.get("ativo")]
    labels = ["Todos"] + [person["nome"] for person in people]
    ids = [None] + [person["id"] for person in people]
    search, situation, signer = st.columns(3)
    texto = search.text_input("Busca", key=prefix + "q")
    chosen = situation.selectbox(
        "Situação",
        list(FILTROS_SITUACAO),
        index=list(FILTROS_SITUACAO).index(default),
        key=prefix + "sit",
    )
    person = signer.selectbox("Procurador signatário", labels, key=prefix + "sig")
    signatario = ids[labels.index(person)] if person in labels else None
    return texto, FILTROS_SITUACAO[chosen], signatario


def _render_lista(store, db, principal, *, prefix, default, acompanhar, empty):
    texto, situacoes, signatario = _filters(store, prefix, default)
    signature = (texto, situacoes, signatario)
    signature_key = prefix + "signature"
    page_key = prefix + "page"
    if st.session_state.get(signature_key) != signature:
        st.session_state[page_key] = 0
        st.session_state[signature_key] = signature
    page = int(st.session_state.get(page_key) or 0)
    rows = db.list_resumo(
        situacoes=situacoes,
        texto=texto,
        signatario_id=signatario,
        limit=PAGE_SIZE + 1,
        offset=page * PAGE_SIZE,
    )
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    if not rows:
        empty_state(empty)
    for index, record in enumerate(rows):
        _card(
            store,
            db,
            principal,
            record,
            index,
            acompanhar=acompanhar,
        )
    if page or has_next:
        previous, label, nxt = st.columns([1, 2, 1])
        if page and previous.button("Anterior", key=prefix + "prev"):
            st.session_state[page_key] = page - 1
            st.rerun()
        label.caption(f"Página {page + 1}")
        if has_next and nxt.button("Próxima", key=prefix + "next"):
            st.session_state[page_key] = page + 1
            st.rerun()


def render(store, principal):
    require_permission(principal, "peticoes")
    db = PeticoesStore(store)
    st.title(module_title("peticoes", "PETIÇÕES"))
    if st.session_state.get("peticoes_edit_id"):
        _render_editor(store, db, principal)
        return
    section = st.radio("Seção", list(SECOES), horizontal=True, key="peticoes_secao")
    if section == "Cadastrar Petição":
        _render_cadastro(store, principal)
        return
    if section == "Histórico":
        _render_lista(
            store,
            db,
            principal,
            prefix="peticoes_hist_",
            default="Concluída",
            acompanhar=False,
            empty="Nenhuma petição encontrada para os filtros selecionados.",
        )
        return
    _render_lista(
        store,
        db,
        principal,
        prefix="peticoes_acomp_",
        default="Em aberto",
        acompanhar=True,
        empty="Nenhuma petição em acompanhamento para os filtros selecionados.",
    )
