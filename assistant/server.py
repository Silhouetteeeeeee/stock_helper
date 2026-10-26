"""本地 Streamlit 服务的启动与停止。

Web 助手跑在独立进程里，避免 Streamlit/Tornado 与 Qt 事件循环互相干扰。
行情窗口第一次点 AI 时启动服务并打开浏览器；之后复用同一个进程，
窗口关闭时只停掉自己启动的那个（手动起的 dev 服务不碰）。
"""

from __future__ import annotations

import atexit
import importlib.util
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEBAPP = Path(__file__).resolve().parent / "webapp.py"
DEFAULT_PORT = 8501
HEALTH_PATH = "/_stcore/health"
START_TIMEOUT = 30.0          # Streamlit 冷启动要导入 pandas/altair，留足时间
POLL_INTERVAL = 0.25
LOG_PATH = PROJECT_ROOT / "data" / "assistant_web.log"

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_process: subprocess.Popen | None = None
_port: int | None = None
_owned = False                  # 服务是不是本进程启动的
_log = None
_job = None                     # Windows Job Object，父进程一死就带走子进程


# ---------------------------------------------------------------------------
# 父子进程绑定：Windows 上终止进程不会自动杀掉子进程，IDE 强杀时 atexit
# 根本不会执行，Streamlit 就会变成孤儿占着端口。把一个「句柄关闭即杀光
# 进程」的 Job Object 挂在子进程上，父进程无论怎么死，系统都会清理它。
# ---------------------------------------------------------------------------
def _kill_on_close_job():
    """创建 kill-on-close 的 Job Object；非 Windows 或失败时返回 None。"""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class _ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimits),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        info = _ExtendedLimits()
        # 0x2000 = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        info.BasicLimitInformation.LimitFlags = 0x2000
        # 9 = JobObjectExtendedLimitInformation
        if not kernel32.SetInformationJobObject(
            ctypes.c_void_p(job), 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            kernel32.CloseHandle(ctypes.c_void_p(job))
            return None
        return job
    except Exception:  # noqa: BLE001 - 取不到就用原来的显式停止兜底
        return None


def _assign_to_job(job, pid: int) -> bool:
    """把子进程放进 Job Object；失败返回 False，不影响服务本身。"""
    if job is None or os.name != "nt":
        return False
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        # PROCESS_ALL_ACCESS
        handle = kernel32.OpenProcess(0x1F0FFF, 0, int(pid))
        if not handle:
            return False
        try:
            return bool(
                kernel32.AssignProcessToJobObject(
                    ctypes.c_void_p(job), ctypes.c_void_p(handle)
                )
            )
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:  # noqa: BLE001
        return False


def url_for(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def is_healthy(port: int, timeout: float = 0.5) -> bool:
    """Streamlit 的健康检查；端口没开或不是 Streamlit 时返回 False。"""
    try:
        with _opener.open(
            f"http://127.0.0.1:{port}{HEALTH_PATH}", timeout=timeout
        ) as response:
            body = response.read(16).strip().lower()
            return response.status == 200 and body in (b"ok", b"")
    except Exception:  # noqa: BLE001 - 连不上就是没起来
        return False


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def find_free_port(preferred: int = DEFAULT_PORT) -> int:
    """优先用 8501；被别的程序占了就让系统分配一个空闲端口。"""
    if _port_available(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_command(port: int) -> list[str]:
    """启动命令；只监听本机，关掉文件监视与使用统计。"""
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(WEBAPP),
        "--server.headless=true",
        "--server.address=127.0.0.1",
        f"--server.port={port}",
        "--server.fileWatcherType=none",
        "--server.runOnSave=false",
        "--browser.gatherUsageStats=false",
    ]


def _open_log():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    return open(LOG_PATH, "ab", buffering=0)


def _stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


def start_server(port: int) -> str:
    """起一个 Streamlit 子进程并等到健康检查通过。"""
    global _process, _port, _owned, _log, _job
    if importlib.util.find_spec("streamlit") is None:
        raise RuntimeError("未安装 Streamlit，请先执行：pip install streamlit")

    _log = _open_log()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        _process = subprocess.Popen(
            build_command(port),
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=_log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    except OSError as error:
        if _log is not None:
            _log.close()
            _log = None
        raise RuntimeError(f"无法启动 Streamlit：{error}") from error

    _port = port
    _owned = True
    # 挂到 Job Object 上：主进程被 IDE 强杀时，Windows 也会结束这个子进程
    if _job is None:
        _job = _kill_on_close_job()
    _assign_to_job(_job, _process.pid)
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if is_healthy(port):
            return url_for(port)
        if _process.poll() is not None:
            break
        time.sleep(POLL_INTERVAL)

    _stop_process(_process)
    _process = None
    _port = None
    _owned = False
    if _log is not None:
        _log.close()
        _log = None
    raise RuntimeError(f"投资助手启动失败，详情见 {LOG_PATH}")


def ensure_server() -> str:
    """返回可用页面地址：复用已有服务，必要时启动并等待就绪。"""
    global _process, _port, _owned
    if _process is not None and _process.poll() is None and _port is not None:
        if is_healthy(_port):
            return url_for(_port)
    # 上次启动的进程已经退出
    if _process is not None:
        _stop_process(_process)
        if _log is not None:
            _log.close()
            _log = None
        _process = None
        _port = None
        _owned = False

    # 端口上已有一个健康的 Streamlit（例如手动起的 dev 服务）：直接复用
    if is_healthy(DEFAULT_PORT):
        _port = DEFAULT_PORT
        _owned = False
        return url_for(DEFAULT_PORT)

    return start_server(find_free_port())


def stop_server() -> None:
    """关闭本进程启动的服务；外部启动的服务保持不动。"""
    global _process, _port, _owned, _log
    if _process is not None and _owned:
        _stop_process(_process)
    _process = None
    _port = None
    _owned = False
    if _log is not None:
        try:
            _log.close()
        except OSError:
            pass
        _log = None


atexit.register(stop_server)
