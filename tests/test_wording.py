from copy import deepcopy
from datetime import date
from io import BytesIO
from zipfile import ZipFile
from lxml import etree
import pytest
from services.wording import compose, period, validate, preview_text, long_date
from services.validation import warnings_for
from document_generator.docx import generate
from tests.cases import sample


def test_subprocuradoria(store):
    p = sample(store)
    validate(p)
    text = preview_text(p, 9)
    for term in [
        "A PROCURADORA-GERAL",
        "Sheyla Barreto Braga de Queiroz",
        "a Subprocuradora-Geral Isabella Barbosa Marinho Falcão",
        "1ª Câmara",
        "8 a 14 de setembro de 2026",
        "art. 61, § 6º",
        "ELVIRA SAMARA PEREIRA DE OLIVEIRA",
        "Procuradora-Geral do Ministério",
    ]:
        assert term in text
    assert "202," not in text


def test_ouvidoria(store):
    c = compose(sample(store, 5))
    assert "na função de Ouvidor" in c["body"][0]
    assert "art. 70, § 3º" in c["intro"]
    assert "ordem de antiguidade" in c["notes"][0]
    assert c["signature_role"].startswith("Procuradora-Geral")


def test_cascade(store):
    c = compose(sample(store, 6))
    assert len(c["body"]) == 2
    assert "esta Procuradora-Geral" in c["body"][0]
    assert c["body"][1].startswith("R E S O L V E, ainda,")
    assert "durante o período acima mencionado." in c["body"][1]
    assert "29 de junho a 9 de julho de 2026" in c["body"][0]


def test_acting_signer(store):
    text = preview_text(sample(store, 4), 4)
    assert "O PROCURADOR-GERAL EM EXERCÍCIO" in text
    assert "O PROCURADORA-GERAL" not in text
    assert "BRADSON TIBÉRIO LUNA CAMELO" in text
    assert (
        "Procurador-Geral do Ministério Público de Contas da Paraíba em exercício"
        in text
    )


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2026-09-08", "2026-09-14", "8 a 14 de setembro de 2026"),
        ("2026-06-29", "2026-07-09", "29 de junho a 9 de julho de 2026"),
        ("2026-12-29", "2027-01-09", "29 de dezembro de 2026 a 9 de janeiro de 2027"),
        ("2028-02-28", "2028-02-29", "28 a 29 de fevereiro de 2028"),
        ("2026-02-28", "2026-03-01", "28 de fevereiro a 1 de março de 2026"),
        ("2026-02-01", "2026-02-01", "1 de fevereiro de 2026"),
    ],
)
def test_dates(start, end, expected):
    assert period(start, end) == expected


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-02-29", "2026-03-01"),
        ("2026-09-14", "2026-09-08"),
        ("0202-01-01", "2026-01-01"),
        ("202-09-08", "2026-09-14"),
    ],
)
def test_invalid_dates(start, end):
    with pytest.raises(ValueError):
        period(start, end)


@pytest.mark.parametrize(
    "field,value",
    [
        ("funcao", ""),
        ("base_legal", ""),
        ("motivo_texto", ""),
        ("assento", "Não se aplica"),
        ("substituto", None),
        ("titular", None),
        ("fim", "2026-09-07"),
    ],
)
def test_validation(store, field, value):
    p = sample(store)
    p["substituicoes"][0][field] = value
    with pytest.raises(ValueError):
        validate(p)


def test_same_person(store):
    p = sample(store)
    p["substituicoes"][0]["substituto"] = p["substituicoes"][0]["titular"]
    with pytest.raises(ValueError):
        validate(p)


def test_manual_is_local(store):
    p = sample(store)
    before = compose(p)
    p["manual"] = {"body": ["R E S O L V E designar conforme texto excepcional."]}
    assert compose(p)["body"] != before["body"]
    assert compose(sample(store)) == before
    p["manual"]["body"] = ["até 14/9/202."]
    with pytest.raises(ValueError):
        validate(p)


@pytest.mark.parametrize("number", [5, 6, 8])
def test_docx_package(store, number):
    p = sample(store, number)
    data = generate(p, number)
    with ZipFile(BytesIO(data)) as z:
        root = etree.fromstring(z.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        text = "".join(root.xpath("//w:t/text()", namespaces=ns))
        assert f"N.º {number}/2026" in text
        assert "Público Contas" not in text
        assert "202," not in text
        assert "ELVIRA SAMARA PEREIRA DE OLIVEIRA" in text
        size = root.find(".//w:pgSz", ns)
        assert int(size.get("{" + ns["w"] + "}w")) == 11907
        refs = root.xpath("//w:footnoteReference", namespaces=ns)
        assert len(refs) == (1 if number == 5 else 0)
        if number == 5:
            assert "ordem de antiguidade" in z.read("word/footnotes.xml").decode()
        assert z.read("word/media/image1.jpeg")


def test_conflict_warning(store):
    p = sample(store)
    assert warnings_for(p, [p])
    assert warnings_for(sample(store, 5))
    assert not warnings_for(p)


@pytest.mark.parametrize("reverse", [False, True])
def test_absent_substitute_warning_independent_of_order(store, reverse):
    p = sample(store, 6)
    p["substituicoes"][1]["decorrente"] = False
    if reverse:
        p["substituicoes"].reverse()
    assert any("afastamento no mesmo período" in w for w in warnings_for(p))


def test_cascade_is_not_absence_conflict(store):
    assert not any(
        "afastamento no mesmo período" in w for w in warnings_for(sample(store, 6))
    )


def test_independent_second_period(store):
    p = sample(store, 6)
    p["substituicoes"][1]["fim"] = "2026-07-10"
    assert "29 de junho a 10 de julho de 2026" in compose(p)["body"][1]
    assert "acima mencionado" not in compose(p)["body"][1]


def test_corregedoria_feminine_and_legal_note(store):
    p = sample(store, 5)
    p["substituicoes"][0]["funcao"] = "Corregedor"
    p["substituicoes"][0]["titular"] = store.catalog("procuradores")[6]
    c = compose(p)
    assert "na função de Corregedora" in c["body"][0]
    assert "art. 70, § 3º" in c["intro"]
    assert c["notes"]


def test_signer_other_person_uses_nominal_target(store):
    p = sample(store, 6)
    p["signatario"] = store.catalog("procuradores")[2]
    p["em_exercicio"] = True
    c = compose(p)
    assert "esta Procuradora-Geral" not in c["body"][0]
    assert "a Procuradora-Geral Elvira Samara Pereira de Oliveira" in c["body"][0]


def test_custom_reason_preserved(store):
    p = sample(store)
    value = "em razão de afastamento expressamente autorizado"
    p["substituicoes"][0]["motivo_texto"] = value
    assert value + "." in compose(p)["body"][0]


def test_three_substitutions_and_footnote(store):
    p = sample(store, 6)
    p["substituicoes"].append(sample(store, 5)["substituicoes"][0])
    data = generate(p, 11)
    with ZipFile(BytesIO(data)) as z:
        xml = etree.fromstring(z.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = [
            "".join(p.xpath(".//w:t/text()", namespaces=ns))
            for p in xml.xpath("/w:document/w:body/w:p", namespaces=ns)
        ]
        assert sum(p.startswith("R E S O L V E") for p in paragraphs) == 3
        assert len(xml.xpath("//w:footnoteReference", namespaces=ns)) == 1
