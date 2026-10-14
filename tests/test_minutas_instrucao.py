"""Instrução processual com número variável de PDFs, sem cadastro no acervo."""

from io import BytesIO

from reportlab.pdfgen import canvas
import pytest

from services.minutas_docx import ELVIRA_TEMPLATE, MinutasDocxService
from services.minutas_generation import MinutasGenerationError, MinutasGenerationService
from services.minutas_instrucao import (
    MinutasInstrucaoError,
    conferencia,
    consolidar,
    fontes,
    receber_pdf,
    versao,
)


def _pdf(*pages):
    output = BytesIO()
    document = canvas.Canvas(output)
    for text in pages:
        text_object = document.beginText(72, 760)
        for line in str(text).splitlines() or [""]:
            text_object.textLine(line)
        document.drawText(text_object)
        document.showPage()
    document.save()
    return output.getvalue()


def _doc(name, text, tipo, **extra):
    item = receber_pdf(name, _pdf(text), tipo)
    item.update(extra)
    return item


def _library():
    class Repository:
        def create(self, *_args, **_kwargs):
            raise AssertionError("a instrução não pode cadastrar documento")

        def add_file(self, *_args, **_kwargs):
            raise AssertionError("a instrução não pode cadastrar documento")

    class Library:
        repository = Repository()
        storage = object()

        def _authorize(self):
            return None

    return Library()


def _analysis(document):
    return {
        "resumo_processual": "Instrucao recebida para conferencia.",
        "sem_irregularidades_identificadas": False,
        "pontos_atencao_gerais": ["Conferir os documentos enviados."],
        "irregularidades": [{
            "id": "IRR-1",
            "apontamento": "Pagamento sem cobertura documental",
            "fatos_documentados": [{
                "descricao": "Pagamento sem cobertura",
                "fontes": [{
                    "fonte_id": document["fonte_id"],
                    "pagina": 1,
                    "trecho_suporte": (
                        "Pagamento sem cobertura documental no instituto "
                        "de previdencia municipal"
                    ),
                }],
            }],
            "normas_mencionadas": [],
            "argumentos_defesa": [],
            "pontos_controvertidos": ["Ha posicoes distintas na instrucao."],
            "hipoteses_juridicas": ["Conferir a cobertura documental."],
            "avaliacao_auditoria": {
                "descricao": "Apontamento registrado no documento.",
                "tratamento_defesa": "NAO_IDENTIFICADO",
                "fontes": [],
            },
        }],
    }


def _structure():
    return {
        "identificacao_processual": "Processo TC 03333/26",
        "ementa": "Ementa de teste da instrucao.",
        "relatorio": "Relatorio da instrucao processual.",
        "relatorio_paragrafos": ["Relatorio da instrucao processual."],
        "fundamentacao_geral": "Fundamentacao geral de teste.",
        "fundamentacao_geral_paragrafos": ["Fundamentacao geral de teste."],
        "topicos_irregularidades": [{
            "titulo": "Pagamento sem cobertura",
            "texto": "O apontamento permanece em conferencia.",
            "paragrafos": ["O apontamento permanece em conferencia."],
            "irregularidades_ids": ["IRR-1"],
            "avaliacoes_irregularidades": [{
                "irregularidade_id": "IRR-1",
                "tratamento_ministerial_defesa": "NAO_APLICAVEL",
                "fundamentos_divergencia": [],
                "estado_saneamento": "NAO_ALEGADO",
                "fontes_saneamento": [],
            }],
        }],
        "conclusao_pedidos": "Pede-se a conferencia.",
        "pedidos": ["Conferir a instrucao antes da assinatura."],
        "encerramento": "E o parecer, salvo melhor juizo.",
    }


def test_somente_relatorio_inicial_nao_inventa_etapa():
    document = _doc(
        "inicial.pdf",
        "Pagamento sem cobertura documental no instituto de previdencia municipal",
        "relatorio_inicial",
    )
    review = conferencia([document])
    assert "apenas" in review["advertencias"][0]
    assert "Relatório Inicial" in review["advertencias"][0]
    assert "defesa alegou" not in " ".join(review["advertencias"])
    assert fontes([document])[0]["papel"] == "auditoria"


def test_somente_analise_de_defesa():
    document = _doc(
        "defesa.pdf",
        "Pagamento sem cobertura documental no instituto de previdencia municipal",
        "analise_defesa",
    )
    review = conferencia([document])
    assert "Relatório de Análise de Defesa" in review["advertencias"][0]
    assert fontes([document])[0]["papel"] == "auditoria"


def test_inicial_e_analise_de_defesa():
    documents = [
        _doc("inicial.pdf", "Relatorio inicial sem apontamento numerado.", "relatorio_inicial"),
        _doc("defesa.pdf", "Relatorio de analise da defesa.", "analise_defesa"),
    ]
    review = conferencia(documents)
    assert not any("apenas" in item for item in review["advertencias"])
    assert [item["tipo"] for item in fontes(documents)] == [
        "Relatório Inicial de Auditoria",
        "Relatório de Análise de Defesa",
    ]


def test_inicial_cota_defesa_e_complementacao():
    documents = [
        _doc("inicial.pdf", "Relatorio inicial.", "relatorio_inicial"),
        _doc("cota.pdf", "Cota ministerial de diligencia.", "cota"),
        _doc("defesa.pdf", "Analise da defesa.", "analise_defesa"),
        _doc("complemento.pdf", "Relatorio de complementacao.", "complementacao"),
    ]
    rows = fontes(documents)
    assert [row["papel"] for row in rows] == [
        "auditoria", "complementar", "auditoria", "auditoria",
    ]
    assert [row["ordem_processual"] for row in rows] == [1, 2, 3, 4]
    assert len({row["fonte_id"] for row in rows}) == 4
    assert all(row["sha256"] for row in rows)


def test_varios_relatorios_complementares_do_mesmo_tipo():
    documents = [
        _doc(f"complemento-{index}.pdf", f"Complementacao {index}.", "complementacao")
        for index in range(1, 4)
    ]
    rows = fontes(documents)
    assert len(rows) == 3
    assert {row["tipo"] for row in rows} == {"Relatório de Complementação"}
    assert [row["arquivo"] for row in rows] == [
        "complemento-1.pdf", "complemento-2.pdf", "complemento-3.pdf",
    ]


def test_ordem_informada_prevalece_sobre_a_data():
    documents = [
        _doc("recente.pdf", "Peca recente.", "despacho", data="2026-03-01"),
        _doc("antiga.pdf", "Peca antiga.", "informacao", data="2026-01-15"),
    ]
    review = conferencia(documents)
    assert any("não segue as datas" in item for item in review["advertencias"])
    assert [row["arquivo"] for row in fontes(documents)] == ["recente.pdf", "antiga.pdf"]


def test_saneamento_posterior_nao_prevalece_sozinho():
    documents = [
        _doc(
            "inicial.pdf",
            "Irregularidade 1 pagamento sem cobertura permanece",
            "relatorio_inicial",
        ),
        _doc(
            "complemento.pdf",
            "Irregularidade 1 pagamento foi sanada",
            "complementacao",
        ),
    ]
    review = conferencia(documents)
    assert review["evolucao"]
    assert "não" in review["evolucao"][0]["leitura"] or "Nenhum" in review["evolucao"][0]["leitura"]
    assert len(review["evolucao"][0]["posicoes"]) == 2


def test_divergencia_entre_relatorios_fica_registrada():
    documents = [
        _doc(
            "inicial.pdf",
            "Irregularidade 2 pagamento sem cobertura",
            "relatorio_inicial",
        ),
        _doc(
            "defesa.pdf",
            "Irregularidade 2 nao se confirma o pagamento",
            "analise_defesa",
        ),
    ]
    review = conferencia(documents)
    assert review["divergencias"]
    assert "sem escolha automática" in review["divergencias"][0]["leitura"]


def test_inclusao_ou_remocao_invalida_a_versao_anterior():
    first = _doc(
        "inicial.pdf",
        "Pagamento sem cobertura documental no instituto de previdencia municipal",
        "relatorio_inicial",
    )
    second = _doc("cota.pdf", "Cota ministerial.", "cota")
    seen = {}

    def analyze(context):
        seen["ids"] = [item["fonte_id"] for item in context["documentos_processo_alvo"]]
        return {"analise": _analysis(first)}

    service = MinutasGenerationService(_library(), analyze=analyze, generate=lambda _context: {"estrutura": _structure()})
    analyzed = service.analyze_instruction([first], processo="03333/26", exercicio=2025)
    assert seen["ids"] == [first["fonte_id"]]
    with pytest.raises(MinutasGenerationError, match="alterada"):
        service.generate_instruction(
            [first, second], analyzed, processo="03333/26", exercicio=2025,
        )
    assert versao([first]) != versao([first, second])
    assert versao([first, second]) != versao([first])


def test_volume_acima_do_contexto_preserva_apontamento_e_lista_omissao():
    finding = "Irregularidade 3 pagamento sem cobertura permanece"
    pages = [{"texto": finding, "pagina": 1}]
    pages.extend(
        {"texto": "registro administrativo sem apontamento " * 30, "pagina": number}
        for number in range(2, 16)
    )
    source = {
        "fonte_id": "volume",
        "arquivo": "volume.pdf",
        "papel": "auditoria",
        "tipo": "Relatório Inicial de Auditoria",
        "ordem_processual": 1,
        "conteudo": pages,
    }
    kept, omissions = consolidar([source], budget=2500)
    assert any(finding in unit["texto"] for unit in kept[0]["conteudo"])
    assert 1 not in omissions[0]["paginas_nao_enviadas_integralmente"]
    assert omissions[0]["paginas_nao_enviadas_integralmente"]


def test_volume_impossivel_nao_trunca_em_silencio():
    source = {
        "fonte_id": "volume",
        "arquivo": "volume.pdf",
        "papel": "auditoria",
        "tipo": "Relatório Inicial de Auditoria",
        "ordem_processual": 1,
        "conteudo": [{
            "texto": "Irregularidade 9 " + ("cobertura " * 400),
            "pagina": 1,
        }],
    }
    with pytest.raises(MinutasInstrucaoError, match="excede o contexto"):
        consolidar([source], budget=120)


@pytest.mark.parametrize("quantidade", [1, 2, 4])
def test_analise_geracao_e_docx_aceitam_quantidade_variavel(quantidade):
    if not ELVIRA_TEMPLATE.is_file():
        pytest.skip("modelo Word indisponível")
    base = _doc(
        "inicial.pdf",
        "Pagamento sem cobertura documental no instituto de previdencia municipal",
        "relatorio_inicial",
    )
    extras = [
        _doc("cota.pdf", "Cota ministerial de diligencia.", "cota"),
        _doc("defesa.pdf", "Analise da manifestacao apresentada.", "analise_defesa"),
        _doc("outro.pdf", "Informacao tecnica complementar.", "informacao"),
    ]
    documents = [base, *extras[: quantidade - 1]]
    captured = {}

    def analyze(context):
        captured["ids"] = [item["fonte_id"] for item in context["documentos_processo_alvo"]]
        captured["instrucao"] = context["processo_alvo"]["instrucao_local"]
        return {"analise": _analysis(base)}

    def generate(context):
        captured["geracao"] = len(context["documentos_processo_alvo"])
        return {"estrutura": _structure()}

    hits = []

    def search(query, **kwargs):
        hits.append((query, kwargs.get("exclude_process_key")))
        return {"resultados": [
            {"papel": "parecer", "referencia_id": "parecer-1", "processo_chave": "0165326",
             "numero_processo": "01653/26", "trecho": "entendimento historico"},
            {"papel": "complementar", "referencia_id": "relatorio-local", "processo_chave": "0999926",
             "trecho": "nao e precedente"},
            {"papel": "parecer", "referencia_id": "mesmo", "processo_chave": "0333326",
             "trecho": "o proprio processo"},
        ]}

    service = MinutasGenerationService(
        _library(), analyze=analyze, generate=generate, search=search,
    )
    analyzed = service.analyze_instruction(
        documents, processo="03333/26", exercicio=2025, origem="Instituto",
    )
    generated = service.generate_instruction(
        documents, analyzed, processo="03333/26", exercicio=2025, origem="Instituto",
    )
    word = MinutasDocxService(_library(), template_path=ELVIRA_TEMPLATE).export_instruction(
        analyzed, generated, processo="03333/26", exercicio=2025, origem="Instituto",
    )
    assert captured["ids"] == [item["fonte_id"] for item in documents]
    assert captured["instrucao"] is True
    assert captured["geracao"] == quantidade
    assert analyzed["fontes_alvo"] == quantidade
    assert hits and hits[0][1] == "0333326"
    assert [item["referencia_id"] for item in analyzed["referencias"]["referencias"]] == ["parecer-1"]
    assert word.content.startswith(b"PK")
    assert word.name == "MINUTA_PARECER_TC_03333_26.docx"
