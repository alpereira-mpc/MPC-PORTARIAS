"""Process lifecycle checks, independent of installed office applications."""

import subprocess
import sys
from types import SimpleNamespace

import pytest

from document_generator import office_process as office
from document_generator import pdf


@pytest.mark.parametrize("outcome", ["success", "error", "timeout", "exception"])
def test_posix_always_kills_group_and_reaps(monkeypatch, outcome):
    monkeypatch.setattr(office.sys, "platform", "linux")
    events = []

    def wait(timeout):
        events.append(("wait", timeout))
        if timeout == 5:
            return 0
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(["office"], timeout)
        if outcome == "exception":
            raise OSError("wait failed")
        return 2 if outcome == "error" else 0

    def popen(args, **kwargs):
        assert args == ["office", "a path with spaces"]
        assert kwargs["start_new_session"] is True
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        assert not kwargs.get("shell", False)
        return SimpleNamespace(pid=54321, wait=wait)

    monkeypatch.setattr(office.subprocess, "Popen", popen)
    monkeypatch.setattr(
        office.os,
        "killpg",
        lambda pid, sig: events.append(("kill", pid, sig)),
        raising=False,
    )
    monkeypatch.setattr(office.signal, "SIGKILL", 9, raising=False)
    if outcome == "success":
        office.run_office(["office", "a path with spaces"])
    else:
        with pytest.raises((OSError, subprocess.SubprocessError)):
            office.run_office(["office", "a path with spaces"])
    assert events == [("wait", 55), ("kill", 54321, 9), ("wait", 5)]


def test_posix_group_already_exited(monkeypatch):
    monkeypatch.setattr(office.sys, "platform", "linux")
    waits = []
    monkeypatch.setattr(
        office.subprocess,
        "Popen",
        lambda *a, **k: SimpleNamespace(
            pid=54321, wait=lambda timeout: waits.append(timeout) or 0
        ),
    )

    def gone(*args):
        raise ProcessLookupError()

    monkeypatch.setattr(office.os, "killpg", gone, raising=False)
    monkeypatch.setattr(office.signal, "SIGKILL", 9, raising=False)
    office.run_office(["office"])
    assert waits == [55, 5]


@pytest.mark.parametrize(
    "outcome", ["success", "error", "timeout", "assign", "resume", "spawn"]
)
def test_windows_job_cleanup(monkeypatch, outcome):
    monkeypatch.setattr(office.sys, "platform", "win32")
    events = []

    def create(*args):
        events.append("create")
        assert args[5] == 12  # suspended + no window in these fakes
        assert args[4] is False  # no inherited job handle
        if outcome == "spawn":
            raise OSError("spawn failed")
        return "process", "thread", 123, 456

    def assign(*args):
        events.append("assign")
        if outcome == "assign":
            raise OSError("assignment failed")

    def resume(*args):
        events.append("resume")
        if outcome == "resume":
            raise OSError("resume failed")

    def wait(process, timeout):
        events.append(("wait", timeout))
        return 258 if outcome == "timeout" and timeout == 55000 else 0

    job = SimpleNamespace(
        CreateJobObject=lambda *a: "job",
        QueryInformationJobObject=lambda *a: {
            "BasicLimitInformation": {"LimitFlags": 0}
        },
        SetInformationJobObject=lambda j, kind, limits: events.append(
            ("limits", limits["BasicLimitInformation"]["LimitFlags"])
        ),
        JobObjectExtendedLimitInformation=9,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=8192,
        AssignProcessToJobObject=assign,
        TerminateJobObject=lambda *a: events.append("terminate_job"),
    )
    process = SimpleNamespace(
        CreateProcess=create,
        CREATE_SUSPENDED=4,
        CREATE_NO_WINDOW=8,
        STARTUPINFO=lambda: None,
        ResumeThread=resume,
        GetExitCodeProcess=lambda p: 1 if outcome == "error" else 0,
        TerminateProcess=lambda *a: events.append("terminate_process"),
    )
    monkeypatch.setitem(office.sys.modules, "win32job", job)
    monkeypatch.setitem(office.sys.modules, "win32process", process)
    monkeypatch.setitem(
        office.sys.modules, "win32event", SimpleNamespace(WaitForSingleObject=wait)
    )
    monkeypatch.setitem(
        office.sys.modules,
        "win32api",
        SimpleNamespace(
            CloseHandle=lambda h: events.append(("close", h)), error=OSError
        ),
    )
    if outcome == "success":
        office.run_office(["office", "path with spaces"])
    else:
        with pytest.raises((OSError, subprocess.SubprocessError)):
            office.run_office(["office", "path with spaces"])
    assert ("limits", 8192) in events
    assert ("close", "job") in events
    if outcome != "spawn":
        assert ("close", "thread") in events and ("close", "process") in events
        assert ("wait", 5000) in events
        assert (
            "terminate_process" if outcome == "assign" else "terminate_job"
        ) in events
    if "resume" in events:
        assert events.index("assign") < events.index("resume")


def test_overlap_rejected_and_lock_released(monkeypatch):
    def conversion(content, engine):
        with pytest.raises(pdf.PdfUnavailable, match="Outra conversão"):
            pdf.convert(b"second")
        raise OSError("failed")

    monkeypatch.setattr(pdf, "_convert", conversion)
    with pytest.raises(OSError):
        pdf.convert(b"first")
    assert not pdf._CONVERSION_LOCK.locked()


def test_lock_released_on_success(monkeypatch):
    monkeypatch.setattr(pdf, "_convert", lambda *a: (b"pdf", "test"))
    assert pdf.convert(b"docx") == (b"pdf", "test")
    assert not pdf._CONVERSION_LOCK.locked()


@pytest.mark.skipif(
    sys.platform != "win32", reason="Native Windows Job Object integration"
)
@pytest.mark.parametrize("outcome", ["success", "error", "timeout"])
def test_real_windows_descendant_terminated(tmp_path, monkeypatch, outcome):
    import win32api
    import win32event
    import win32process

    marker = tmp_path / "child.pid"
    script = tmp_path / "wrapper.py"
    script.write_text(
        "import subprocess, sys, time\nfrom pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "Path(sys.argv[1]).write_text(str(child.pid))\n"
        "if sys.argv[2] == 'timeout': time.sleep(60)\n"
        "sys.exit(2 if sys.argv[2] == 'error' else 0)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(office, "TIMEOUT", 3)
    args = [sys.executable, str(script), str(marker), outcome]
    if outcome == "success":
        office.run_office(args)
    else:
        with pytest.raises(subprocess.SubprocessError):
            office.run_office(args)
    child_pid = int(marker.read_text())
    try:
        handle = win32api.OpenProcess(0x100000 | 0x1000 | 1, False, child_pid)
    except win32api.error as exc:
        assert exc.winerror == 87  # PID no longer exists
    else:
        try:
            assert win32event.WaitForSingleObject(handle, 5000) == 0
            assert win32process.GetExitCodeProcess(handle) != 259
        finally:
            # Limit cleanup to the synthetic child if an assertion detects a leak.
            if win32process.GetExitCodeProcess(handle) == 259:
                win32process.TerminateProcess(handle, 1)
            win32api.CloseHandle(handle)
