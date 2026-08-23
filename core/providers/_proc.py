"""子进程启动助手：保证父进程（下载器）无论以何种方式退出，都关闭其启动的 API 服务。

平台机制各用系统级"父亡即杀"：
- Windows：作业对象（Job Object）+ JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE。
  只要持有该作业句柄的下载器进程终止（含被强制结束），操作系统自动终止
  作业内所有子进程（子进程派生的孙进程在 Windows 上默认也归入同一作业）。
  作业句柄挂到 Popen 实例上并随 bridge 持有的 proc 一起存活，防止句柄被提前
  释放而误杀仍运行的子进程。
- Linux：fork 后 exec 前用 prctl(PR_SET_PDEATHSIG, SIGTERM) 设置"父进程死亡
  即发 SIGTERM"，并在设置后回查 getppid()，消除"父进程恰好在设置前退出"的竞态
  （父已退出则子进程被 init 收养，ppid 变为 1，此时自杀）。

spawn_protected 是本模块对外唯一入口，两个 bridge（qq/netease）共用同一行调用；
平台差异集中收敛于此，便于维护。任一平台机制初始化失败时静默回退为朴素 Popen，
此时正常退出仍由 bridge.stop() / atexit 兜底。
"""

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

# 父进程死亡后发送给 Linux 子进程的信号
_LINUX_PDEATHSIG = 15  # SIGTERM


def _linux_pdeathsig_preexec() -> None:
    """Linux 子进程内（fork 后、exec 前）启用父亡信号，并回查 ppid 防竞态。

    该回调只在子进程地址空间执行，须自包含（不依赖闭包安全），失败只静默忽略。
    """
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        PR_SET_PDEATHSIG = 1
        libc.prctl(PR_SET_PDEATHSIG, _LINUX_PDEATHSIG)
        # 竞态回查：若父进程在 prctl 设置前已退出，本进程已被 init(ppid=1) 收养，
        # 此时父亡信号不会再触发，改为立即自杀。
        if os.getppid() == 1:
            os.kill(os.getpid(), _LINUX_PDEATHSIG)
    except Exception:
        # 设置失败则保持默认行为；正常退出仍由 bridge.stop() 兜底
        pass


def _attach_windows_job(proc: "subprocess.Popen") -> None:
    """把子进程放入 KILL_ON_JOB_CLOSE 作业，句柄挂在 proc 上防提前释放。

    任一环节失败均静默返回，保持朴素 Popen 行为（不阻断启动）。
    """
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        PROCESS_SET_QUOTA = 0x0100
        PROCESS_TERMINATE = 0x0001
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JobObjectExtendedLimitInformation = 9

        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        # 匿名作业（NULL 名字）
        h_job = kernel32.CreateJobObjectW(None, None)
        if not h_job:
            return

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
                h_job, JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(h_job)
            return

        h_proc = kernel32.OpenProcess(
            PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, proc.pid)
        if not h_proc:
            kernel32.CloseHandle(h_job)
            return
        try:
            assigned = kernel32.AssignProcessToJobObject(h_job, h_proc)
        finally:
            kernel32.CloseHandle(h_proc)
        if not assigned:
            kernel32.CloseHandle(h_job)
            return

        # 句柄挂在 proc 上：bridge 持有 proc，进程运行期间句柄不释放；
        # stop() 后 proc 被置 None，句柄随对象释放（此时子进程已被停掉）
        proc._parent_death_job_handle = h_job
    except Exception:
        logger.warning(
            "Windows 作业对象保护未生效，回退为朴素 Popen"
            "（正常退出仍会关闭 API 服务）", exc_info=True)


def spawn_protected(cmd, cwd=None, env=None, stdout=None, stderr=None):
    """按平台为子进程启用"父进程死亡即终止"，返回 subprocess.Popen 实例。

    参数透传给 subprocess.Popen；平台差异（Windows 的 CREATE_NO_WINDOW + 作业
    对象、Linux 的 PDEATHSIG preexec）在本函数内封装。

    :param cmd: 可执行命令列表
    :param cwd: 子进程工作目录
    :param env: 子进程环境变量
    :param stdout/stderr: 子进程标准输出/错误（透传）
    """
    kwargs = {"cwd": cwd, "env": env, "stdout": stdout, "stderr": stderr}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(cmd, **kwargs)
        _attach_windows_job(proc)
        return proc
    if sys.platform.startswith("linux"):
        kwargs["preexec_fn"] = _linux_pdeathsig_preexec
    return subprocess.Popen(cmd, **kwargs)