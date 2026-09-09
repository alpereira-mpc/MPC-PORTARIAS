from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
import json
import pytest
from tests.cases import sample
from services.wording import compose, preview_text, validate, reason_text
from document_generator.docx import generate
from document_generator.pdf import convert


@pytest.mark.parametrize("reason_id", [1, 2, 3, 0])
def test_reasons_natural_and_preserved(store, reason_id):
    p = sample(store)
    sub = p["substituicoes"][0]
    text = (
        next(
            r["texto"]
            for r in store.catalog("motivos_afastamento")
            if r["id"] == reason_id
        )
        if reason_id
        else "em razão de participação em evento institucional"
    )
    sub.update(motivo_id=reason_id, motivo_texto=text)
    identifier = store.save_draft(p)
    assert (
        "{" not in store.get(identifier)["payload"]["substituicoes"][0]["motivo_texto"]
    )
    assert "{" not in preview_text(p)
    assert text in compose(p)["body"][0]
    assert "por motivo de por motivo de" not in preview_text(p)
    generate(p)


@pytest.mark.parametrize(
    "gender,expected", [("feminino", "da titular"), ("masculino", "do titular")]
)
def test_legacy_special_leave_resolved_before_storage(store, gender, expected):
    p = sample(store)
    sub = p["substituicoes"][0]
    sub["titular"]["genero"] = gender
    sub.update(
        motivo_id=2, motivo_texto="por motivo de gozo de licença especial {do_titular}"
    )
    identifier = store.save_draft(p)
    saved = store.get(identifier)["payload"]["substituicoes"][0]
    assert saved["motivo_texto"].endswith(expected)
    assert reason_text(sub) == saved["motivo_texto"]
    assert "{" not in compose(p)["body"][0]


@pytest.mark.parametrize(
    "field", ["motivo_texto", "base_legal", "nota", "assento", "manual", "signatario"]
)
def test_placeholder_rejected_at_all_boundaries(store, field):
    p = sample(store)
    if field == "manual":
        p["manual"] = {"signature_name": "{nome}"}
    elif field == "signatario":
        p["signatario"]["nome"] = "{nome}"
    else:
        p["substituicoes"][0][field] = "{xxxxx}"
    for operation in [store.save_draft, preview_text, validate, generate]:
        with pytest.raises(ValueError, match="placeholder interno"):
            operation(p)
    identifier = store.save_draft(sample(store))
    with store.connection() as c:
        c.execute(
            "UPDATE portarias SET payload=? WHERE id=?", (json.dumps(p), identifier)
        )
    with pytest.raises(ValueError, match="placeholder interno"):
        store.finalize(identifier)
    assert store.next_number(2026) == 9


def test_custom_reason_internal_token_is_not_silently_changed(store):
    p = sample(store)
    p["substituicoes"][0].update(motivo_id=0, motivo_texto="{do_titular}")
    with pytest.raises(ValueError, match="placeholder"):
        store.save_draft(p)


def test_pdf_rejects_placeholder_even_split_across_xml_runs(store):
    content = generate(sample(store))
    out = BytesIO()
    with ZipFile(BytesIO(content)) as inp, ZipFile(out, "w", ZIP_DEFLATED) as target:
        for name in inp.namelist():
            data = inp.read(name)
            if name == "word/document.xml":
                data = data.replace(
                    b"ELVIRA SAMARA PEREIRA DE OLIVEIRA",
                    b"{no</w:t></w:r><w:r><w:t>me}",
                )
            target.writestr(name, data)
    with pytest.raises(ValueError, match="placeholder"):
        convert(out.getvalue())
