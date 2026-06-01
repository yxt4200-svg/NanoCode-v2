"""Optional shell sandbox runner."""

import subprocess
from pathlib import Path
from shutil import which as default_which

from .checker import SandboxChecker
from .command_matcher import command_is_excluded
from .config import SandboxConfig


class SandboxRunner:
    def __init__(self, config=None, *, which=None, run=None, emit_event=None):
        self.config = config or SandboxConfig()  # 配置对象
        self.which = which or default_which      # 命令查找工具
        self.run_process = run                   # 进程运行函数（用于测试）
        self.emit_event = emit_event or (lambda event, payload: None)  # 事件发射器

    def run(self, command, *, cwd, env, timeout):
        config = self.config

        # 情况1：off 模式 或 沙箱模式不是 "required" 且命令在排除列表中 → 直接执行
        if config.mode == "off" or (
            config.mode != "required"
            and command_is_excluded(command, config.excluded_commands)
        ):
            return self._plain(command, cwd=cwd, env=env, timeout=timeout)

        # 情况2：沙箱开启。检查沙箱后端 bubblewrap 是否可用
        backend_path = SandboxChecker(self.which).backend_path(config.backend)
        # 情况2.1：bubblewrap 后端不可用
        if not backend_path:
            self.emit_event(
                "sandbox_unavailable",
                {"mode": config.mode, "backend": config.backend, "command": str(command or "")[:200]},
            )
            # required模式下直接报错
            if config.mode == "required":
                raise RuntimeError("sandbox required but unavailable")
            # best_effort 模式降级为普通执行
            return self._plain(command, cwd=cwd, env=env, timeout=timeout)

        # 情况2.2：bubblewrap 后端可用，构建 bubblewrap 命令行参数
        argv = self._bubblewrap_argv(backend_path, command, Path(cwd), config)
        run_process = self.run_process or subprocess.run
        return run_process(
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
        )

    def _plain(self, command, *, cwd, env, timeout):
        run_process = self.run_process or subprocess.run
        return run_process(
            command,
            cwd=cwd,
            shell=True,  # 使用shell执行
            capture_output=True,  # 捕获输出
            text=True,  # 返回文本格式
            timeout=timeout,
            env=env,
        )

    def _bubblewrap_argv(self, backend_path, command, cwd, config):
        """构建 bubblewrap 命令行参数"""
        argv = [
            backend_path,  # bubblewrap 程序本身
            # 基础环境
            "--die-with-parent",  # 父进程退出时子进程也退出
            "--proc", "/proc",    # 挂载 /proc 虚拟文件系统（让进程能看到自己）
            "--dev", "/dev",      # 挂载 /dev 设备目录（让进程能访问基本设备）
            # 只读系统
            "--ro-bind", "/usr", "/usr",  # 把系统的 /usr 挂载进去，只读
            "--ro-bind", "/bin", "/bin",  # 把系统的 /bin 挂载进去，只读
            "--ro-bind", "/lib", "/lib",  # 把系统的 /lib 挂载进去，只读
            "--ro-bind", "/lib64", "/lib64",  # 64位库目录，只读
        ]

        # 添加可写工作区（把 /workspace 可写挂载进去，这是模型唯一能修改代码的地方）
        bind_mode = "--bind" if config.workspace_write else "--ro-bind"
        argv.extend([bind_mode, str(cwd), str(cwd)])

        # 添加额外只读路径
        for path in config.extra_readonly_paths:
            argv.extend(["--ro-bind", path, path])

        # 添加禁止访问的路径（用 tmpfs 覆盖禁止访问的路径，进程在那里写东西都是临时的，进程退出就消失）
        for path in (*config.deny_read, *config.deny_write):
            argv.extend(["--tmpfs", path])

        # 添加命令执行部分（切换到工作目录并执行命令，使用 shell 执行命令）
        argv.extend(["--chdir", str(cwd), "--", "/bin/sh", "-lc", str(command)])
        return argv

