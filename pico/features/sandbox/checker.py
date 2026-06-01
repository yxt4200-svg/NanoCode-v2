"""Sandbox backend availability checks."""


class SandboxChecker:
    def __init__(self, which):
        self.which = which  # 命令查找工具（默认是 shutil.which）

    def backend_path(self, backend):
        # 如果是 auto 模式，自动选择 bubblewrap
        backend = "bubblewrap" if backend == "auto" else backend

        # 如果是 none 或 off，直接返回空（不可用）
        if backend in {"none", "off"}:
            return ""

        # 如果是 bubblewrap，检查 bubblewrap（bwrap 命令）是否安装在系统中
        if backend == "bubblewrap":
            return self.which("bwrap") or ""  # 返回命令路径或空字符串

        return ""