"""Named tool capability surfaces for runtime modes."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSetProfile:
    name: str
    allowed_tools: frozenset[str]

    def allows(self, tool_name):
        return tool_name in self.allowed_tools


def build_tool_profiles(tools):
    all_tools = frozenset(tools)
    coordinator_tools = frozenset({"agent", "send_message", "task_stop"})
    mode_tools = frozenset({"enter_plan_mode", "exit_plan_mode"})
    interactive_tools = frozenset({"ask_user"})

    # 只读工具：不包含协调工具、模式工具和交互工具的工具集合
    read_only = (
        frozenset(name for name, tool in tools.items() if tool.read_only)
        - coordinator_tools
        - mode_tools
        - interactive_tools
    )

    # 计划模式工具：只读工具加上部分协调工具和模式工具
    plan_tools = read_only | frozenset({
        "write_file",      # 允许写文件（但受路径限制）
        "patch_file",      # 允许补丁文件（但受路径限制）
        "agent",           # 允许启动子代理（但只允许 Explore）
        "send_message",    # 允许发送消息
        "task_stop",       # 允许停止任务
        "ask_user",        # 允许询问用户
        "exit_plan_mode",  # 允许退出计划模式
})

    # 梦想模式工具：只读工具加上写入协调工具和模式工具
    dream_tools = read_only | frozenset({"write_file", "patch_file"})
    # 工作模式工具：所有工具减去协调工具、模式工具、交互工具和 run_shell 工具
    worker_tools = (
        all_tools
        - coordinator_tools
        - mode_tools
        - interactive_tools
        - frozenset({"run_shell"})  # 不允许在工作模式下运行 shell
    )
    return {
        "default": ToolSetProfile("default", all_tools),
        "plan": ToolSetProfile("plan", plan_tools & all_tools),
        "dream": ToolSetProfile("dream", dream_tools & all_tools),
        "readonly": ToolSetProfile("readonly", read_only),
        "worker": ToolSetProfile("worker", worker_tools),
    }
