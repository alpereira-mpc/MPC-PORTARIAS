"""Contratos da orientação obrigatória do Assessor."""

import pytest
from streamlit.testing.v1 import AppTest

from services.minutas_generation import (
    MinutasGenerationError,
    MinutasGenerationService,
)
from services.minutas_orientacao import (
    consulta_da_orientacao,
    contexto_orientacao,
    mesclar_referencias,
    normalizar_orientacao,
    termos_de_consulta,
    trecho_aderente,
)
from tests.test_minutas_generation import (
    Library,
    _analysis_result,
    _draft_result,
    _record,
    _row,
    _scenario,
)


ORIENTACAO_REGRESSAO = (
    "Sustentar a linha argumentativa indicada pelo assessor nesta minuta experimental."
)
ORIENTACAO_REGULAR = (
    "Elaborar parecer pela regularidade das contas com ressalvas, "
    "relevando falhas formais e recomendando providências."
)
ORIENTACAO_IRREGULAR = (
    "Manifestar-se pela irregularidade das contas, com multa e imputação de débito."
)


def _alvo(kwargs):
    return {
        "processo": kwargs["processo"],
        "exercicio": kwargs.get("exercicio"),
        "origem": kwargs.get("origem", ""),
        "beneficiario": kwargs.get("beneficiario", ""),
        "ato": kwargs.get("ato", ""),
        "periodo_fatos": kwargs.get("periodo_fatos", ""),
        "modalidade_recurso": kwargs.get("modalidade_recurso", ""),
        "materia_originaria": kwargs.get("materia_originaria", ""),
        "data_decisao": kwargs.get("data_decisao", ""),
    }


def gerar_instrucao(service, documentos, analyzed, **kwargs):
    texto = kwargs.pop("orientacao_juridica", None) or ORIENTACAO_REGRESSAO
    contexto = kwargs.pop("contexto_orientacao", None)
    usuario = kwargs.pop("usuario", "")
    if contexto is None:
        target = service._resolver_alvo_instrucao(**_alvo(kwargs))
        pacote = service.pacote_orientacao(texto, target, analyzed, usuario)
        texto = pacote["orientacao_juridica"]
        contexto = pacote["contexto_orientacao"]
        usuario = pacote["usuario"]
    return service.generate_instruction(
        documentos,
        analyzed,
        orientacao_juridica=texto,
        contexto_orientacao=contexto,
        usuario=usuario,
        **kwargs,
    )


def test_normalizar_rejeita_vazio_espacos_e_texto_sem_conteudo():
    for value in (None, "", "   \n\t  ", "............", "123456789012", "ok", "a a a a"):
        with pytest.raises(MinutasGenerationError):
            normalizar_orientacao(value)
    with pytest.raises(MinutasGenerationError, match="texto"):
        normalizar_orientacao(12)
    with pytest.raises(MinutasGenerationError, match="6.000"):
        normalizar_orientacao("palavra " * 2000)
    assert "contas" in normalizar_orientacao("  " + ORIENTACAO_REGULAR + "  ")
    assert "\n" in normalizar_orientacao("Primeira tese principal.\n\nTese subsidiária e pedidos.")


def test_contexto_muda_com_procurador_especialidade_documento_e_analise():
    base = dict(
        usuario="Assessor@mpc.pb.gov.br",
        procurador_chave="elvira samara",
        especialidade_id="elvira.pca.rpps",
        fontes_versao="v1",
        analise={"resumo_processual": "Fatos."},
        processo_chave="0333326",
    )
    original = contexto_orientacao(**base)
    assert original == contexto_orientacao(**{**base, "usuario": " assessor@mpc.pb.gov.br "})
    for field, value in (
        ("procurador_chave", "bltc"),
        ("especialidade_id", "elvira.denuncias"),
        ("fontes_versao", "v2"),
        ("processo_chave", "0210826"),
        ("analise", {"resumo_processual": "Outros fatos."}),
        ("usuario", "outro@mpc.pb.gov.br"),
    ):
        assert contexto_orientacao(**{**base, field: value}) != original


def test_consulta_usa_termos_da_tese_e_ignora_comando():
    assert termos_de_consulta(ORIENTACAO_REGRESSAO) == []
    assert consulta_da_orientacao(ORIENTACAO_REGRESSAO) == ""
    consulta = consulta_da_orientacao(ORIENTACAO_REGULAR)
    assert "regularidade" in consulta
    assert "ressalvas" in consulta
    assert "elaborar" not in consulta.casefold()
    assert trecho_aderente(consulta, "Precedente pela regularidade com ressalvas.")
    assert not trecho_aderente(consulta, "Entendimento sobre folha de pagamento.")


def test_mescla_prioriza_aderencia_e_preserva_precedente_anterior():
    anterior = {"referencia_id": "antigo", "trecho": "Irregularidade mantida no acervo."}
    novo = {
        "referencia_id": "novo",
        "trecho": "Regularidade com ressalvas em caso semelhante.",
    }
    mesclado = mesclar_referencias([anterior], [novo])
    assert [item["referencia_id"] for item in mesclado] == ["novo", "antigo"]
    repetido = mesclar_referencias([anterior, novo], [novo])
    assert [item["referencia_id"] for item in repetido] == ["novo", "antigo"]


def test_geracao_historica_bloqueia_sem_orientacao_e_contexto_divergente():
    target, history, other, rows = _scenario()
    calls = []
    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=lambda context: calls.append(context) or _draft_result(context),
    )
    analyzed = service.analyze(target["id"])
    with pytest.raises(MinutasGenerationError, match="Informe a orientação"):
        service.generate(target["id"], analyzed)
    with pytest.raises(MinutasGenerationError, match="não contém conteúdo"):
        service.generate(target["id"], analyzed, orientacao_juridica="   ...   ")
    pacote = service.pacote_orientacao(ORIENTACAO_REGULAR, target, analyzed, "assessor@mpc.pb.gov.br")
    with pytest.raises(MinutasGenerationError, match="não corresponde ao contexto"):
        service.generate(
            target["id"],
            analyzed,
            orientacao_juridica=pacote["orientacao_juridica"],
            contexto_orientacao=pacote["contexto_orientacao"],
            usuario="outro@mpc.pb.gov.br",
        )
    assert calls == []


def test_orientacoes_divergentes_chegam_ao_modelo_e_a_pesquisa():
    target, history, other, rows = _scenario()
    opinion = next(
        row for row in rows
        if row["referencia_id"] == history["id"] and row["papel"] == "parecer"
    )
    estrangeiro = next(
        row for row in rows
        if row["referencia_id"] == other["id"] and row["papel"] == "parecer"
    )
    drafting = []
    queries = []

    def search(query, **_filters):
        queries.append(query)
        return {"resultados": [
            {
                "referencia_id": history["id"],
                "procuradora": history["procuradora"],
                "procuradora_chave": history["procuradora_chave"],
                "processo": history["numero_processo"],
                "processo_chave": history["processo_chave"],
                "documento": opinion["arquivo_nome"],
                "arquivo_path": opinion["arquivo_path"],
                "sha256_registrado": opinion["arquivo_sha256"],
                "fonte_versao": "source-version",
                "pagina": 2,
                "papel": "parecer",
                "trecho": "Entendimento pela regularidade com ressalvas.",
                "contexto": "Entendimento pela regularidade com ressalvas.",
                "relevancia": 2,
            },
            {
                "referencia_id": other["id"],
                "procuradora": other["procuradora"],
                "procuradora_chave": other["procuradora_chave"],
                "processo": other["numero_processo"],
                "processo_chave": other["processo_chave"],
                "documento": estrangeiro["arquivo_nome"],
                "arquivo_path": estrangeiro["arquivo_path"],
                "sha256_registrado": estrangeiro["arquivo_sha256"],
                "papel": "parecer",
                "trecho": "Regularidade com ressalvas de outro gabinete.",
                "contexto": "Regularidade com ressalvas de outro gabinete.",
                "relevancia": 9,
            },
        ]}

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=lambda context: drafting.append(context) or _draft_result(context),
        search=search,
    )
    analyzed = service.analyze(target["id"])
    service.generate(
        target["id"], analyzed, use_rag=True,
        **service.pacote_orientacao(ORIENTACAO_REGULAR, target, analyzed),
    )
    service.generate(
        target["id"], analyzed, use_rag=True,
        **service.pacote_orientacao(ORIENTACAO_IRREGULAR, target, analyzed),
    )

    assert drafting[0]["orientacao_juridica_usuario"]["texto"] == ORIENTACAO_REGULAR
    assert drafting[1]["orientacao_juridica_usuario"]["texto"] == ORIENTACAO_IRREGULAR
    assert "ressalvas" in drafting[0]["orientacao_juridica_usuario"]["consulta_de_precedentes"]
    assert "multa" in drafting[1]["orientacao_juridica_usuario"]["consulta_de_precedentes"]
    assert any("ressalvas" in query for query in queries)
    enviados = " ".join(
        item.get("trecho_literal", "")
        for item in drafting[0].get("referencias_juridicas_recuperadas") or []
    )
    assert "outro gabinete" not in enviados
    assert "orientacao_assessor" in service.generate(
        target["id"], analyzed, use_rag=False,
        **service.pacote_orientacao(ORIENTACAO_IRREGULAR, target, analyzed),
    )


def test_instrucao_rejeita_chamada_direta_sem_orientacao():
    from tests.test_minutas_instrucao import _analysis, _doc, _library, _structure

    document = _doc(
        "inicial.pdf",
        "Pagamento sem cobertura documental no instituto de previdencia municipal",
        "relatorio_inicial",
    )
    service = MinutasGenerationService(
        _library(),
        analyze=lambda _context: {"analise": _analysis(document)},
        generate=lambda _context: {"estrutura": _structure()},
    )
    analyzed = service.analyze_instruction([document], processo="03333/26", exercicio=2025)
    with pytest.raises(MinutasGenerationError, match="Informe a orientação"):
        service.generate_instruction([document], analyzed, processo="03333/26", exercicio=2025)
    generated = gerar_instrucao(
        service, [document], analyzed, processo="03333/26", exercicio=2025,
        orientacao_juridica=ORIENTACAO_REGULAR,
    )
    assert generated["orientacao_assessor"]["texto"] == ORIENTACAO_REGULAR


def test_interface_bloqueia_ate_confirmacao_e_isola_o_contexto(monkeypatch):
    calls = []
    state = {"procurador": "elvira samara", "especialidade": "elvira.pca.rpps", "fontes": "v1"}
    document = {
        "id": "doc-1", "ordem": 1, "tipo": "relatorio_inicial", "descricao": "",
        "data": "", "origem_documento": "", "sha256": "abc123", "nome": "relatorio.pdf",
        "paginas": [1], "fonte_id": "fonte-1",
    }
    analysis = {
        "analise": {"resumo_processual": "Fatos identificados.", "irregularidades": [], "pontos_atencao_gerais": []},
        "fontes_versao": "v1",
        "processo_alvo_chave": "0333326",
        "procuradora_chave": "elvira samara",
    }

    class Generator:
        def campos_orientacao_instrucao(self, analyzed, usuario="", **_alvo):
            return {
                "usuario": usuario,
                "procurador_chave": state["procurador"],
                "especialidade_id": state["especialidade"],
                "fontes_versao": state["fontes"],
                "analise": analyzed.get("analise"),
                "processo_chave": "0333326",
                "processo_analise": analyzed.get("processo_alvo_chave") or "",
            }

        def generate_instruction(self, _documents, _analyzed, **kwargs):
            texto = kwargs.get("orientacao_juridica")
            calls.append(texto)
            return {
                "texto": "Minuta.",
                "fontes_versao": state["fontes"],
                "advertencias_qualidade": [],
                "orientacao_assessor": {"texto": texto},
            }

    class Word:
        def __init__(self, _library):
            pass

        def export_instruction(self, *_args, **_kwargs):
            return type("Doc", (), {"content": b"PK", "name": "MINUTA.docx", "mime": "application/vnd.word"})()

    def page(generator, document, analysis):
        import streamlit as st
        from services.minutas_ia_ui import _instruction_panel
        from services.minutas_instrucao import versao

        st.session_state["minutas_instrucao_documentos"] = [dict(document)]
        st.session_state["minutas_instrucao_versao"] = versao([document])
        st.session_state["minutas_instrucao_analise"] = dict(analysis)
        st.session_state["minutas_instrucao_processo"] = "03333/26"
        _instruction_panel(generator, object(), {"email": "assessor@mpc.pb.gov.br"})

    monkeypatch.setattr("services.minutas_ia_ui.MinutasDocxService", Word)
    app = AppTest.from_function(
        page, args=(Generator(), document, analysis), default_timeout=30,
    ).run()
    assert any("Orientação do Assessor para a Minuta" in str(item.value) for item in app.markdown)
    assert app.button(key="minutas_instrucao_gerar").disabled is True
    app.button(key="minutas_instrucao_gerar").click().run()
    assert calls == []

    area = next(item for item in app.text_area if "conclusão pretendida" in item.label)
    area.set_value("   \n  ").run()
    next(item for item in app.button if item.label == "Confirmar orientação e habilitar geração").click().run()
    assert app.button(key="minutas_instrucao_gerar").disabled is True
    assert calls == []

    area = next(item for item in app.text_area if "conclusão pretendida" in item.label)
    area.set_value(ORIENTACAO_REGULAR).run()
    assert app.button(key="minutas_instrucao_gerar").disabled is True
    next(item for item in app.button if item.label == "Confirmar orientação e habilitar geração").click().run()
    assert app.button(key="minutas_instrucao_gerar").disabled is False
    app.run()
    assert app.session_state[area.key] == ORIENTACAO_REGULAR
    assert calls == []

    app.button(key="minutas_instrucao_gerar").click().run()
    assert calls == [ORIENTACAO_REGULAR]
    area = next(item for item in app.text_area if "conclusão pretendida" in item.label)
    area.set_value(ORIENTACAO_IRREGULAR).run()
    assert app.button(key="minutas_instrucao_gerar").disabled is True
    assert any("orientação anterior" in item.value for item in app.caption)

    state["procurador"] = "bltc"
    app.run()
    nova = next(item for item in app.text_area if "conclusão pretendida" in item.label)
    assert nova.key != area.key
    assert not str(getattr(nova, "value", "") or "")
    assert app.button(key="minutas_instrucao_gerar").disabled is True

    state["procurador"] = "elvira samara"
    state["especialidade"] = "elvira.denuncias"
    app.run()
    assert app.button(key="minutas_instrucao_gerar").disabled is True

    state["especialidade"] = "elvira.pca.rpps"
    state["fontes"] = "v2"
    app.run()
    assert app.button(key="minutas_instrucao_gerar").disabled is True
    assert calls == [ORIENTACAO_REGULAR]
