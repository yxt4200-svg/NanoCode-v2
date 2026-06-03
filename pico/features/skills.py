"""Skill discovery, prompt expansion, and slash workflow execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# 以 --- 开头
# 中间是 YAML 内容
# 以 --- 结束
# re.DOTALL 使 . 匹配换行符
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
SKILL_FILE_CREATION_GUIDE = """When creating Pico skill files at .pico/skills/<name>/SKILL.md or skills/<name>/SKILL.md, use frontmatter:
---
name: audit
description: Audit a file
user-invocable: true
---
Audit $ARGUMENTS for risky changes."""


@dataclass(frozen=True)
class Skill:
    name: str                    # 技能名称（新加技能必须填）
    description: str = ""        # 技能描述（新加技能必须填）
    prompt: str = ""             # 核心 prompt 内容（告诉 LLM 遇到什么场景做什么决策）
    source: str = "builtin"      # 来源（builtin/project/user）
    skill_root: str = ""         # 技能文件所在目录
    when_to_use: str = ""        # 使用场景说明
    context: str = "inline"      # 上下文模式（inline/attached）
    allowed_tools: tuple[str, ...] = ()  # 允许使用的工具列表
    argument_hint: str = ""      # 参数提示
    user_invocable: bool = True  # 是否允许用户直接调用
    disable_model_invocation: bool = False  # 是否禁用模型调用
    model: str = ""              # 指定使用的模型
    paths: tuple[str, ...] = ()  # 关联的文件路径
    prompt_fn: Callable[[str], str] | None = None  # 动态 prompt 生成函数（告诉 LLM 遇到什么场景做什么决策，需要动态生成的复杂场景才吧决策手册写这里）

    def render(self, arguments=""):
        text = self.prompt_fn(str(arguments)) if self.prompt_fn else self.prompt
        replacements = {
            "$ARGUMENTS": str(arguments),
            "${PICO_SKILL_DIR}": self.skill_root,
            "${CLAUDE_SKILL_DIR}": self.skill_root,
        }
        if self.argument_hint:
            replacements[f"${{{self.argument_hint}}}"] = str(arguments)
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text.strip()

    def metadata(self):
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "context": self.context,
            "allowed_tools": list(self.allowed_tools),
            "paths": list(self.paths),
            "user_invocable": self.user_invocable,
            "disable_model_invocation": self.disable_model_invocation,
            "model": self.model,
        }


def discover_skills(root, home=None):
    """
    从指定目录扫描技能文件，返回一个包含所有技能的字典。
    """
    # 1. 先加载内置技能
    # 编译时确定	内置技能是系统核心功能，随代码一起发布
    # 性能考虑	函数调用比文件 IO 快，启动更快
    # 安全性	    内置技能经过严格测试，不能被随意修改
    # 版本控制	内置技能与代码版本绑定，便于维护
    skills = {skill.name: skill for skill in bundled_skills()}

    # 2. 定义三层扫描路径
    search_roots = [
        (Path(home or Path.home()) / ".pico" / "skills", "user"),    # 用户级
        (Path(root) / "skills", "project"),                           # 项目级
        (Path(root) / ".pico" / "skills", "project"),                # 项目私有
    ]

    # 3. 逐层扫描并合并
    for directory, source in search_roots:
        for skill in load_skills_from_dir(directory, source=source):
            skills[skill.name] = skill  # 后面的覆盖前面的

    # 4. 返回排序后的技能字典
    return dict(sorted(skills.items()))


def load_skills_from_dir(skills_dir, source):
    skills_dir = Path(skills_dir).expanduser()
    if not skills_dir.exists():
        return []
    files = []
    for path in sorted(skills_dir.iterdir()):
        # 支持两种格式：目录内的 SKILL.md 或直接的 .md 文件
        if path.is_dir() and (path / "SKILL.md").is_file():
            files.append(path / "SKILL.md")
        elif path.is_file() and path.suffix.lower() == ".md":
            files.append(path)
    return [skill for path in files if (skill := load_skill_file(path, source=source))]


def load_skill_file(path, source):
    path = Path(path)
    # 使用 frontmatter 解析器分离元数据和正文
    metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))

    # 确定技能名称：优先使用 metadata 中的 name，否则用目录名或文件名
    default_name = path.parent.name if path.name == "SKILL.md" else path.stem
    name = str(metadata.get("name") or default_name).strip().lstrip("/")

    if not name:
        return None

    # 新增：强制要求 description
    description = _string(metadata.get("description"))
    if not description:
        return None  # 没有描述则加载失败

    return Skill(
        name=name,
        description=_string(metadata.get("description")),
        when_to_use=_string(metadata.get("when_to_use")),
        context=_string(metadata.get("context"), "inline") or "inline",
        allowed_tools=tuple(_list_value(metadata.get("allowed_tools"))),
        argument_hint=_string(metadata.get("arguments") or metadata.get("argument_hint")),
        user_invocable=_bool_value(metadata.get("user_invocable", True)),
        disable_model_invocation=_bool_value(metadata.get("disable_model_invocation", False)),
        model=_string(metadata.get("model")),
        paths=tuple(_list_value(metadata.get("paths"))),
        prompt=body.strip(),
        source=source,
        skill_root=str(path.parent),
    )


def parse_frontmatter(text):
    """
    解析文本中的 frontmatter 格式，返回元数据和正文。
    """
    if not text.startswith("---"):
        return {}, text
    match = FRONTMATTER_RE.match(str(text))
    if not match:
        return {}, str(text)
    metadata = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        # 忽略空行和注释
        if not line or line.startswith("#") or ":" not in line:
            continue
        # 只支持单层键值对
        key, value = line.split(":", 1)
        metadata[key.strip().lower().replace("-", "_")] = _parse_value(value.strip())
    return metadata, str(text)[match.end() :]


def render_prompt_section(skills):
    visible = [skill for skill in list_skills(skills, user_invocable_only=False) if _should_show_in_prompt(skill)]
    if not visible:
        return "Available skills:\n- none"
    lines = ["Available skills:"]
    for skill in visible:
        description = skill.description or skill.when_to_use or "No description"
        suffix = f" — {skill.when_to_use}" if skill.when_to_use else ""
        lines.append(f"- /{skill.name}: {description}{suffix}")
    return "\n".join(lines)


def render_skills_list(skills):
    lines = []
    for skill in list_skills(skills):
        description = skill.description or skill.when_to_use or "No description"
        hint = f" [{skill.argument_hint}]" if skill.argument_hint else ""
        lines.append(f"/{skill.name}{hint:<10} {description} [{skill.source}]")
    return "\n".join(lines)


def list_skills(skills, user_invocable_only=True):
    items = [skills[name] for name in sorted(skills)]
    if user_invocable_only:
        items = [skill for skill in items if skill.user_invocable]
    return sorted(items, key=lambda skill: (skill.source != "builtin", skill.name))


def parse_slash_command(text):
    text = str(text).strip()
    if not text.startswith("/") or text == "/":
        return "", ""
    command, _, arguments = text[1:].partition(" ")
    return command.strip(), arguments.strip()


def _parse_value(value):
    """
    解析 frontmatter 中的值，支持布尔值、列表和字符串。
    "true" → True
    "no" → False
    "a,b,c" → ["a","b","c"]
    "hello" → "hello"
    " 123 " → "123"
    """
    value = value.strip().strip("\"'")

    # 布尔值
    if value.lower() in {"true", "yes"}:
        return True
    if value.lower() in {"false", "no"}:
        return False

    # 列表用逗号分隔
    if "," in value:
        return [item.strip() for item in value.split(",") if item.strip()]

    # 默认：字符串
    return value


def _list_value(value):
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _string(value, default=""):
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def _bool_value(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _should_show_in_prompt(skill):
    return skill.user_invocable or bool(skill.paths)
