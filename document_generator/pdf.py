"""Bounded local DOCX-to-PDF conversion; no document leaves this machine."""

from pathlib import Path
from tempfile import TemporaryDirectory
import logging
import os
import shutil
import subprocess
import sys
from services.placeholders import assert_docx_clean

LOG = logging.getLogger(__name__)


class PdfUnavailable(RuntimeError):
    pass


def libreoffice_path():
    candidates = [os.environ.get("MPC_LIBREOFFICE", ""), shutil.which("soffice")]
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        if os.environ.get(key):
            candidates.append(
                str(Path(os.environ[key]) / "LibreOffice/program/soffice.exe")
            )
    return next(
        (str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()), None
    )


def convert(content, engine="auto"):
    assert_docx_clean(content)
    if engine not in ("auto", "word", "libreoffice"):
        raise ValueError("Mecanismo PDF inválido.")
    failures = []
    with TemporaryDirectory(prefix="mpc_pdf_") as temp:
        folder = Path(temp)
        source = folder / "portaria.docx"
        source.write_bytes(content)
        target = folder / "portaria.pdf"
        if engine in ("auto", "word") and sys.platform == "win32":
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("word_worker.py")),
                        str(source),
                        str(target),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=55,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if completed.returncode or not target.exists():
                    raise RuntimeError(completed.stderr[-1000:])
                result = target.read_bytes()
                if not result.startswith(b"%PDF-"):
                    raise ValueError("Saída Word inválida")
                return result, "Microsoft Word"
            except (
                OSError,
                ValueError,
                RuntimeError,
                subprocess.TimeoutExpired,
            ) as exc:
                LOG.exception("Conversão Word indisponível")
                failures.append("Microsoft Word indisponível ou sem resposta")
        if engine in ("auto", "libreoffice"):
            executable = libreoffice_path()
            if executable:
                try:
                    target.unlink(missing_ok=True)
                    profile = (folder / "profile").as_uri()
                    subprocess.run(
                        [
                            executable,
                            f"-env:UserInstallation={profile}",
                            "--headless",
                            "--convert-to",
                            "pdf",
                            "--outdir",
                            str(folder),
                            str(source),
                        ],
                        capture_output=True,
                        timeout=55,
                        check=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    result = target.read_bytes()
                    if not result.startswith(b"%PDF-"):
                        raise ValueError("Saída LibreOffice inválida")
                    return result, "LibreOffice"
                except (OSError, ValueError, subprocess.SubprocessError):
                    LOG.exception("Conversão LibreOffice indisponível")
                    failures.append("LibreOffice falhou ao converter")
        detail = "; ".join(failures)
        raise PdfUnavailable(
            "A geração de PDF requer Microsoft Word ou LibreOffice funcionando neste computador. O DOCX permanece disponível."
            + (" " + detail + "." if detail else "")
        )
