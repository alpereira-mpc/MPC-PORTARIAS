"""Run one isolated LibreOffice process tree; never kill by executable name."""

import os
import signal
import subprocess
import sys

TIMEOUT = 55


def run_office(args):
    if sys.platform == "win32":
        import win32api

        try:
            return _run_windows(args)
        except win32api.error as exc:
            raise OSError("Falha no controle do processo LibreOffice") from exc
    process = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        code = process.wait(timeout=TIMEOUT)
        if code:
            raise subprocess.CalledProcessError(code, args)
    finally:
        # The wrapper may have exited while soffice.bin is still alive.
        # The session/group belongs only to this conversion, even on success.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        finally:
            process.wait(timeout=5)


def _run_windows(args):
    import win32api
    import win32event
    import win32job
    import win32process

    job = win32job.CreateJobObject(None, "")
    process = thread = None
    assigned = False
    try:
        limits = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation
        )
        limits["BasicLimitInformation"][
            "LimitFlags"
        ] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, limits
        )
        # Suspend before assigning to prevent children escaping the Job Object.
        process, thread, _, _ = win32process.CreateProcess(
            args[0],
            subprocess.list2cmdline(args),
            None,
            None,
            False,
            win32process.CREATE_SUSPENDED | win32process.CREATE_NO_WINDOW,
            None,
            None,
            win32process.STARTUPINFO(),
        )
        win32job.AssignProcessToJobObject(job, process)
        assigned = True
        win32process.ResumeThread(thread)
        if win32event.WaitForSingleObject(process, TIMEOUT * 1000) != 0:
            raise subprocess.TimeoutExpired(args, TIMEOUT)
        code = win32process.GetExitCodeProcess(process)
        if code:
            raise subprocess.CalledProcessError(code, args)
    finally:
        try:
            if assigned:
                win32job.TerminateJobObject(job, 1)
            elif process is not None:
                win32process.TerminateProcess(process, 1)
            if process is not None:
                if win32event.WaitForSingleObject(process, 5000) != 0:
                    raise OSError("Não foi possível encerrar o conversor PDF")
        finally:
            # Also kills remaining children if explicit termination failed.
            win32api.CloseHandle(job)
            if thread is not None:
                win32api.CloseHandle(thread)
            if process is not None:
                win32api.CloseHandle(process)
