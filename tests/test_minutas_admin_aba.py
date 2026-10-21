"""Administração do Minutas IA concentrada em Administração → Minutas IA."""

import pytest

from services.minutas_acervo import MinutasAcervoService
from services.minutas_ia_ui import rotulo_procurador_administracao
from services.minutas_procuradores import listar_ativos
from services.minutas_specialties import (
    CAMARAS,
    PREFEITURAS,
    RECURSOS,
    RPPS,
    SECRETARIAS_MUNICIPAIS,
)
from tests.access_testing import TEST_IDENTITY
from tests.test_minutas_specialties import library, seed


def _ambiente(tmp_path):
    import re
    from pathlib import Path

    from tests.test_minutas_specialties import CATALOG, Storage, build_store

    store = build_store(tmp_path / "minutas.db")
    with store.connection() as conexao:
        sql = (Path(__file__).resolve().parents[1] / "database/minutas_especialidades.sql").read_text(encoding="utf-8")
        sql = re.sub(r"--[^\n]*", "", sql).replace("mpc_portarias.", "").replace("::text", "")
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement.startswith(("CREATE TABLE", "CREATE INDEX", "CREATE OR REPLACE VIEW")):
                conexao.execute(statement.replace("CREATE OR REPLACE VIEW", "CREATE VIEW"))
        for profile in CATALOG:
            conexao.execute(
                "INSERT INTO minutas_especialidades(id,procuradora_chave,grupo,nome,perfil,estado,motor) VALUES(?,?,?,?,?,?,?)",
                tuple(profile[key] for key in ("id", "procuradora_chave", "grupo", "nome", "perfil", "estado", "motor")),
            )
    return store, Storage()


class _Gestor:
    id = 1
    ativo = True
    pode_admin = True
    administrator = True
    perfil = "ADMINISTRADOR"
    email = TEST_IDENTITY["email"]
    nome = "Administrador de Teste"
    categoria_acesso = "INSTITUCIONAL"
    _store = None


def _chaves(app):
    chaves = []
    for nome in (
        "button",
        "selectbox",
        "radio",
        "checkbox",
        "text_input",
        "text_area",
        "number_input",
        "download_button",
    ):
        for item in app.get(nome):
            chave = getattr(item, "key", None)
            if chave:
                chaves.append(chave)
    return chaves


def _sem_erro(app):
    assert not app.exception, app.exception
    assert not app.error, [item.value for item in app.error]


def _servico(store, storage, chamadas):
    def servico(store_arg, identity, *, administrative=False, procurador=None, principal=None):
        from services.minutas_procuradores import exigir_procurador

        pessoa = exigir_procurador(procurador)
        chamadas.append(pessoa.sigla)
        built = library(store, storage, None, principal=principal)
        built.procurador = pessoa
        built.repository.procuradora_chave = pessoa.chave
        return built

    return servico


def _abrir(monkeypatch, store, storage, *, ator=None, operacional=None, secao="Minutas IA"):
    from streamlit.testing.v1 import AppTest

    chamadas = []
    monkeypatch.setattr(
        "services.access_requests.count_pending_access_requests",
        lambda store: 0,
    )
    monkeypatch.setattr(
        "services.minutas_ia_ui.servico_minutas",
        _servico(store, storage, chamadas),
    )
    monkeypatch.setattr(
        "services.minutas_ia_ui._contexto_teste",
        {"ator": ator or _Gestor(), "operacional": operacional, "secao": secao},
        raising=False,
    )

    def page():
        import streamlit as st

        from services.access_ui import render
        from services.minutas_ia_ui import _contexto_teste
        from tests.access_testing import TEST_IDENTITY

        ator_atual = _contexto_teste["ator"]
        st.session_state["_audit_actor"] = ator_atual
        st.session_state["_audit_identity"] = dict(TEST_IDENTITY)
        st.session_state["admin_secao"] = _contexto_teste["secao"]
        operacional_atual = _contexto_teste["operacional"]
        if operacional_atual is not None and "minutas_procurador" not in st.session_state:
            st.session_state["minutas_procurador"] = operacional_atual
        forjado = _contexto_teste.get("forjar")
        if forjado:
            st.session_state["admin_minutas_procurador"] = forjado
            _contexto_teste["forjar"] = None
        render(None, ator_atual)

    app = AppTest.from_function(page, default_timeout=30).run()
    return app, chamadas


def test_aba_lista_procuradores_preserva_painel_e_isola_o_estado(tmp_path, monkeypatch):
    from services.access_ui import ADMIN_SECTIONS

    store, storage = _ambiente(tmp_path)
    seed(store)
    seed(store, process="02720/25", subtype="Prefeitura", entity="Prefeitura Municipal de Exemplo")
    MinutasAcervoService(library(store, storage)).apply(
        MinutasAcervoService(library(store, storage)).dry_run(),
        {"identificador": "teste", "restauracao_verificada": True},
    )
    app, chamadas = _abrir(monkeypatch, store, storage)
    _sem_erro(app)
    assert app.radio(key="admin_secao").options == list(ADMIN_SECTIONS)
    assert app.radio(key="admin_secao").value == "Minutas IA"
    assert not any(item.label == "Administração do módulo" for item in app.button)
    assert "minutas_procurador" not in app.session_state

    seletor = app.selectbox(key="admin_minutas_procurador")
    pessoas = listar_ativos()
    assert seletor.options == [rotulo_procurador_administracao(pessoa) for pessoa in pessoas]
    assert seletor.value == "ESPO"
    assert seletor.format_func("ESPO") == "Dra. Elvira Samara Pereira de Oliveira"
    assert seletor.format_func("BLTC") == "Dr. Bradson Tibério Luna Camelo"
    assert any("ESPO · Elvira Samara Pereira de Oliveira" in item.value for item in app.caption)
    assert chamadas == ["ESPO"]

    operacao = next(item for item in app.selectbox if item.label == "Administração")
    assert list(operacao.options) == [
        "Painel de especialidades",
        "Inventário e reclassificação",
        "Evolução das especialidades",
    ]
    panel = app.dataframe[0].value
    technical = app.dataframe[1].value
    assert list(panel.columns) == [
        "Especialidade", "ID", "Estado", "Geração", "Processos", "Pareceres",
        "Relatórios", "Documentos indexados",
    ]
    assert len(panel) == 9
    assert set(panel["ID"]) == {
        PREFEITURAS, CAMARAS, RPPS, SECRETARIAS_MUNICIPAIS,
        "elvira.pca.orgaos_estaduais", "elvira.denuncias",
        "elvira.pessoal.aposentadorias", "elvira.pessoal.pensoes",
        RECURSOS,
    }
    assert len(panel) + len(technical) == 23
    assert panel.loc[panel["ID"] == PREFEITURAS, "Especialidade"].tolist() == ["Prefeituras Municipais"]
    assert not any(frame["ID"].isin(["elvira.pca.governo", "elvira.pca.gestao"]).any() for frame in (panel, technical))
    assert any("Catálogo técnico preservado: 23" in caption.value for caption in app.caption)
    assert any("A iniciar" in caption.value for caption in app.caption)
    assert len(_chaves(app)) == len(set(_chaves(app)))

    app.selectbox(key="admin_minutas_operacao_espo").set_value("Evolução das especialidades").run()
    _sem_erro(app)
    assert any(item.label == "Registrar evolução" for item in app.button)
    assert any(item.label == "Especialidade técnica" for item in app.selectbox)
    app.selectbox(key="admin_minutas_operacao_espo").set_value("Painel de especialidades").run()
    _sem_erro(app)

    for pessoa in pessoas:
        app.selectbox(key="admin_minutas_procurador").set_value(pessoa.sigla).run()
        _sem_erro(app)
        assert app.selectbox(key="admin_minutas_procurador").value == pessoa.sigla
        assert chamadas[-1] == pessoa.sigla
        assert any(f"{pessoa.sigla} · {pessoa.nome}" in item.value for item in app.caption)
        identificadores = list(app.dataframe[0].value["ID"])
        assert identificadores
        assert all(str(item).startswith(pessoa.prefixo + ".") for item in identificadores)
        assert len(_chaves(app)) == len(set(_chaves(app)))
        assert "minutas_procurador" not in app.session_state

    app.selectbox(key="admin_minutas_procurador").set_value("ESPO").run()
    app.selectbox(key="admin_minutas_operacao_espo").set_value("Inventário e reclassificação").run()
    _sem_erro(app)
    app.button(key="admin_minutas_simular_espo").click().run()
    _sem_erro(app)
    assert "admin_minutas_plano_espo" in app.session_state
    app.text_input(key="admin_minutas_backup_espo").set_value("evidencia-espo").run()
    app.checkbox(key="admin_minutas_backup_ok_espo").set_value(True).run()
    app.session_state["minutas_procurador"] = "LAF"
    app.selectbox(key="admin_minutas_procurador").set_value("MTTF").run()
    _sem_erro(app)
    assert app.session_state["minutas_procurador"] == "LAF"
    assert app.selectbox(key="admin_minutas_procurador").value == "MTTF"
    assert chamadas[-1] == "MTTF"
    assert all(str(item).startswith("mttf.") for item in app.dataframe[0].value["ID"])
    assert not any(str(item).startswith("elvira.") for item in app.dataframe[0].value["ID"])
    assert "admin_minutas_plano_espo" in app.session_state
    assert "admin_minutas_plano_mttf" not in app.session_state
    assert not any(item.label == "Baixar relatório da simulação" for item in app.get("download_button"))

    app.selectbox(key="admin_minutas_operacao_mttf").set_value("Inventário e reclassificação").run()
    _sem_erro(app)
    assert not any(getattr(item, "key", None) == "admin_minutas_backup_mttf" for item in app.get("text_input"))
    assert all(item.value != "evidencia-espo" for item in app.get("text_input"))
    app.button(key="admin_minutas_simular_mttf").click().run()
    _sem_erro(app)
    assert app.text_input(key="admin_minutas_backup_mttf").value == ""
    assert app.checkbox(key="admin_minutas_backup_ok_mttf").value is False
    assert app.session_state["admin_minutas_plano_mttf"] != app.session_state["admin_minutas_plano_espo"]
    assert "admin_minutas_backup_espo" not in app.session_state

    app.selectbox(key="admin_minutas_procurador").set_value("ESPO").run()
    _sem_erro(app)
    assert app.session_state["minutas_procurador"] == "LAF"
    assert "admin_minutas_plano_espo" in app.session_state
    app.selectbox(key="admin_minutas_operacao_espo").set_value("Inventário e reclassificação").run()
    _sem_erro(app)
    assert any(item.label == "Baixar relatório da simulação" for item in app.get("download_button"))
    assert app.text_input(key="admin_minutas_backup_espo").value == ""
    assert app.checkbox(key="admin_minutas_backup_ok_espo").value is False

    import services.minutas_ia_ui as minutas_ia_ui

    minutas_ia_ui._contexto_teste["forjar"] = "INVALIDO"
    app.run()
    _sem_erro(app)
    escolha = app.selectbox(key="admin_minutas_procurador").value
    pessoa = next(item for item in pessoas if item.sigla == escolha)
    assert any(f"{pessoa.sigla} · {pessoa.nome}" in item.value for item in app.caption)
    assert all(str(item).startswith(pessoa.prefixo + ".") for item in app.dataframe[0].value["ID"])
    assert app.session_state["minutas_procurador"] == "LAF"


def test_seletor_inicial_acompanha_o_gabinete_sem_grava_lo(tmp_path, monkeypatch):
    store, storage = _ambiente(tmp_path)
    app, chamadas = _abrir(monkeypatch, store, storage, operacional="BLTC")
    _sem_erro(app)
    assert app.selectbox(key="admin_minutas_procurador").value == "BLTC"
    assert chamadas == ["BLTC"]
    assert any("Bradson Tibério Luna Camelo" in item.value for item in app.caption)
    app.selectbox(key="admin_minutas_procurador").set_value("SBBQ").run()
    _sem_erro(app)
    assert app.selectbox(key="admin_minutas_procurador").value == "SBBQ"
    assert app.session_state["minutas_procurador"] == "BLTC"
    assert all(str(item).startswith("sbbq.") for item in app.dataframe[0].value["ID"])


def test_tela_do_procurador_ignora_o_atalho_antigo(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    store, storage = _ambiente(tmp_path)

    def servico(*args, administrative=False, **kwargs):
        import streamlit as st

        return library(
            store,
            storage,
            None if administrative else st.session_state.get("minutas_specialty"),
            principal=st.session_state.get("_audit_actor"),
        )

    monkeypatch.setattr("services.minutas_ia_ui._service", servico)

    def page():
        import streamlit as st

        from services.minutas_ia_ui import render
        from tests.access_testing import TEST_IDENTITY
        from tests.test_minutas_admin_aba import _Gestor

        st.session_state["minutas_procurador"] = "ESPO"
        st.session_state["minutas_initial_administration"] = True
        st.session_state["_audit_actor"] = _Gestor()
        render(None, TEST_IDENTITY)

    app = AppTest.from_function(page, default_timeout=30).run()
    _sem_erro(app)
    assert "minutas_initial_administration" not in app.session_state
    assert not any(item.key == "minutas_initial_administration_open" for item in app.button)
    assert not any(item.label == "Administração do módulo" for item in app.button)
    assert not any(item.label == "Administração" for item in app.selectbox)
    assert any(item.key == "minutas_category_PCA" for item in app.button)


@pytest.mark.parametrize(
    "ator",
    (
        type("Comum", (), {
            "ativo": True,
            "pode_admin": False,
            "email": "comum@test.local",
            "categoria_acesso": "INSTITUCIONAL",
            "_store": None,
        })(),
        type("Inativo", (), {
            "ativo": False,
            "pode_admin": True,
            "email": "inativo@test.local",
            "categoria_acesso": "INSTITUCIONAL",
            "_store": None,
        })(),
        type("Estagiario", (), {
            "ativo": True,
            "pode_admin": True,
            "email": "estagio@test.local",
            "categoria_acesso": "ESTAGIARIO",
            "estagio_vigente": True,
            "minutas_gabinetes": ("ESPO",),
            "_store": None,
        })(),
    ),
)
def test_sem_privilegio_nao_abre_a_aba_mesmo_com_estado_forjado(monkeypatch, ator):
    from streamlit.testing.v1 import AppTest

    chamadas = []

    def servico(*args, **kwargs):
        chamadas.append(kwargs)
        raise AssertionError("acervo administrativo não deve ser carregado")

    monkeypatch.setattr("services.minutas_ia_ui.servico_minutas", servico)
    monkeypatch.setattr("services.minutas_ia_ui._contexto_teste", ator, raising=False)

    def page():
        import streamlit as st

        from services.minutas_ia_ui import _contexto_teste, render_administracao

        st.session_state["admin_secao"] = "Minutas IA"
        st.session_state["admin_minutas_procurador"] = "MTTF"
        st.session_state["minutas_initial_administration"] = True
        render_administracao(None, _contexto_teste)

    app = AppTest.from_function(page, default_timeout=30).run()
    assert not app.exception, app.exception
    assert chamadas == []
    assert any("restrita" in item.value for item in app.error)
    assert not any(getattr(item, "key", None) == "admin_minutas_procurador" for item in app.selectbox)


def test_usuario_comum_nao_entra_no_modulo_administracao(store):
    from services.access import resolve_principal
    from services.access_ui import render
    from tests.access_testing import seed_access

    seed_access(
        store,
        email="comum.minutas.admin@test.local",
        perfil="USUARIO",
        pode_admin=False,
    )
    principal = resolve_principal(store, {"email": "comum.minutas.admin@test.local"})
    with pytest.raises(ValueError, match="módulo"):
        render(store, principal)
