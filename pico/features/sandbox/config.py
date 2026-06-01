"""Sandbox configuration for shell execution."""

from dataclasses import dataclass

SANDBOX_MODES = {"off", "best_effort", "required"}
SANDBOX_BACKENDS = {"auto", "bubblewrap", "none"}


@dataclass(frozen=True)
class SandboxConfig:
    """沙箱配置类"""
    mode: str = "off"              # 沙箱模式：off=关闭, best_effort=尽力而为, required=强制
    backend: str = "auto"          # 沙箱后端：auto=自动, bubblewrap=使用Bubblewrap, none=不使用
    workspace_write: bool = True   # 是否允许写入工作区
    excluded_commands: tuple[str, ...] = ()  # 排不走沙箱的命令列表（如 git），只在非 required 模式下生效
    extra_readonly_paths: tuple[str, ...] = ()  # 额外挂载的只读路径
    deny_read: tuple[str, ...] = ()  # 禁止读取的路径
    deny_write: tuple[str, ...] = ()  # 禁止写入的路径

    @property
    def enabled(self):
        return self.mode != "off"   # 判断沙箱是否启用


def resolve_sandbox_config(values):
    """沙箱配置解析函数"""
    sandbox = dict((values or {}).get("sandbox", {}) or {})
    filesystem = dict(sandbox.get("filesystem", {}) or {})
    mode = str(sandbox.get("mode", "off") or "off")
    backend = str(sandbox.get("backend", "auto") or "auto")
    # 校验模式和后端是否合法
    if mode not in SANDBOX_MODES:
        raise ValueError(f"sandbox.mode must be one of {sorted(SANDBOX_MODES)}")
    if backend not in SANDBOX_BACKENDS:
        raise ValueError(f"sandbox.backend must be one of {sorted(SANDBOX_BACKENDS)}")
    # 构建配置对象
    return SandboxConfig(
        mode=mode,
        backend=backend,
        workspace_write=bool(sandbox.get("workspace_write", True)),
        excluded_commands=tuple(
            str(item) for item in sandbox.get("excluded_commands", []) or []
        ),
        extra_readonly_paths=tuple(
            str(item) for item in filesystem.get("extra_readonly_paths", []) or []
        ),
        deny_read=tuple(str(item) for item in filesystem.get("deny_read", []) or []),
        deny_write=tuple(str(item) for item in filesystem.get("deny_write", []) or []),
    )
