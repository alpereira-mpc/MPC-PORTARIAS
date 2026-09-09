from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
import re
import unicodedata
import hashlib
from document_generator.docx import generate
from document_generator.pdf import convert
from services.wording import parsed
from services.placeholders import assert_docx_clean


def setup_logging(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("")
    path = folder / "mpc.log"
    if not any(
        isinstance(h, RotatingFileHandler) and h.baseFilename == str(path.resolve())
        for h in logger.handlers
    ):
        handler = RotatingFileHandler(
            path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


def filename(payload, number, extension):
    s = payload["substituicoes"][0]
    names = "_".join(
        [s["titular"]["nome"].split()[0], "por", s["substituto"]["nome"].split()[0]]
    )
    ascii_names = (
        unicodedata.normalize("NFKD", names).encode("ascii", "ignore").decode()
    )
    ascii_names = re.sub(r"[^A-Za-z0-9_]+", "_", ascii_names)
    return f"Portaria_PROGE_{number:03d}_{parsed(payload['data']).year}_Substituicao_{ascii_names}.{extension}"


def write_new(folder, name, content):
    folder = Path(folder).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    for version in range(10000):
        candidate = path if not version else path.with_stem(f"{path.stem}_v{version+1}")
        try:
            with candidate.open("xb") as stream:
                stream.write(content)
            return candidate
        except FileExistsError:
            continue
    raise OSError("Muitas versões no diretório de exportação.")


def export_record(store, identifier, extension="docx", regenerate=False):
    record = store.get(identifier)
    if record["status"] == "Rascunho":
        raise ValueError("Use a prévia para exportar um rascunho.")
    settings = store.settings()
    assert_docx_clean(record["docx"])
    if extension == "docx":
        content = record["docx"]
    elif extension == "pdf":
        content = record["pdf"]
        if not content or regenerate:
            content, _ = convert(record["docx"], settings.get("pdf_engine", "auto"))
            store.cache_pdf(identifier, content)
    else:
        raise ValueError("Formato inválido.")
    name = filename(record["payload"], record["numero"], extension)
    with store.connection() as c:
        c.execute("BEGIN IMMEDIATE")
        if not c.execute(
            "SELECT 1 FROM portarias WHERE id=? AND status!='Rascunho'", (identifier,)
        ).fetchone():
            raise ValueError(
                "Esta Portaria foi excluída. A exportação não foi realizada."
            )
        path = write_new(settings["export_dir"], name, content)
        try:
            c.execute(
                "INSERT INTO exportacoes(portaria_id,caminho,formato,sha256) VALUES(?,?,?,?)",
                (identifier, str(path), extension, hashlib.sha256(content).hexdigest()),
            )
            c.commit()
        except Exception:
            path.unlink(missing_ok=True)
            raise
    return path, content
