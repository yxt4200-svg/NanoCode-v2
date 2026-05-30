"""Runtime permission decisions for tool execution."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PermissionDecision:
    decision: str
    reason: str
    security_event_type: str = ""

    @classmethod
    def allow(cls, reason):
        return cls("allow", reason)

    @classmethod
    def deny(cls, reason, security_event_type=""):
        return cls("deny", reason, security_event_type)

    @property
    def allowed(self):
        return self.decision == "allow"


class PermissionChecker:
    def __init__(self, runtime):
        self.runtime = runtime

    def check(self, tool, args):
        args = args or {}

        # 1. 工具配置文件检查
        profile = self.runtime.active_tool_profile
        if not profile.allows(tool.name):
            if profile.name == "plan":
                return PermissionDecision.deny("plan_mode_tool_not_allowed", "plan_mode_write_guard")
            return PermissionDecision.deny("tool_not_allowed")

        # 2. Plan Mode 特殊检查
        if self.runtime.runtime_mode == "plan":
            return self._check_plan(tool, args)

        # 3. Write Scope 检查（仅 write_file/patch_file）
        if tool.name in {"write_file", "patch_file"} and getattr(self.runtime, "write_scope", ()):
            return self._check_write_scope(tool, args)

        # 4. 只读工具直接允许
        if tool.read_only:
            return PermissionDecision.allow("read_only")

        # 5. 运行时只读模式检查
        if self.runtime.read_only:
            return PermissionDecision.deny("approval_denied", "read_only_block")

        # 6. 审批策略决策
        if self.runtime.approval_policy == "auto":
            return PermissionDecision.allow("approval_auto")
        if self.runtime.approval_policy == "never":
            return PermissionDecision.deny("approval_denied", "approval_denied")
        if self.runtime.approve(tool.name, args):
            return PermissionDecision.allow("approval_prompt")
        return PermissionDecision.deny("approval_denied", "approval_denied")


    def _check_plan(self, tool, args):
        if tool.read_only:
            return PermissionDecision.allow("plan_read_only")
        if tool.name not in {"write_file", "patch_file"}:
            return PermissionDecision.deny("plan_mode_tool_not_allowed", "plan_mode_write_guard")
        requested = self.runtime.path(args.get("path", ""))
        active = self.runtime.path(self.runtime.plan_mode.plan_path)
        if Path(requested) != Path(active):
            return PermissionDecision.deny("plan_mode_path_mismatch", "plan_mode_write_guard")
        return PermissionDecision.allow("plan_artifact_write")

    def _check_write_scope(self, tool, args):
        # 1. 获取请求的路径
        requested = self.runtime.path(args.get("path", ""))

        # 2. 遍历所有允许的写入范围
        for raw_scope in self.runtime.write_scope:
            scope = self.runtime.path(raw_scope)
            try:
                # 3. 检查请求路径是否在允许范围内
                requested.relative_to(scope)
                return PermissionDecision.allow("write_scope")
            except ValueError:
                # 不在当前范围内，继续检查下一个
                continue

        # 4. 所有范围都不匹配，拒绝
        return PermissionDecision.deny("write_scope_mismatch", "write_scope_guard")
