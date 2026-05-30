"""Session-scoped worker lifecycle for subagents."""

import json
import queue
import threading
import time
from dataclasses import dataclass, field

from .worker_execution import run_worker
from .worker_runtime import build_child_runtime
from .workspace import now


@dataclass
class WorkerTask:
    """
    工作任务的数据结构
    保存一个子代理任务的所有信息：ID、描述、类型、权限、运行状态等
    """
    id: str
    description: str
    subagent_type: str
    write_scope: tuple[str, ...]
    runtime: object
    thread: threading.Thread | None = None
    stop_requested: bool = False
    state: dict = field(default_factory=dict)


class WorkerManager:
    """
    工作器管理器
    核心功能：
    1. 创建/管理子代理任务
    2. 后台线程执行任务
    3. 任务状态持久化（保存到session）
    4. 停止任务、关闭所有任务
    """
    def __init__(self, runtime):
        """
        初始化 WorkerManager，设置会话状态和内存任务映射。
        """
        self.runtime = runtime
        self.runtime.session.setdefault("workers", {"next_id": 1, "items": []})  # 持久化存储任务列表和下一个 ID
        self._tasks = {}          # 内存中的任务映射 {task_id: WorkerTask}
        self._lock = threading.Lock()  # 线程安全锁，保证多线程环境下的状态一致性
        self._notifications = queue.Queue()  # 通知队列


    @property
    def state(self):
        """
        获取当前工作器管理器的状态，包括下一个任务 ID 和任务列表。
        """
        return self.runtime.session.setdefault("workers", {"next_id": 1, "items": []})

    def spawn(self, description, prompt, subagent_type="worker", write_scope=None):
        """
        创建并启动一个新的子代理任务
        :param description: 任务描述
        :param prompt: 执行提示词
        :param subagent_type: 代理类型
        :param write_scope: 写入权限
        :return: 任务公开信息
        """
        subagent_type = _clean_type(subagent_type)
        # 计划模式限制：只允许 Explore 类型
        if self.runtime.runtime_mode == "plan" and subagent_type != "Explore":
            raise ValueError("plan mode only allows Explore agents")
        # 创建任务
        task = self._new_task(description, subagent_type, write_scope)
        # 存入内存
        self._tasks[task.id] = task
        # 如果是后台模式：创建线程异步执行任务
        if self._can_run_background():
            self._start_background(task, prompt, action="spawn")
            return self._public_payload(task, status="started")
        # 否则是前台模式：直接执行任务
        run_worker(self, task, prompt, action="spawn")
        return self._public_payload(task)

    def continue_task(self, task_id, message):
        """
        继续执行一个已存在的子智能体任务。
        """
        task = self._get_active_task(task_id)
        item = self._get_item(task_id)
        # 检查任务状态：正在运行则不能继续
        if item.get("status") in {"running", "stopping"}:
            raise ValueError(f"worker is running: {task_id}")
        # 模式检查：plan 模式只允许 Explore 类型的任务
        if self.runtime.runtime_mode == "plan" and task.subagent_type != "Explore":
            raise ValueError("plan mode only allows Explore agents")
        # 如果是后台模式：创建线程异步执行任务
        if self._can_run_background():
            self._start_background(task, message, action="continue")
            return self._public_payload(task, status="started")
        # 否则是前台模式：直接执行任务
        run_worker(self, task, message, action="continue")
        return self._public_payload(task)

    def stop_task(self, task_id):
        """
        停止一个正在运行的子智能体任务。
        """
        item = self._get_item(task_id)
        # 只有运行中才能停止
        if item["status"] == "running":
            task = self._tasks.get(str(task_id))
            if task is not None:
                self._request_stop(task)  # 设置任务停止标志
            # 更新状态为 stopping
            item["status"] = "stopping"
            item["updated_at"] = now()

            # 发送停止请求通知
            self.runtime.session_event_bus.emit(
                "worker_stop_requested", {"worker_id": item["id"], "status": "stopping"}
            )
            self._save() # 保存任务状态
        return {
            "task_id": item["id"],
            "status": item["status"],
            "description": item["description"],
        }

    def shutdown(self, timeout=2.0):
        """
        关闭所有正在运行的子智能体任务。
        """
        # 给所有运行中任务发停止信号
        tasks = list(self._tasks.values())
        for task in tasks:
            item = self._get_item(task.id)
            if item.get("status") in {"running", "stopping"}:
                self._request_stop(task)
                with self._lock:
                    item["status"] = "stopping"
                    item["updated_at"] = now()
                self.runtime.session_event_bus.emit(
                    "worker_stop_requested",
                    {"worker_id": item["id"], "status": "stopping"},
                )
        if tasks:
            self._save()
        # 等待线程结束
        deadline = time.monotonic() + float(timeout)
        for task in tasks:
            thread = task.thread
            if thread is None or not thread.is_alive():
                continue
            remaining = max(0.0, deadline - time.monotonic())
            if remaining:
                thread.join(remaining)  # 最多等待 remaining 秒
        return {"stopped": sum(1 for task in tasks if task.stop_requested)}


    # ------------------------------
    # 内部工具方法
    # ------------------------------


    def to_dict(self):
        """
        将任务管理器状态转换为字典，用于序列化
        """
        return {
            "next_id": int(self.state.get("next_id", 1)),
            "items": [dict(item) for item in self.state.get("items", [])],
        }

    def _new_task(self, description, subagent_type, write_scope):
        """
        创建一个新的子智能体任务
        """
        with self._lock:
            worker_id = f"agent_{int(self.state.get('next_id', 1))}"
            self.state["next_id"] = int(self.state.get("next_id", 1)) + 1
        scope = tuple(_clean_scope(write_scope))
        child = build_child_runtime(self.runtime, subagent_type, scope)
        item = {
            "id": worker_id,
            "description": str(description or "").strip() or "Worker task",
            "subagent_type": subagent_type,
            "write_scope": list(scope),
            "status": "idle",
            "result": "",
            "tool_steps": 0,
            "attempts": 0,
            "duration_ms": 0,
            "notification_drained": False,
            "created_at": now(),
            "updated_at": now(),
        }
        with self._lock:
            self.state.setdefault("items", []).append(item)
            self._save()
        return WorkerTask(worker_id, item["description"], subagent_type, scope, child)

    def _can_run_background(self):
        """
        判断是否支持后台运行（需要模型客户端）
        """
        return getattr(self.runtime, "model_client_factory", None) is not None

    def _start_background(self, task, prompt, action):
        """
        创建一个后台线程执行任务
        """
        thread = threading.Thread(
            target=run_worker,
            args=(self, task, prompt, action),
            daemon=True,
            name=f"pico-worker-{task.id}",
        )
        task.thread = thread
        thread.start()

    def _request_stop(self, task):
        """
        设置任务停止标志
        """
        task.stop_requested = True  # 请求停止任务
        abort = getattr(task.runtime, "abort_current_turn", None)  # 获取当前轮次的取消函数
        if callable(abort):
            abort()

    def drain_notifications(self):
        """
        从通知队列中提取所有通知
        """
        drained = []
        while True:
            try:
                task_id, notification = self._notifications.get_nowait()
            except queue.Empty:
                break
            item = self._get_item(task_id)
            with self._lock:
                if item.get("notification_drained"):
                    continue
                item["notification_drained"] = True
                item["updated_at"] = now()
            drained.append(notification)
        if drained:
            self._save()
        return drained

    def _get_active_task(self, task_id):
        """
        从会话状态获取活动任务信息
        """
        task = self._tasks.get(str(task_id))
        if task is None:
            raise ValueError(f"unknown or inactive worker: {task_id}")
        return task

    def _get_item(self, task_id):
        """
        从会话状态获取任务信息
        """
        for item in self.state.setdefault("items", []):
            if item.get("id") == str(task_id):
                return item
        raise ValueError(f"unknown worker: {task_id}")

    def _public_payload(self, task, status=None):
        """
        生成任务的公共状态更新,返回给外部的任务信息
        :param task: 任务实例
        :param status: 任务状态，可选
        :return: 包含任务状态的字典
        """
        item = self._get_item(task.id)
        return {
            "task_id": task.id,
            "status": status or item["status"],
            "description": task.description,
        }

    def _save(self):
        """
        保存会话状态
        """
        self.runtime.session_path = self.runtime.session_store.save(
            self.runtime.session
        )

# ------------------------------
# 工具函数
# ------------------------------


def _clean_type(value):
    """
    清理子智能体类型，确保是 worker 或 Explore
    """
    subagent_type = str(value or "worker").strip()
    if subagent_type not in {"worker", "Explore"}:
        raise ValueError("subagent_type must be worker or Explore")
    return subagent_type


def _clean_scope(value):
    """
    清理写入范围，确保是字符串列表
    """
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("write_scope must be a list of workspace paths")
    return [str(item).strip() for item in value if str(item).strip()]


def dumps_payload(payload):
    """
    将字典转换为 JSON 字符串
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
