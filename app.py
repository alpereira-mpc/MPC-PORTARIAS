"""MPC-PB — local institutional Portaria workflow."""

from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
import logging
import streamlit as st
from database.store import Store, ROOT
from document_generator.docx import generate
from document_generator.pdf import convert, PdfUnavailable
from services.exports import setup_logging, export_record
from services.validation import warnings_for
from services.wording import compose, preview_text, parsed, role, validate
from services.wording import reason_text, normalized_payload
from services.placeholders import assert_docx_clean
from services.deletion import REASONS

VERSION = "1.1.0"
st.set_page_config(
    page_title="MPC-PB | Portarias PROGE",
    page_icon=str(ROOT / "assets/logo.jpeg"),
    layout="wide",
)
store = Store()
setup_logging(store.path.parent)
settings = store.settings()
# Drop document byte caches from sessions opened before this update.
for cached_key in list(st.session_state):
    if cached_key.endswith(("filedocx", "filepdf")):
        st.session_state.pop(cached_key, None)
if "next_nav" in st.session_state:
    st.session_state["nav"] = st.session_state.pop("next_nav")


def navigate(page):
    st.session_state["next_nav"] = page
    st.rerun()


def reset_editor(payload=None, identifier=None):
    st.session_state["editor_seed"] = deepcopy(payload or {})
    st.session_state["editor_id"] = identifier
    st.session_state["editor_version"] = st.session_state.get("editor_version", 0) + 1
    st.session_state.pop("preview", None)
    st.session_state.pop("last_finalized", None)


def open_draft(identifier):
    reset_editor(store.get(identifier)["payload"], identifier)
    navigate("Nova Portaria")


def choose(label, options, current=None, key=None, format_func=str, optional=False):
    index = options.index(current) if current in options else (None if optional else 0)
    return st.selectbox(
        label,
        options,
        index=index,
        key=key,
        format_func=format_func,
        placeholder="Selecione",
    )


def error(exc):
    logging.getLogger(__name__).exception("Operação não concluída")
    st.error(
        str(exc)
        if isinstance(exc, (ValueError, PdfUnavailable))
        else "Não foi possível concluir a operação. Os dados já gravados permanecem no histórico. Consulte o log local."
    )


def member_editor(prefix):
    people = store.catalog("procuradores")
    mapping = {p["id"]: p for p in people}
    choice = choose(
        "Editar cadastro",
        [0, *mapping],
        0,
        key=prefix + "pick",
        format_func=lambda i: "Novo procurador" if not i else mapping[i]["nome"],
    )
    m = mapping.get(
        choice,
        {
            "nome": "",
            "genero": "feminino",
            "cargo_base": "Procuradora",
            "funcao": "Procurador",
            "assento": "Não se aplica",
            "ativo": True,
            "observacoes": "",
        },
    )
    key = f"{prefix}{choice}"
    with st.form(key):
        name = st.text_input("Nome completo", m["nome"])
        gender = choose("Gênero gramatical", ["feminino", "masculino"], m["genero"])
        base = st.text_input("Cargo base", m["cargo_base"])
        function = st.text_input(
            "Função atual",
            role(m["funcao"], m["genero"]),
            help="Use Procurador-Geral, Subprocurador-Geral, Procurador, Corregedor ou Ouvidor. A redação ajusta o gênero automaticamente.",
        )
        seat_options = [x["nome"] for x in store.catalog("assentos")]
        seat = choose(
            "Assento atual",
            seat_options,
            m["assento"] if m["assento"] in seat_options else "Outro",
        )
        extra = st.text_input(
            "Outro assento",
            (
                m["assento"]
                if m["assento"] not in [x["nome"] for x in store.catalog("assentos")]
                else ""
            ),
        )
        active = st.checkbox("Ativo", bool(m["ativo"]))
        notes = st.text_area("Observações", m["observacoes"])
        if st.form_submit_button("Salvar procurador", type="primary"):
            try:
                canonical = {
                    role(x, "feminino"): x
                    for x in [
                        "Procurador",
                        "Procurador-Geral",
                        "Subprocurador-Geral",
                        "Corregedor",
                        "Ouvidor",
                    ]
                }.get(function, function)
                store.save_member(
                    dict(
                        id=choice or None,
                        nome=name.strip(),
                        genero=gender,
                        cargo_base=role(base, gender),
                        funcao=canonical,
                        assento=extra if seat == "Outro" else seat,
                        ativo=int(active),
                        observacoes=notes,
                    )
                )
                st.success(
                    "Cadastro salvo. Atos finalizados preservam os dados da emissão."
                )
            except Exception as exc:
                error(exc)


def configuration():
    st.subheader("Configurações")
    st.caption(f"Versão {VERSION} · Dados armazenados neste computador")
    with st.expander("Numeração anual", expanded=True):
        year = int(
            st.number_input(
                "Ano da sequência",
                min_value=1000,
                max_value=9999,
                value=date.today().year,
            )
        )
        current = store.next_number(year) - 1
        st.caption(
            f"Baseline administrativo preservado: {store.baseline(year)}/{year}."
        )
        suggested = (
            8 if year == 2026 and settings.get("sequence_confirmed") != "1" else current
        )
        last = int(
            st.number_input(
                "Última Portaria PROGE emitida no ano",
                min_value=0,
                value=suggested,
                key=f"sequence_{year}",
            )
        )
        st.caption(
            "A sugestão 8 para 2026 vem dos modelos. Confirme o número efetivamente emitido antes do primeiro uso."
        )
        confirm = st.checkbox(
            "Confirmo o último número informado", key="confirm_sequence"
        )
        if st.button("Salvar sequência"):
            try:
                store.set_sequence(year, last, confirm)
                st.success("Sequência confirmada.")
                st.rerun()
            except Exception as exc:
                error(exc)
    with st.expander("Exportação e administração", expanded=True):
        with st.form("export_config"):
            folder = st.text_input("Diretório de exportação", settings["export_dir"])
            engine = choose(
                "Mecanismo PDF",
                ["auto", "word", "libreoffice"],
                settings.get("pdf_engine", "auto"),
                format_func=lambda x: {
                    "auto": "Automático: Word e depois LibreOffice",
                    "word": "Microsoft Word",
                    "libreoffice": "LibreOffice",
                }[x],
            )
            admin = st.checkbox(
                "Habilitar edição administrativa do número",
                settings.get("admin_number") == "1",
            )
            if st.form_submit_button("Salvar configurações"):
                try:
                    if not folder.strip():
                        raise ValueError("Informe o diretório de exportação.")
                    store.configure(
                        export_dir=str(Path(folder).expanduser().resolve()),
                        pdf_engine=engine,
                        admin_number="1" if admin else "0",
                    )
                    st.success("Configurações salvas.")
                except Exception as exc:
                    error(exc)
    with st.expander("Funções atuais e signatário padrão"):
        st.caption(
            "Defina os ocupantes atuais. Alterações não modificam Portarias já finalizadas."
        )
        current = {p["id"]: p for p in store.catalog("procuradores") if p["ativo"]}
        assignments = []
        for function, seat in [
            ("Procurador-Geral", "Tribunal Pleno"),
            ("Subprocurador-Geral", "1ª Câmara"),
            ("Subprocurador-Geral", "2ª Câmara"),
            ("Ouvidor", "Não se aplica"),
            ("Corregedor", "Não se aplica"),
        ]:
            default = next(
                (
                    i
                    for i, p in current.items()
                    if p["funcao"] == function and p["assento"] == seat
                ),
                None,
            )
            assigned = choose(
                function + " — " + seat,
                list(current),
                default,
                key="office" + function + seat,
                format_func=lambda i: current[i]["nome"],
                optional=True,
            )
            assignments.append((function, seat, assigned))
        if st.button("Salvar ocupantes das funções"):
            try:
                store.assign_roles(assignments)
                st.success("Funções atualizadas.")
            except Exception as exc:
                error(exc)
        people = [p for p in store.catalog("procuradores") if p["ativo"]]
        mp = {p["id"]: p for p in people}
        signer = choose(
            "Signatário padrão",
            [0, *mp],
            int(settings.get("signer_id", "0")),
            key="default_signer",
            format_func=lambda i: (
                "Procurador-Geral ativo do cadastro" if i == 0 else mp[i]["nome"]
            ),
        )
        if st.button("Salvar signatário padrão"):
            store.configure(signer_id=signer)
            st.success("Signatário padrão salvo.")
    with st.expander("Motivos de afastamento"):
        reasons = store.catalog("motivos_afastamento")
        rm = {r["id"]: r for r in reasons}
        selected = choose(
            "Motivo para editar",
            [0, *rm],
            0,
            key="reason_pick",
            format_func=lambda i: "Novo motivo" if not i else rm[i]["nome"],
        )
        with st.form(f"reason_edit_{selected}"):
            name = st.text_input("Nome do motivo", rm.get(selected, {}).get("nome", ""))
            wording = st.text_area(
                "Redação do motivo",
                rm.get(selected, {}).get("texto", ""),
                help="Inclua a expressão completa, por exemplo: por motivo de gozo de férias regulamentares. Use {do_titular} para ajustar da titular/do titular.",
            )
            if st.form_submit_button("Salvar motivo"):
                try:
                    store.save_reason(name, wording, selected or None)
                    st.success("Motivo salvo.")
                except Exception as exc:
                    error(exc)
    with st.expander("Bases legais e notas de rodapé"):
        bases = {b["funcao"]: b for b in store.catalog("bases_legais")}
        function = choose(
            "Função da base",
            [x["nome"] for x in store.catalog("funcoes")],
            key="basis_function",
        )
        other = (
            st.text_input("Outra função", key="basis_other")
            if function == "Outro"
            else function
        )
        with st.form(f"basis_{function}"):
            wording = st.text_area("Base legal", bases.get(other, {}).get("texto", ""))
            note = st.text_area("Nota de rodapé", bases.get(other, {}).get("nota", ""))
            st.caption(
                "Configuração inicial: RN-TC nº 07/2024. Confira a base aplicável antes de emitir."
            )
            if st.form_submit_button("Salvar base legal"):
                try:
                    store.save_basis(other, wording, note)
                    st.success("Base legal salva.")
                except Exception as exc:
                    error(exc)
    with st.expander("Backup local"):
        st.code(store.location)
        if st.button("Criar backup do banco"):
            try:
                path = (
                    store.path.parent / f"backup_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
                )
                path = store.backup(path)
                st.success(f"Backup criado: {path}")
            except Exception as exc:
                error(exc)


def download_record(identifier, prefix="record"):
    record = store.get(identifier)
    if record["status"] == "Cancelada":
        st.warning(
            "Ato cancelado. Os arquivos abaixo são os originais da emissão e não representam um ato vigente."
        )
    a, b = st.columns(2)
    for extension, col in [("docx", a), ("pdf", b)]:
        with col:
            if st.button("GERAR " + extension.upper(), key=prefix + extension):
                try:
                    with st.spinner("Preparando documento…"):
                        path, content = export_record(
                            store, identifier, extension, regenerate=True
                        )
                    record[extension] = content
                    del content
                    st.success(f"Arquivo salvo: {path}")
                except Exception as exc:
                    error(exc)
            content = record[extension]
            if content:
                if extension == "docx":
                    assert_docx_clean(content)
                from services.exports import filename

                st.download_button(
                    "Baixar " + extension.upper(),
                    content,
                    file_name=filename(record["payload"], record["numero"], extension),
                    key=prefix + "download" + extension,
                )


def exclusion_register():
    with st.expander("Registro de exclusões"):
        entries = store.deletion_history()
        if not entries:
            st.caption("Nenhuma exclusão registrada.")
        else:
            st.dataframe(
                [
                    {
                        "Data/Hora": r["data_hora"],
                        "Portaria": f"{r['numero'] or 'Rascunho'}/{r['ano']}",
                        "Status anterior": r["status_anterior"],
                        "Motivo da exclusão": r["motivo"],
                    }
                    for r in entries
                ],
                hide_index=True,
                width="stretch",
            )
            st.caption(
                "O registro administrativo não ocupa numeração. Não há restauração automática nesta versão."
            )
            with store.connection() as c:
                files = [
                    dict(r)
                    for r in c.execute(
                        "SELECT a.numero,a.ano,f.caminho,f.quarentena,f.estado,f.detalhe FROM audit_arquivos f JOIN audit_log a ON a.id=f.audit_id ORDER BY f.id DESC"
                    )
                ]
            if files:
                st.dataframe(files, hide_index=True, width="stretch")
            st.caption(
                "Somente exportações com caminho registrado são movimentadas. Arquivos antigos sem vínculo são preservados. Confira eventuais arquivos mantidos ou pendentes acima."
            )


def delete_from_history(identifier, key, draft=False):
    # Complete the action before Streamlit renders the new list; no stale widgets
    # from the deleted record survive a mid-render rerun.
    try:
        if draft:
            result = store.delete_draft(
                identifier, st.session_state.get(key + "draft", False)
            )
        else:
            reason = st.session_state.get(key + "reason", "")
            if reason == "Outro":
                reason = st.session_state.get(key + "other", "")
            result = store.delete_portaria(
                identifier,
                reason,
                st.session_state.get(key + "confirmed", False),
                st.session_state.get(key + "typed", ""),
                st.session_state.get(key + "files", True),
            )
        reset_editor()
        st.session_state["history_message"] = result["message"]
    except Exception as exc:
        logging.exception("Exclusão administrativa não concluída")
        st.session_state["history_error"] = (
            str(exc)
            if isinstance(exc, ValueError)
            else "Não foi possível concluir a exclusão. Consulte o log local."
        )


def delete_controls(r):
    identifier = r["id"]
    key = "delete_" + identifier
    st.divider()
    st.markdown("**Área administrativa — ação destrutiva**")
    if r["status"] == "Rascunho":
        with st.expander("Excluir rascunho"):
            confirmed = st.checkbox(
                "Confirmo a exclusão deste rascunho", key=key + "draft"
            )
            st.button(
                "Excluir rascunho",
                key=key + "draft_button",
                disabled=not confirmed,
                on_click=delete_from_history,
                args=(identifier, key, True),
            )
        return
    st.caption(
        "Cancelar mantém o número ocupado. Excluir remove o registro ativo e pode liberar o número quando for o último da sequência."
    )
    if st.button("Excluir Portaria definitivamente", key=key + "open"):
        st.session_state[key + "confirm_open"] = True
    if not st.session_state.get(key + "confirm_open"):
        return
    with st.container(border=True):
        st.warning(
            "Esta operação excluirá definitivamente esta Portaria do cadastro ativo do sistema. Utilize-a apenas para testes, lançamentos incorretos ou atos que não chegaram a ser oficialmente emitidos."
        )
        st.markdown(f"**PORTARIA – PROGE N.º {r['numero']}/{r['ano']}**")
        st.write("Data:", r["payload"]["data"], "— Status:", r["status"])
        for sub in r["payload"]["substituicoes"]:
            st.write("Titular:", (sub.get("titular") or {}).get("nome", ""))
            st.write("Substituto:", (sub.get("substituto") or {}).get("nome", ""))
        reason = st.selectbox("Motivo da exclusão", REASONS, key=key + "reason")
        if reason == "Outro":
            reason = st.text_input("Descreva o motivo da exclusão", key=key + "other")
        files = st.checkbox(
            "Excluir também os arquivos DOCX/PDF associados",
            value=True,
            key=key + "files",
        )
        st.caption(
            "Os arquivos vinculados serão enviados à quarentena. Arquivos antigos sem caminho registrado serão preservados."
        )
        confirmed = st.checkbox(
            "Confirmo que esta Portaria pode ser excluída definitivamente.",
            key=key + "confirmed",
        )
        typed = st.text_input("Digite EXCLUIR para confirmar", key=key + "typed")
        st.button(
            "EXCLUIR DEFINITIVAMENTE",
            type="primary",
            key=key + "execute",
            disabled=not (confirmed and typed == "EXCLUIR" and reason.strip()),
            on_click=delete_from_history,
            args=(identifier, key),
        )


def history():
    st.subheader("Histórico")
    notice = st.empty()
    if "history_message" in st.session_state:
        notice.success(st.session_state.pop("history_message"))
    if "history_error" in st.session_state:
        notice.error(st.session_state.pop("history_error"))
    exclusion_register()
    records = store.history()
    if not records:
        st.info("Nenhuma Portaria registrada.")
        return
    search = st.text_input("Pesquisar por nome, motivo ou texto").casefold()
    years = sorted({r["ano"] for r in records}, reverse=True)
    year = choose("Filtrar por ano", ["Todos", *years], key="history_year")
    people = store.catalog("procuradores")
    mp = {p["id"]: p["nome"] for p in people}
    person = choose(
        "Filtrar por Procurador",
        [0, *mp],
        key="history_person",
        format_func=lambda i: "Todos" if not i else mp[i],
    )
    filtered = []
    rows = []
    for r in records:
        p = r["payload"]
        participants = [p["signatario"]["id"]] if p.get("signatario") else []
        participants += [
            s[k]["id"]
            for s in p["substituicoes"]
            for k in ("titular", "substituto")
            if s.get(k)
        ]
        if year != "Todos" and r["ano"] != year:
            continue
        if person and person not in participants:
            continue
        if search and search not in str(p).casefold():
            continue
        filtered.append(r)
        for s in p["substituicoes"]:
            rows.append(
                {
                    "Número": (
                        str(r["numero"]) if r["numero"] is not None else "Rascunho"
                    ),
                    "Ano": r["ano"],
                    "Status": r["status"],
                    "Data": p["data"],
                    "Titular": (s.get("titular") or {}).get("nome", ""),
                    "Função": role(
                        s["funcao"], (s.get("titular") or {}).get("genero", "masculino")
                    ),
                    "Assento": s["assento"],
                    "Substituto": (s.get("substituto") or {}).get("nome", ""),
                    "Período": s["inicio"] + " a " + s["fim"],
                    "Motivo": reason_text(s),
                    "Signatário": (p.get("signatario") or {}).get("nome", ""),
                    "Status": r["status"],
                    "Criação": r["criada"],
                }
            )
    st.dataframe(rows, use_container_width=True, hide_index=True)
    if not filtered:
        return
    rm = {r["id"]: r for r in filtered}
    selected = choose(
        "Abrir Portaria",
        list(rm),
        key="history_open",
        format_func=lambda i: f"{rm[i]['numero'] or 'Rascunho'}/{rm[i]['ano']} · {rm[i]['status']} · {i[:6]}",
    )
    r = rm[selected]
    st.markdown(f"**{r['numero'] or 'Rascunho'}/{r['ano']} — {r['status']}**")
    st.dataframe(
        [row for row in rows if row["Criação"] == r["criada"]],
        hide_index=True,
        width="stretch",
    )
    if r["status"] == "Rascunho":
        if st.button("Continuar rascunho"):
            open_draft(selected)
    else:
        st.text(preview_text(r["payload"], r["numero"]))
        download_record(selected, "h" + selected)
        if st.button("Duplicar como nova Portaria"):
            open_draft(store.duplicate(selected))
        if r["status"] == "Finalizada":
            with st.expander("Cancelar Portaria"):
                why = st.text_input("Motivo do cancelamento")
                confirm = st.checkbox(
                    "Confirmo o cancelamento; o número continuará ocupado"
                )
                if st.button("Cancelar ato"):
                    try:
                        store.cancel(selected, why, confirm)
                        st.session_state["history_message"] = (
                            "Portaria cancelada. O número permanece ocupado."
                        )
                        st.rerun()
                    except Exception as exc:
                        error(exc)
        elif r["status"] == "Cancelada":
            st.warning("Motivo: " + r["cancelamento"])
    delete_controls(r)


def new_portaria():
    st.subheader("Nova Portaria")
    if st.button("Iniciar novo formulário"):
        reset_editor()
        st.rerun()
    if st.session_state.get("last_finalized"):
        identifier = st.session_state["last_finalized"]
        r = store.get(identifier)
        st.success(
            f"Portaria {r['numero']}/{r['ano']} finalizada e preservada no histórico."
        )
        download_record(identifier, "final" + identifier)
        return
    seed = st.session_state.get("editor_seed", {})
    version = st.session_state.get("editor_version", 0)
    prefix = f"e{version}_"
    all_people = store.catalog("procuradores")
    people = {p["id"]: p for p in all_people if p["ativo"]}
    if len(people) < 2:
        st.warning("Cadastre pelo menos dois procuradores ativos.")
        return
    functions = [x["nome"] for x in store.catalog("funcoes")]
    seats = [x["nome"] for x in store.catalog("assentos")]
    reasons = {r["id"]: r for r in store.catalog("motivos_afastamento")}
    bases = {b["funcao"]: b for b in store.catalog("bases_legais")}
    a, b = st.columns(2)
    with a:
        issued = st.date_input(
            "Data da Portaria",
            parsed(seed.get("data", date.today())),
            min_value=date(1000, 1, 1),
            max_value=date(9999, 12, 31),
            key=prefix + "date",
            format="DD/MM/YYYY",
        )
    with b:
        st.metric(
            "Próximo número previsto",
            (
                f"{store.next_number(issued.year)}/{issued.year}"
                if settings.get("sequence_confirmed") == "1"
                else "A confirmar"
            ),
        )
        st.caption("Prévias e rascunhos não consomem número.")
    if settings.get("sequence_confirmed") != "1":
        st.warning(
            "Antes de finalizar, confirme a última Portaria emitida em Configurações."
        )
    default_signer = int(settings.get("signer_id", "0")) or next(
        (i for i, p in people.items() if p["funcao"] == "Procurador-Geral"), None
    )
    signer_id = choose(
        "Signatário",
        list(people),
        (seed.get("signatario") or {}).get("id", default_signer),
        key=prefix + "signer",
        format_func=lambda i: people[i]["nome"],
        optional=True,
    )
    quality = choose(
        "Qualidade do signatário",
        ["Titular", "Em exercício", "Outro"],
        (
            "Outro"
            if seed.get("qualidade_outro")
            else ("Em exercício" if seed.get("em_exercicio") else "Titular")
        ),
        key=prefix + "quality",
    )
    custom_quality = (
        st.text_input(
            "Outra qualidade",
            seed.get("qualidade_outro", ""),
            key=prefix + "other_quality",
        )
        if quality == "Outro"
        else ""
    )
    if signer_id:
        st.caption(
            "Cargo na assinatura: "
            + (custom_quality or role("Procurador-Geral", people[signer_id]["genero"]))
            + (" em exercício" if quality == "Em exercício" else "")
        )
    count_key = prefix + "count"
    if count_key not in st.session_state:
        st.session_state[count_key] = max(1, len(seed.get("substituicoes", [])))
    subs = []
    for i in range(st.session_state[count_key]):
        old = (
            seed.get("substituicoes", [])[i]
            if i < len(seed.get("substituicoes", []))
            else {}
        )
        key = prefix + str(i)
        with st.expander(f"Substituição {i+1}", expanded=True):
            holder_id = choose(
                "Procurador titular",
                list(people),
                (old.get("titular") or {}).get("id"),
                key=key + "holder",
                format_func=lambda j: people[j]["nome"],
                optional=True,
            )
            holder = people.get(holder_id)
            same_holder = holder_id is not None and holder_id == (
                old.get("titular") or {}
            ).get("id")
            holder_key = key + str(holder_id)
            c1, c2 = st.columns(2)
            with c1:
                f_default = (
                    old.get("funcao")
                    if same_holder
                    else (holder or {}).get("funcao", "Procurador")
                )
                function = choose(
                    "Cargo/função do titular",
                    functions,
                    f_default if f_default in functions else "Outro",
                    key=holder_key + "function",
                    format_func=lambda name: role(
                        name, (holder or {}).get("genero", "masculino")
                    ),
                )
                if function == "Outro":
                    function = st.text_input(
                        "Outra função",
                        f_default if f_default not in functions else "",
                        key=holder_key + "otherfunction",
                    )
            with c2:
                seat_default = (
                    old.get("assento")
                    if same_holder
                    else (holder or {}).get("assento", "Não se aplica")
                )
                seat = choose(
                    "Assento",
                    seats,
                    seat_default if seat_default in seats else "Outro",
                    key=holder_key + "seat",
                )
                if seat == "Outro":
                    seat = st.text_input(
                        "Outro assento",
                        seat_default if seat_default not in seats else "",
                        key=holder_key + "otherseat",
                    )
            sub_id = choose(
                "Procurador substituto",
                [j for j in people if j != holder_id],
                (old.get("substituto") or {}).get("id"),
                key=holder_key + "sub",
                format_func=lambda j: people[j]["nome"],
                optional=True,
            )
            same_period = (
                st.checkbox(
                    "Usar o mesmo período da substituição anterior",
                    value=old.get("mesmo_periodo", False),
                    key=key + "same",
                )
                if i
                else False
            )
            if same_period:
                start, end = subs[-1]["inicio"], subs[-1]["fim"]
                st.caption(start + " a " + end)
            else:
                c1, c2 = st.columns(2)
                with c1:
                    start = st.date_input(
                        "Data inicial do afastamento",
                        parsed(old.get("inicio", issued)),
                        min_value=date(1000, 1, 1),
                        max_value=date(9999, 12, 31),
                        key=key + "start",
                        format="DD/MM/YYYY",
                    ).isoformat()
                with c2:
                    end = st.date_input(
                        "Data final do afastamento",
                        parsed(old.get("fim", issued)),
                        min_value=date(1000, 1, 1),
                        max_value=date(9999, 12, 31),
                        key=key + "end",
                        format="DD/MM/YYYY",
                    ).isoformat()
            dependent = (
                st.checkbox(
                    "Decorrente da substituição anterior",
                    value=old.get("decorrente", False),
                    key=key + "dependent",
                )
                if i
                else False
            )
            reason_id = old.get("motivo_id")
            if dependent:
                reason_text = "decorrente da substituição anterior"
            else:
                reason_id = choose(
                    "Motivo do afastamento",
                    [*reasons, 0],
                    (
                        reason_id
                        if reason_id in reasons
                        else 0 if old.get("motivo_texto") else next(iter(reasons), 0)
                    ),
                    key=key + "reason",
                    format_func=lambda j: reasons[j]["nome"] if j else "Outro",
                )
                reason_text = (
                    reasons[reason_id]["texto"]
                    if reason_id
                    else st.text_area(
                        "Redação livre do motivo",
                        old.get("motivo_texto", "") if not old.get("motivo_id") else "",
                        key=key + "reasontext",
                        help="Digite a expressão completa, como ela deve aparecer após o período; por exemplo: por motivo de afastamento legal.",
                    )
                )
            legal_key = holder_key + function
            legal = st.text_area(
                "Base legal sugerida — editável",
                (
                    old.get("base_legal", "")
                    if same_holder and function == old.get("funcao")
                    else bases.get(function, {}).get("texto", "")
                ),
                key=legal_key + "basis",
            )
            note = st.text_area(
                "Nota de rodapé — editável",
                (
                    old.get("nota", "")
                    if same_holder and function == old.get("funcao")
                    else bases.get(function, {}).get("nota", "")
                ),
                key=legal_key + "note",
            )
            subs.append(
                dict(
                    titular=holder,
                    substituto=people.get(sub_id),
                    funcao=function,
                    assento=seat,
                    inicio=start,
                    fim=end,
                    motivo_id=reason_id,
                    motivo_texto=reason_text,
                    decorrente=dependent,
                    mesmo_periodo=same_period,
                    base_legal=legal,
                    nota=note,
                )
            )
    a, b = st.columns(2)
    with a:
        if st.button("+ Adicionar outra substituição"):
            st.session_state[count_key] += 1
            st.rerun()
    with b:
        if st.session_state[count_key] > 1 and st.button("Remover última substituição"):
            st.session_state[count_key] -= 1
            st.rerun()
    payload = dict(
        data=issued.isoformat(),
        signatario=people.get(signer_id),
        em_exercicio=quality == "Em exercício",
        qualidade_outro=custom_quality,
        substituicoes=subs,
    )
    seed_fields = {k: v for k, v in seed.items() if k != "manual"}
    if seed.get("manual") and payload == seed_fields:
        payload["manual"] = deepcopy(seed["manual"])
    a, b = st.columns(2)
    with a:
        if st.button("Salvar rascunho"):
            try:
                identifier = store.save_draft(
                    payload, st.session_state.get("editor_id")
                )
                st.session_state["editor_id"] = identifier
                st.success("Rascunho salvo sem reservar número.")
            except Exception as exc:
                error(exc)
    with b:
        if st.button("Preparar prévia", type="primary"):
            try:
                if quality == "Outro" and not custom_quality.strip():
                    raise ValueError("Informe a qualidade do signatário.")
                validate(payload)
                identifier = store.save_draft(
                    payload, st.session_state.get("editor_id")
                )
                st.session_state["editor_id"] = identifier
                st.session_state["preview"] = deepcopy(payload)
                st.session_state["preview_revision"] = (
                    st.session_state.get("preview_revision", 0) + 1
                )
            except Exception as exc:
                error(exc)
    if "preview" not in st.session_state:
        return
    st.divider()
    st.subheader("Prévia da Portaria")
    p = deepcopy(st.session_state["preview"])
    stale = payload != st.session_state["preview"]
    if stale:
        st.warning(
            "Os campos foram alterados. Clique em Preparar prévia para atualizar antes de finalizar."
        )
    preview_key = f"preview_{st.session_state.get('preview_revision',0)}_"
    automatic = compose(p)
    manual = st.checkbox(
        "Editar excepcionalmente o texto desta Portaria",
        value=bool(p.get("manual")),
        key=preview_key + "manual",
    )
    if manual:
        intro = st.text_area("Preâmbulo", automatic["intro"], key=preview_key + "intro")
        body = st.text_area(
            "Texto das substituições",
            "\n\n".join(automatic["body"]),
            height=260,
            key=preview_key + "body",
            help="Separe parágrafos por uma linha em branco.",
        )
        name = st.text_input(
            "Nome na assinatura", automatic["signature_name"], key=preview_key + "name"
        )
        signature = st.text_input(
            "Cargo na assinatura",
            automatic["signature_role"],
            key=preview_key + "signature",
        )
        notes = st.text_area(
            "Notas de rodapé",
            "\n\n".join(automatic["notes"]),
            key=preview_key + "notes",
        )
        p["manual"] = dict(
            intro=intro,
            body=body.split("\n\n"),
            signature_name=name.upper(),
            signature_role=signature,
            notes=[n for n in notes.split("\n\n") if n.strip()],
        )
    if st.button("Restaurar texto automático"):
        st.session_state["preview"].pop("manual", None)
        st.session_state.get("editor_seed", {}).pop("manual", None)
        st.session_state["preview_revision"] += 1
        st.rerun()
    number = store.next_number(issued.year)
    administrative = None
    if settings.get("admin_number") == "1" and st.checkbox(
        "Usar número administrativo", key=preview_key + "admin"
    ):
        administrative = int(
            st.number_input(
                "Número administrativo",
                min_value=number,
                value=number,
                key=preview_key + "number",
            )
        )
        number = administrative
    st.text(preview_text(p, number))
    st.caption(
        "Número previsto; a reserva só ocorre na finalização. Confira os dados e a redação antes de finalizar o ato."
    )
    warnings = warnings_for(
        p, [r["payload"] for r in store.history() if r["status"] == "Finalizada"]
    )
    for warning in warnings:
        st.warning(warning)
    acknowledged = (
        st.checkbox(
            "Conferi os avisos e confirmo a situação informada",
            key=preview_key + "warnings",
        )
        if warnings
        else True
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Gerar prévia DOCX", disabled=stale):
            try:
                data = generate(p)
                st.download_button(
                    "Baixar prévia DOCX", data, file_name="Previa_Portaria_PROGE.docx"
                )
            except Exception as exc:
                error(exc)
    with c2:
        if st.button("Gerar prévia PDF", disabled=stale):
            try:
                with st.spinner("Convertendo a prévia…"):
                    data, engine = convert(
                        generate(p), settings.get("pdf_engine", "auto")
                    )
                st.download_button(
                    "Baixar prévia PDF", data, file_name="Previa_Portaria_PROGE.pdf"
                )
            except Exception as exc:
                error(exc)
    with c3:
        if st.button("Salvar edição da prévia", disabled=stale):
            try:
                store.save_draft(p, st.session_state["editor_id"])
                st.success("Texto salvo no rascunho.")
            except Exception as exc:
                error(exc)
    if st.button(
        "FINALIZAR PORTARIA", type="primary", disabled=stale or not acknowledged
    ):
        try:
            validate(p)
            identifier = st.session_state["editor_id"]
            store.save_draft(p, identifier)
            store.finalize(identifier, administrative, acknowledged)
            st.session_state["last_finalized"] = identifier
            try:
                export_record(store, identifier, "docx")
            except OSError:
                logging.exception("Ato salvo; exportação pendente no histórico")
                st.warning(
                    "A Portaria foi salva no banco. A exportação está pendente; tente novamente no Histórico."
                )
            record = store.get(identifier)
            st.success(
                f"Portaria {record['numero']}/{record['ano']} finalizada. Consulte o Histórico para baixar os arquivos."
            )
        except Exception as exc:
            error(exc)


with st.sidebar:
    st.image(str(ROOT / "assets/logo.jpeg"), width=110)
    st.markdown("**MPC-PB**  \nProcuradoria-Geral")
    menu = st.radio(
        "Navegação",
        ["Nova Portaria", "Histórico", "Procuradores", "Configurações"],
        key="nav",
    )
    st.divider()
    st.caption("Gerador de Portarias PROGE\n\nAplicação local · v" + VERSION)
st.title("MPC-PB")
st.markdown("### Gerador de Portarias PROGE")
st.caption("Ministério Público de Contas do Estado da Paraíba")
try:
    if menu == "Nova Portaria":
        new_portaria()
    elif menu == "Histórico":
        history()
    elif menu == "Configurações":
        configuration()
    else:
        st.subheader("Procuradores")
        st.dataframe(
            [
                {
                    "Nome": p["nome"],
                    "Gênero gramatical": p["genero"],
                    "Cargo base": role(p["cargo_base"], p["genero"]),
                    "Função atual": role(p["funcao"], p["genero"]),
                    "Assento": p["assento"],
                    "Ativo": "Sim" if p["ativo"] else "Não",
                    "Observações": p["observacoes"],
                }
                for p in store.catalog("procuradores")
            ],
            hide_index=True,
            use_container_width=True,
        )
        member_editor("members")
except Exception as exc:
    error(exc)
