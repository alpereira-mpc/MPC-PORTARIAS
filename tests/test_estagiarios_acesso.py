"""Acesso de estagiário: Conta Google pessoal, vínculo e Minutas IA."""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from database.access import PROTECTED_ADMIN_EMAIL, PROTECTED_ADMIN_MESSAGE, AccessStore
from database.estagiarios import EstagiariosStore
from database.memorandos import MemorandosStore
from portal import portal_navigation
from services.access import has_permission, require_permission, resolve_principal, confirm_principal
from services.access_requests import (
    SUCCESS_BODY,
    SUCCESS_BODY_ESTAGIARIO,
    approve_access_request,
    autorizar_estagiario,
    submit_access_request,
)
from services.estagiarios_acesso import TIPO_ESTAGIARIO, hoje_institucional, vinculo_vigente
from services.minutas_docx import MinutasDocxError, MinutasDocxService
from services.minutas_library import MinutasAccessDenied, MinutasLibraryService
from services.minutas_procuradores import camadas_prompt, exigir_procurador, resolver_configuracao
from services.minutas_search import MinutasSearchService
from services.minutas_specialties import APOSENTADORIAS, RPPS
from services.search import global_search
from tests.access_testing import seed_access


def _pessoa(store, nome, matricula):
    base = MemorandosStore(store)
    base.import_servers(
        [{"nome": nome, "matricula": matricula, "cargo": "Estagiária", "setor": "PROGE"}],
        actor_email="admin@test",
        filename=matricula + ".xlsx",
        content_hash=matricula,
        administrator=True,
    )
    return next(row["id"] for row in base.all_servers() if row["nome"] == nome)


def _vinculo(store, nome="Ana Estágio", matricula="501", lotacao="BLTC", inicio=None):
    pessoa = _pessoa(store, nome, matricula)
    inicio = inicio or date(2026, 1, 5)
    return EstagiariosStore(store).create(
        pessoa, lotacao, inicio, actor_email="admin@test", administrator=True
    )


def _identidade(email, verified=True):
    return {"email": email, "name": "Ana Estágio", "email_verified": verified}


def _liberar(store, lotacao_id, email="ana.estagio@gmail.com", extras=("MTTF",), **extra):
    payload = {
        "nome": "Ana Estágio",
        "email": email,
        "perfil": "ADMINISTRADOR",
        "ativo": True,
        "pode_admin": True,
        "pode_portarias": True,
        "pode_oficios": True,
        "categoria_acesso": "ESTAGIARIO",
        "estagio_lotacao_id": lotacao_id,
        "minutas_gabinetes": list(extras),
        "gabinetes": ["PROGE", "BTLC"],
    }
    payload.update(extra)
    existente = AccessStore(store).get_by_email(payload["email"])
    return AccessStore(store).save_user(
        payload, existente["id"] if existente else None
    )


def _principal(store, email="ana.estagio@gmail.com", verified=True):
    return resolve_principal(store, _identidade(email, verified))


def test_servidor_e_administrador_institucionais_permanecem(store):
    usuario = seed_access(
        store,
        email="servidor@tce.pb.gov.br",
        nome="Servidor",
        perfil="USUARIO",
        pode_portarias=True,
        pode_agenda=False,
        pode_oficios=False,
        pode_admin=False,
        gabinetes=[],
    )
    admin = seed_access(
        store,
        email="gestor@tce.pb.gov.br",
        nome="Gestor",
        perfil="ADMINISTRADOR",
    )
    servidor = resolve_principal(store, {"email": "servidor@tce.pb.gov.br", "email_verified": True})
    gestor = resolve_principal(store, {"email": "gestor@tce.pb.gov.br", "email_verified": None})
    assert usuario and admin
    assert servidor.categoria_acesso == "INSTITUCIONAL"
    assert servidor.pode_portarias is True
    assert has_permission(servidor, "portarias") is True
    assert has_permission(servidor, "tarefas") is True
    assert has_permission(servidor, "busca") is True
    assert "Início" in portal_navigation(servidor, {"email": servidor.email})
    assert "Busca Global" in portal_navigation(servidor, {"email": servidor.email})
    assert "Tarefas" in portal_navigation(servidor, {"email": servidor.email})
    assert gestor.administrator is True
    assert gestor.pode_admin is True
    assert has_permission(gestor, "admin") is True
    assert "Administração" in portal_navigation(gestor, {"email": gestor.email})
    gravado = AccessStore(store).get(usuario)
    assert gravado["categoria_acesso"] == "INSTITUCIONAL"
    assert gravado["pode_portarias"] is True


def test_gmail_sem_cadastro_pendente_ou_sem_usuario_e_recusado(store):
    assert _principal(store) is None
    pedido = submit_access_request(
        store,
        "Ana Estágio",
        "ana.estagio@gmail.com",
        "Bradson Tibério Luna Camelo — BLTC",
        tipo=TIPO_ESTAGIARIO,
    )
    assert pedido.ok is True
    assert pedido.record["tipo_solicitante"] == "ESTAGIARIO"
    assert pedido.message == SUCCESS_BODY_ESTAGIARIO
    assert AccessStore(store).get_by_email("ana.estagio@gmail.com") is None
    assert _principal(store) is None
    approve_access_request(store, pedido.record["id"], "admin@tce.pb.gov.br")
    assert AccessStore(store).get_by_email("ana.estagio@gmail.com") is None
    assert _principal(store) is None


def test_estagiario_valido_entra_somente_no_minutas_do_gabinete(store):
    lotacao = _vinculo(store)
    _liberar(store, lotacao)
    gravado = AccessStore(store).get_by_email("ana.estagio@gmail.com")
    assert gravado["perfil"] == "USUARIO"
    assert gravado["pode_admin"] is False
    assert gravado["pode_portarias"] is False
    assert gravado["pode_oficios"] is False
    assert gravado["gabinetes"] == []
    with store.connection() as conexao:
        conexao.execute(
            "UPDATE usuarios_acesso SET pode_admin=1, pode_portarias=1, perfil='USUARIO' WHERE email=?",
            ("ana.estagio@gmail.com",),
        )
    assert "BLTC" not in gravado["minutas_gabinetes"]
    assert gravado["minutas_gabinetes"] == ["MTTF"]
    principal = _principal(store)
    assert principal is not None
    assert principal.estagio_vigente is True
    assert principal.minutas_gabinetes[0] == "BLTC"
    assert "MTTF" in principal.minutas_gabinetes
    assert has_permission(principal, "minutas") is True
    for modulo in (
        "portarias", "agenda", "oficios", "memorandos", "tarefas", "relatorios",
        "representacoes", "peticoes", "ouvidoria", "admin", "busca", "alertas", "pendencias",
    ):
        assert has_permission(principal, modulo) is False
        with pytest.raises(ValueError, match="não autorizado"):
            require_permission(principal, modulo)
    identidade = _identidade("ana.estagio@gmail.com")
    assert portal_navigation(principal, identidade) == ["Minutas IA"]
    assert global_search(store, principal, "portaria")[2]["status"] == "denied"
    assert _principal(store, "outra.pessoa@gmail.com") is None
    assert _principal(store, verified=None) is None
    assert _principal(store, verified=False) is None


def test_vigencia_desativacao_e_revalidacao_durante_a_sessao(store):
    lotacao = _vinculo(store)
    _liberar(store, lotacao)
    principal = _principal(store)
    antes = AccessStore(store).get_by_email(principal.email)["atualizado_em"]
    EstagiariosStore(store).update(
        lotacao, "SBBQ", date(2026, 2, 1), date(2028, 2, 1),
        actor_email="admin@test", administrator=True,
    )
    assert AccessStore(store).get_by_email(principal.email)["atualizado_em"] == antes
    atualizado = confirm_principal(principal, store)
    assert atualizado is not None
    assert atualizado.minutas_gabinetes[0] == "SBBQ"
    assert "BLTC" not in atualizado.minutas_gabinetes
    assert "MTTF" in atualizado.minutas_gabinetes
    _liberar(store, lotacao, extras=())
    sem_extra = confirm_principal(atualizado, store)
    assert sem_extra.minutas_gabinetes == ("SBBQ",)
    with store.connection() as conexao:
        conexao.execute(
            "UPDATE estagiarios_lotacoes SET data_limite=? WHERE id=?",
            ((hoje_institucional() - timedelta(days=1)).isoformat(), lotacao),
        )
    assert confirm_principal(sem_extra, store) is None
    with store.connection() as conexao:
        conexao.execute(
            "UPDATE estagiarios_lotacoes SET data_limite=? WHERE id=?",
            ("2028-02-01", lotacao),
        )
    restaurado = _principal(store)
    assert restaurado is not None
    EstagiariosStore(store).close(
        lotacao, hoje_institucional(),
        actor_email="admin@test", administrator=True,
    )
    assert confirm_principal(restaurado, store) is None
    outro = _vinculo(store, nome="Bia Estágio", matricula="502", lotacao="LAF")
    _liberar(store, outro, email="bia.estagio@gmail.com", extras=())
    ativo = _principal(store, "bia.estagio@gmail.com")
    _liberar(store, outro, email="bia.estagio@gmail.com", extras=(), ativo=False)
    assert confirm_principal(ativo, store) is None
    with store.connection() as conexao:
        conexao.execute(
            "UPDATE estagiarios_lotacoes SET data_inicio=? WHERE id=?",
            ((hoje_institucional() + timedelta(days=2)).isoformat(), outro),
        )
        conexao.execute(
            "UPDATE usuarios_acesso SET ativo=1 WHERE email=?",
            ("bia.estagio@gmail.com",),
        )
    assert _principal(store, "bia.estagio@gmail.com") is None


def test_data_limite_inclusiva_e_encerramento_imediato():
    hoje = hoje_institucional()
    base = {
        "ativo": 1,
        "data_inicio": (hoje - timedelta(days=10)).isoformat(),
        "data_limite": hoje.isoformat(),
        "lotacao": "BLTC",
    }
    assert vinculo_vigente(base) is True
    assert vinculo_vigente({**base, "data_limite": (hoje - timedelta(days=1)).isoformat()}) is False
    assert vinculo_vigente({**base, "data_inicio": (hoje + timedelta(days=1)).isoformat()}) is False
    assert vinculo_vigente({**base, "data_encerramento": hoje.isoformat()}) is False
    assert vinculo_vigente({**base, "ativo": 0}) is False


def test_virada_do_dia_revalida_sessao_sem_alterar_usuario(store, monkeypatch):
    lotacao = _vinculo(store)
    with store.connection() as conexao:
        conexao.execute(
            "UPDATE estagiarios_lotacoes SET data_limite=? WHERE id=?",
            (hoje_institucional().isoformat(), lotacao),
        )
    _liberar(store, lotacao, extras=())
    principal = _principal(store)
    assert principal is not None
    assert confirm_principal(principal, store) is principal
    amanha = hoje_institucional() + timedelta(days=1)
    monkeypatch.setattr(
        "services.estagiarios_acesso.hoje_institucional", lambda: amanha
    )
    assert confirm_principal(principal, store) is None


def test_solicitacao_institucional_nao_vira_estagiario_nem_cria_usuario(store):
    institucional = submit_access_request(
        store, "Maria Silva", "maria.silva@tce.pb.gov.br", "Procuradoria-Geral — PROGE"
    )
    assert institucional.ok is True
    assert institucional.message == SUCCESS_BODY
    assert institucional.record["tipo_solicitante"] == "INSTITUCIONAL"
    pessoal = submit_access_request(
        store, "Maria Silva", "maria.silva@gmail.com", "Procuradoria-Geral — PROGE"
    )
    assert pessoal.ok is False
    assert "@tce.pb.gov.br" in pessoal.message
    repetido = submit_access_request(
        store, "Maria Silva", "maria.silva@tce.pb.gov.br", "Procuradoria-Geral — PROGE"
    )
    assert repetido.ok is False
    approve_access_request(store, institucional.record["id"], "admin@tce.pb.gov.br")
    assert AccessStore(store).get_by_email("maria.silva@tce.pb.gov.br") is None


def test_autorizacao_associa_estagio_e_bloqueia_elevacao(store):
    lotacao = _vinculo(store)
    pedido = submit_access_request(
        store,
        "Ana Estágio",
        "ana.estagio@gmail.com",
        "BLTC",
        tipo=TIPO_ESTAGIARIO,
    )
    resultado = autorizar_estagiario(
        store, pedido.record["id"], lotacao, "admin@tce.pb.gov.br"
    )
    assert resultado["usuario_id"]
    principal = _principal(store)
    assert principal.pode_admin is False
    assert principal.perfil == "USUARIO"
    assert has_permission(principal, "minutas") is True
    assert has_permission(principal, "admin") is False
    with pytest.raises(ValueError, match=PROTECTED_ADMIN_MESSAGE):
        _liberar(store, lotacao, email=PROTECTED_ADMIN_EMAIL)
    existente = AccessStore(store).get_by_email("servidor.ausente@tce.pb.gov.br")
    assert existente is None
    outro = submit_access_request(
        store, "Carlos Servidor", "servidor@tce.pb.gov.br", "LAF", tipo=TIPO_ESTAGIARIO
    )
    assert outro.ok is True
    seed_access(
        store,
        email="servidor@tce.pb.gov.br",
        nome="Servidor",
        perfil="USUARIO",
        pode_portarias=True,
        pode_admin=False,
        gabinetes=[],
    )
    with pytest.raises(ValueError, match="já possui cadastro"):
        autorizar_estagiario(store, outro.record["id"], lotacao, "admin@tce.pb.gov.br")
    assert AccessStore(store).get_by_email("servidor@tce.pb.gov.br")["categoria_acesso"] == "INSTITUCIONAL"


def test_pesquisa_geracao_e_docx_respeitam_o_gabinete(store):
    lotacao = _vinculo(store)
    _liberar(store, lotacao, extras=())
    principal = _principal(store)
    chamadas = []

    def pesquisar(query, chave, *_args, **_kwargs):
        chamadas.append(chave)
        return [
            {
                "referencia_id": "bltc-1",
                "documento_id": "doc-1",
                "procuradora": "Bradson Tibério Luna Camelo",
                "procuradora_chave": "bltc",
                "numero_processo": "TC 10/26",
                "exercicio": 2026,
                "subtipo": "",
                "arquivo_nome": "parecer.pdf",
                "arquivo_path": "bltc/parecer.pdf",
                "arquivo_sha256": "a" * 64,
                "extrator_versao": "1",
                "atualizado_em": "1",
            },
            {
                "referencia_id": "espo-1",
                "documento_id": "doc-2",
                "procuradora": "Elvira Samara",
                "procuradora_chave": "elvira samara",
                "numero_processo": "TC 99/26",
                "exercicio": 2026,
                "subtipo": "",
                "arquivo_nome": "parecer.pdf",
                "arquivo_path": "espo/parecer.pdf",
                "arquivo_sha256": "b" * 64,
                "extrator_versao": "1",
                "atualizado_em": "1",
            },
        ]

    def biblioteca(sigla):
        repositorio = SimpleNamespace(
            specialty_id=APOSENTADORIAS,
            store=store,
            include_acervo=True,
            search_opinions=pesquisar,
        )
        return MinutasLibraryService(
            repositorio,
            SimpleNamespace(),
            _identidade(principal.email),
            {},
            procurador=sigla,
            principal=principal,
        )

    autorizada = biblioteca("BLTC")
    resultados = MinutasSearchService(autorizada).search("parecer")
    assert chamadas == ["bltc"]
    assert [item["processo"] for item in resultados["resultados"]] == ["TC 10/26"]
    negada = biblioteca("ESPO")
    with pytest.raises(MinutasAccessDenied):
        MinutasSearchService(negada).search("parecer")
    assert chamadas == ["bltc"]
    with pytest.raises(MinutasAccessDenied):
        autorizada.create({}, {}, {})
    with pytest.raises(MinutasDocxError, match="indisponível"):
        MinutasDocxService(autorizada).export("bltc-1", {}, {})
    with pytest.raises(MinutasAccessDenied):
        MinutasDocxService(negada).export("espo-1", {}, {})
    perfil, visual = MinutasDocxService(autorizada)._presentation(
        {"procuradora_chave": "bltc", "procuradora": "Bradson Tibério Luna Camelo"}
    )
    assert perfil["neutro"] is True
    assert visual.get("template") != "elvira"
    assert resolver_configuracao("BLTC")["template"] == "institucional"
    assert "linguagem institucional neutra" in camadas_prompt(exigir_procurador("BLTC"), "Prefeituras")
    assert resolver_configuracao("ESPO")["template"] == "elvira"


def test_estado_antigo_nao_abre_outro_modulo(store):
    import streamlit as st

    lotacao = _vinculo(store)
    _liberar(store, lotacao, extras=())
    principal = _principal(store)
    opcoes = portal_navigation(principal, _identidade(principal.email))
    st.session_state["portal_module"] = "Tarefas"
    st.session_state["portal_nav_request"] = {"module": "Busca Global", "state": {}}
    from portal import PORTAL_NAV_REQUEST, apply_portal_navigation

    st.session_state[PORTAL_NAV_REQUEST] = {"module": "Administração", "state": {}}
    assert apply_portal_navigation(opcoes) is None
    if st.session_state.get("portal_module") not in opcoes:
        st.session_state["portal_module"] = opcoes[0]
    assert st.session_state["portal_module"] == "Minutas IA"
