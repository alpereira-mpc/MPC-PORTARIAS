"""Converter tests use synthetic DOCX files and no production database."""

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import subprocess

from docx import Document
import pytest

from document_generator import pdf

PDF = b"%PDF-1.4\nsimulated output"


@pytest.fixture
def content():
    doc = Document()
    doc.add_paragraph("Documento exclusivo de teste")
    stream = BytesIO()
    doc.save(stream)
    return stream.getvalue()


@pytest.mark.parametrize("name", ["libreoffice", "soffice"])
def test_linux_path_search(monkeypatch, tmp_path, name):
    executable = tmp_path / name
    executable.touch()
    monkeypatch.setattr(pdf.sys, "platform", "linux")
    monkeypatch.delenv("MPC_LIBREOFFICE", raising=False)
    monkeypatch.setattr(
        pdf.shutil, "which", lambda n: str(executable) if n == name else None
    )
    assert pdf.libreoffice_path() == str(executable.resolve())


@pytest.mark.parametrize("name", ["libreoffice", "soffice"])
def test_linux_standard_locations(monkeypatch, name):
    monkeypatch.setattr(pdf.sys, "platform", "linux")
    monkeypatch.delenv("MPC_LIBREOFFICE", raising=False)
    monkeypatch.setattr(pdf.shutil, "which", lambda n: None)
    expected = Path("/usr/bin") / name
    monkeypatch.setattr(Path, "is_file", lambda p: p == expected)
    assert pdf.libreoffice_path() == str(expected.resolve())


def test_windows_program_files(monkeypatch, tmp_path):
    executable = tmp_path / "LibreOffice/program/soffice.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(pdf.sys, "platform", "win32")
    monkeypatch.delenv("MPC_LIBREOFFICE", raising=False)
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.setattr(pdf.shutil, "which", lambda n: None)
    assert pdf.libreoffice_path() == str(executable.resolve())


def test_explicit_executable_preserved(monkeypatch, tmp_path):
    executable = tmp_path / "custom office"
    executable.touch()
    monkeypatch.setenv("MPC_LIBREOFFICE", str(executable))
    assert pdf.libreoffice_path() == str(executable.resolve())


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_absent_libreoffice(monkeypatch, content, platform):
    monkeypatch.setattr(pdf.sys, "platform", platform)
    monkeypatch.delenv("MPC_LIBREOFFICE", raising=False)
    monkeypatch.setattr(pdf.shutil, "which", lambda n: None)
    monkeypatch.setattr(Path, "is_file", lambda p: False)
    assert pdf.libreoffice_path() is None
    with pytest.raises(
        pdf.PdfUnavailable,
        match="não instalado.*DOCX permanece disponível|DOCX permanece disponível.*não instalado",
    ):
        pdf.convert(content, "libreoffice")


@pytest.mark.parametrize("engine", ["auto", "word", "libreoffice"])
def test_linux_conversion(monkeypatch, content, engine):
    monkeypatch.setattr(pdf.sys, "platform", "linux")
    monkeypatch.setattr(pdf, "libreoffice_path", lambda: "/usr/bin/libreoffice")
    folders = []

    def run(args, **kwargs):
        assert args[0] == "/usr/bin/libreoffice"
        assert "--headless" in args and "--convert-to" in args
        assert not kwargs.get("shell", False)
        source = Path(args[-1])
        assert source.read_bytes() == content
        folder = Path(args[args.index("--outdir") + 1])
        assert folder == source.parent
        assert args[1] == f"-env:UserInstallation={(folder / 'profile').as_uri()}"
        folders.append(folder)
        (folder / "profile" / "user").mkdir(parents=True)
        (folder / "profile" / "user" / "registrymodifications.xcu").write_text("test")
        (folder / "portaria.pdf").write_bytes(PDF)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(pdf, "run_office", run)
    assert pdf.convert(content, engine) == (PDF, "LibreOffice")
    assert not folders[0].exists()


@pytest.mark.parametrize("fallback", [False, True])
def test_windows_word_and_fallback(monkeypatch, content, fallback):
    monkeypatch.setattr(pdf.sys, "platform", "win32")
    monkeypatch.setattr(pdf, "libreoffice_path", lambda: "soffice.exe")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            assert Path(args[1]).name == "word_worker.py"
            if fallback:
                Path(args[-1]).write_bytes(b"invalid stale output")
                return SimpleNamespace(returncode=1, stderr="Word unavailable")
            Path(args[-1]).write_bytes(PDF)
        else:
            assert args[0] == "soffice.exe"
            target = Path(args[-1]).with_suffix(".pdf")
            assert not target.exists()
            target.write_bytes(PDF)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(pdf, "run_office", run)
    monkeypatch.setattr(pdf.subprocess, "run", run)
    assert pdf.convert(content) == (
        PDF,
        "LibreOffice" if fallback else "Microsoft Word",
    )
    assert len(calls) == (2 if fallback else 1)


@pytest.mark.parametrize("platform", ["linux", "win32"])
@pytest.mark.parametrize(
    "failure", ["subprocess", "timeout", "os", "missing", "invalid"]
)
def test_conversion_failures(monkeypatch, content, platform, failure):
    monkeypatch.setattr(pdf.sys, "platform", platform)
    monkeypatch.setattr(pdf, "libreoffice_path", lambda: "soffice")
    folders = []

    def run(args, **kwargs):
        folders.append(Path(args[-1]).parent)
        profile = folders[-1] / "profile" / "user"
        profile.mkdir(parents=True)
        (profile / "registrymodifications.xcu").write_text("test")
        if failure == "subprocess":
            raise subprocess.CalledProcessError(1, args)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 55)
        if failure == "os":
            raise OSError("cannot execute")
        if failure == "invalid":
            Path(args[-1]).with_suffix(".pdf").write_bytes(b"not a PDF")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(pdf, "run_office", run)
    with pytest.raises(pdf.PdfUnavailable, match="DOCX permanece disponível"):
        pdf.convert(content, "libreoffice")
    assert not folders[0].exists()


@pytest.mark.parametrize(
    "outcome", ["success", "missing", "error", "timeout", "exception"]
)
def test_process_cleanup_precedes_profile_cleanup(monkeypatch, content, outcome):
    from document_generator import office_process as office

    monkeypatch.setattr(pdf.sys, "platform", "linux")
    monkeypatch.setattr(pdf, "libreoffice_path", lambda: "/usr/bin/soffice")
    folders = []
    events = []

    def popen(args, **kwargs):
        folder = Path(args[-1]).parent
        folders.append(folder)
        (folder / "profile").mkdir()
        (folder / "profile" / "settings").write_bytes(b"temporary profile")

        def wait(timeout):
            if timeout == 5:
                events.append("reaped")
                return 0
            if outcome == "timeout":
                raise subprocess.TimeoutExpired(args, timeout)
            if outcome == "exception":
                raise OSError("wait failed")
            if outcome == "success":
                (folder / "portaria.pdf").write_bytes(PDF)
            return 1 if outcome == "error" else 0

        return SimpleNamespace(pid=12345, wait=wait)

    def kill(pid, sig):
        assert (folders[0] / "profile" / "settings").is_file()
        events.append("killed")

    monkeypatch.setattr(office.subprocess, "Popen", popen)
    monkeypatch.setattr(office.os, "killpg", kill, raising=False)
    monkeypatch.setattr(office.signal, "SIGKILL", 9, raising=False)
    if outcome == "success":
        assert pdf.convert(content) == (PDF, "LibreOffice")
    else:
        with pytest.raises(pdf.PdfUnavailable):
            pdf.convert(content)
    assert events == ["killed", "reaped"]
    assert not folders[0].exists()
    assert not pdf._CONVERSION_LOCK.locked()
