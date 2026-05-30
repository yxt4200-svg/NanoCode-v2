"""Child runtime construction for worker tasks."""

from .workspace import WorkspaceContext


def build_child_runtime(parent, subagent_type, write_scope):
    from .runtime import Pico

    child = Pico(
        model_client=new_model_client(parent),
        workspace=WorkspaceContext.build(parent.root, repo_root_override=parent.root),
        session_store=parent.session_store,
        run_store=parent.run_store,
        # Explore和Worker的边界1：审批策略：Explore 不会审批写入操作，worker 自动审批
        approval_policy="never" if subagent_type == "Explore" else "auto",
        max_steps=parent.max_steps,
        max_new_tokens=parent.max_new_tokens,
        depth=parent.depth + 1,
        max_depth=parent.max_depth,
        # Explore和Worker的边界5=2：只读模式：Explore 强制只读模式，worker 根据写入权限决定是否只读
        read_only=subagent_type == "Explore"
        or (subagent_type == "worker" and not write_scope),
        secret_env_names=parent.secret_env_names,
        shell_env_allowlist=parent.shell_env_allowlist,
        feature_flags=parent.feature_flags,
        write_scope=write_scope,
        model_client_factory=getattr(parent, "model_client_factory", None),
        sandbox_config=getattr(parent, "sandbox_config", None),
        ask_user_callback=getattr(parent, "ask_user_callback", None),
    )
    # Explore和Worker的边界3：工具权限：Explore 只能使用只读工具，worker 有权限使用所有工具
    child.set_tool_profile("readonly" if subagent_type == "Explore" else "worker")
    child.refresh_prefix(force=True)
    return child


def new_model_client(parent):
    """
    创建新的子代理模型客户端实例
    """
    factory = getattr(parent, "model_client_factory", None)
    if factory is not None:
        return factory()  # 使用自定义工厂，每个子代理获得独立的 client
    return parent.model_client  # 使用父代理的模型客户端，所有子代理共享同一个 client
