"""Generate from preserved institutional packages, replacing only content slots."""

from copy import deepcopy
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import re
from lxml import etree as E
from services.wording import compose, long_date, parsed, period, role, validate

ROOT = Path(__file__).resolve().parents[1]
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


def tag(local):
    return "{" + W + "}" + local


def text(p):
    return "".join(p.xpath(".//w:t/text()", namespaces=NS))


def run(value, bold=False, size=26):
    r = E.Element(tag("r"))
    pr = E.SubElement(r, tag("rPr"))
    fonts = E.SubElement(pr, tag("rFonts"))
    for name in ("ascii", "hAnsi", "cs", "eastAsia"):
        fonts.set(tag(name), "Times New Roman")
    E.SubElement(pr, tag("sz")).set(tag("val"), str(size))
    E.SubElement(pr, tag("color")).set(tag("val"), "000000")
    if bold:
        E.SubElement(pr, tag("b"))
    t = E.SubElement(r, tag("t"))
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = value
    return r


def replace(p, value, bold_terms=(), size=26):
    for child in list(p):
        if child.tag != tag("pPr"):
            p.remove(child)
    terms = sorted(set(t for t in bold_terms if t), key=len, reverse=True)
    pattern = "(" + "|".join(re.escape(t) for t in terms) + ")" if terms else None
    for part in re.split(pattern, value) if pattern else [value]:
        if part:
            p.append(run(part, part in terms, size))


def insert_note_reference(paragraph, offset, identifier):
    reference = E.Element(tag("r"))
    rp = E.SubElement(reference, tag("rPr"))
    E.SubElement(rp, tag("vertAlign")).set(tag("val"), "superscript")
    E.SubElement(reference, tag("footnoteReference")).set(tag("id"), str(identifier))
    for original in paragraph.findall(tag("r")):
        value = text(original)
        if offset <= len(value):
            bold = original.find("w:rPr/w:b", NS) is not None
            if value[:offset]:
                original.addprevious(run(value[:offset], bold))
            original.addprevious(reference)
            if value[offset:]:
                original.addprevious(run(value[offset:], bold))
            paragraph.remove(original)
            return
        offset -= len(value)
    paragraph.append(reference)


def xml_bytes(root):
    return E.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def generate(payload, number=None):
    validate(payload)
    content = compose(payload)
    template = (
        "multipla"
        if len(content["body"]) > 1
        else ("com_nota" if content["notes"] else "simples")
    )
    with ZipFile(ROOT / "templates" / f"{template}.docx") as source:
        parts = {name: source.read(name) for name in source.namelist()}
    document = E.fromstring(parts["word/document.xml"])
    body = document.find(tag("body"))
    paragraphs = body.findall(tag("p"))
    title = next(p for p in paragraphs if text(p).startswith("PORTARIA"))
    title_text = f"PORTARIA – PROGE N.º {number if number is not None else '[PRÉVIA]'}/{parsed(payload['data']).year:04d}"
    replace(title, title_text, [title_text])
    dated = next(p for p in paragraphs if text(p).startswith("João Pessoa"))
    replace(dated, "João Pessoa, " + long_date(payload["data"]) + ".")
    intro = next(p for p in paragraphs if "no uso de suas atribuições" in text(p))
    intro_lead = content["intro"].split(" do Ministério", 1)[0]
    replace(intro, content["intro"], [intro_lead])
    # Real Word footnotes, including separator and proper relationships.
    if content["notes"]:
        if "word/footnotes.xml" not in parts:
            with ZipFile(ROOT / "templates/com_nota.docx") as z:
                parts["word/footnotes.xml"] = z.read("word/footnotes.xml")
        notes = E.fromstring(parts["word/footnotes.xml"])
        for n in list(notes):
            if int(n.get(tag("id"), "0")) > 0:
                notes.remove(n)
        for i, note in enumerate(content["notes"], 1):
            n = E.SubElement(notes, tag("footnote"))
            n.set(tag("id"), str(i))
            p = E.SubElement(n, tag("p"))
            pr = E.SubElement(p, tag("pPr"))
            E.SubElement(pr, tag("jc")).set(tag("val"), "both")
            nr = E.SubElement(p, tag("r"))
            E.SubElement(nr, tag("footnoteRef"))
            p.append(run(" " + note, size=20))
            linked_basis = next(
                (
                    s["base_legal"].split(", do Regimento", 1)[0]
                    for s in payload["substituicoes"]
                    if s.get("nota", "").strip() == note
                ),
                "",
            )
            start = content["intro"].find(linked_basis) if linked_basis else -1
            offset = (
                start + len(linked_basis)
                if start >= 0
                else len(content["intro"].rstrip(","))
            )
            insert_note_reference(intro, offset, i)
        parts["word/footnotes.xml"] = xml_bytes(notes)
        relpath = "word/_rels/document.xml.rels"
        rels = E.fromstring(parts[relpath])
        if not any(r.get("Type", "").endswith("/footnotes") for r in rels):
            rel = E.SubElement(
                rels,
                "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship",
            )
            rel.set("Id", "rIdMpcFootnotes")
            rel.set(
                "Type",
                "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes",
            )
            rel.set("Target", "footnotes.xml")
        parts[relpath] = xml_bytes(rels)
        types = E.fromstring(parts["[Content_Types].xml"])
        if not any(x.get("PartName") == "/word/footnotes.xml" for x in types):
            t = E.SubElement(
                types,
                "{http://schemas.openxmlformats.org/package/2006/content-types}Override",
            )
            t.set("PartName", "/word/footnotes.xml")
            t.set(
                "ContentType",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
            )
        parts["[Content_Types].xml"] = xml_bytes(types)
    terms = ["R E S O L V E"]
    for s in payload["substituicoes"]:
        terms += [
            s["titular"]["nome"],
            s["substituto"]["nome"],
            s["assento"],
            period(s["inicio"], s["fim"]),
        ]
        if s["funcao"] in ("Ouvidor", "Corregedor"):
            terms.append(role(s["funcao"], s["titular"]["genero"]))
    slots = [p for p in paragraphs if text(p).startswith("R E S O L V E")]
    sample = deepcopy(slots[0])
    for i, value in enumerate(content["body"]):
        if i < len(slots):
            p = slots[i]
        else:
            blank = E.Element(tag("p"))
            slots[-1].addnext(blank)
            p = deepcopy(sample)
            blank.addnext(p)
            slots.append(p)
        replace(p, value, terms)
    for p in slots[len(content["body"]) :]:
        body.remove(p)
    signature = document.xpath("//w:tbl//w:p", namespaces=NS)
    nonempty = [p for p in signature if text(p).strip()]
    replace(nonempty[0], content["signature_name"], [content["signature_name"]], 26)
    replace(nonempty[1], content["signature_role"], size=22)
    # Keep the signature together and prevent unnecessary fixed row clipping.
    for p in nonempty:
        pr = p.find(tag("pPr"))
        if pr is None:
            pr = E.SubElement(p, tag("pPr"))
        E.SubElement(pr, tag("keepNext")).set(
            tag("val"), "1" if p is nonempty[0] else "0"
        )
        jc = pr.find(tag("jc"))
        if jc is None:
            jc = E.SubElement(pr, tag("jc"))
        jc.set(tag("val"), "center")
        indent = pr.find(tag("ind"))
        if indent is None:
            indent = E.SubElement(pr, tag("ind"))
        for attr in ("left", "right", "firstLine"):
            indent.set(tag(attr), "0")
    for h in document.xpath("//w:trHeight", namespaces=NS):
        h.set(tag("hRule"), "atLeast")
    parts["word/document.xml"] = xml_bytes(document)
    # Source author metadata is not the author of generated official acts.
    if "docProps/core.xml" in parts:
        core = E.fromstring(parts["docProps/core.xml"])
        for child in core:
            if E.QName(child).localname in ("creator", "lastModifiedBy"):
                child.text = "MPC-PB"
        parts["docProps/core.xml"] = xml_bytes(core)
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as target:
        for name, data in parts.items():
            target.writestr(name, data)
    return out.getvalue()
