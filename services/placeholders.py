"""Reject unresolved template expressions at every document boundary."""

import re
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree


def reject_placeholders(value):
    if isinstance(value, dict):
        for item in value.values():
            reject_placeholders(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            reject_placeholders(item)
    elif isinstance(value, str) and re.search(r"\{[^{}]+\}", value):
        raise ValueError(
            "Erro técnico: texto com placeholder interno não resolvido. Corrija a redação antes de continuar."
        )


def assert_docx_clean(content):
    with ZipFile(BytesIO(content)) as package:
        for name in package.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                root = ElementTree.fromstring(package.read(name))
                reject_placeholders("".join(root.itertext()))
