"""配布する設定データの整合性検証 (#11)。

CI (#11) はこのモジュールの `collect_problems()` で配布物を検査し、
不正な設定が 1 件でもあれば失敗する。手元では `python -m nepoforge.validate`
でも同じ検査を実行できる。課金・ネットワークは使わない。

検査対象はパッケージ同梱の正本 `nepoforge/data/` (#57):

    - エージェント定義: frontmatter の必須キー・列挙値・モデル表記・権限ルールの形
    - コマンド定義: frontmatter の description
    - 役割参照: エージェント ID・description・参照するコマンド/エージェントが解決できること
    - モデルプロファイル: `profiles.load_profile_config` の検証 (候補・規則・制約)
    - 規約との整合: 役割別モデルの候補登録、task_rules のモデルの候補登録、
      ルート (model-routes.json) の形とプロファイル候補のカバレッジ
    - テンプレート: JSONC / JSON として読め、guardrail の managed マーカーが対になっていること
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import data_policy, jsonc, profiles, store
from .store import NepoforgeError

# frontmatter で許すキー (OpenCode のエージェント定義で使うものだけに限定する)。
AGENT_KEYS = frozenset({"description", "mode", "model", "permission", "permissions", "tools", "temperature", "color", "disable"})
AGENT_MODES = frozenset({"primary", "subagent", "all"})
# "*" は「全アクション」を既定 deny にするためのワイルドカード (reviewer 等)。
PERMISSION_ACTIONS = frozenset(
    {"read", "edit", "glob", "grep", "list", "bash", "task", "webfetch", "websearch", "lsp", "skill", "todoread", "todowrite", "shell", "execute", "subagent", "*"}
)
PERMISSION_EFFECTS = frozenset({"allow", "deny", "ask"})

MODEL_LINE_RE = re.compile(r"^model:\s*(.+?)\s*$", re.MULTILINE)
DESCRIPTION_LINE_RE = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def problem(kind: str, path: str | Path, message: str) -> dict[str, str]:
    return {"kind": kind, "path": str(path), "message": message}


def _rel(path: Path) -> str:
    root = store.DATA_DIR.parent.parent
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def split_frontmatter(text: str) -> tuple[list[str] | None, str]:
    """`---` で囲まれた frontmatter の行と本文を返す。無ければ (None, text)。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1 :])
    return None, text


def parse_block_scalars(frontmatter: list[str]) -> dict[str, object]:
    """frontmatter を最小限の YAML サブセットとして読む (#11)。

    使うのは「key: 値」「key:」+ インデントした「- ...」列だけなので、
    PyYAML を依存に加えずに解析する。値は文字列・真偽値・数値・
    dict のリスト・文字列のリストになる。解析できない場合は ValueError。
    """
    data: dict[str, object] = {}
    current_key: str | None = None
    list_items: list[object] = []
    item_fields: dict[str, object] | None = None

    def flush(current: str | None, items: list[object]) -> None:
        if current is not None:
            data[current] = items if items else None

    for raw in frontmatter:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if indent == 0:
            flush(current_key, list_items)
            list_items, item_fields = [], None
            if ":" not in stripped:
                raise ValueError(f"frontmatter line is not 'key: value': {stripped!r}")
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            current_key = key
            if value:
                data[key] = _scalar(value)
                current_key = None
        else:
            if current_key is None:
                raise ValueError(f"unexpected indented line: {stripped!r}")
            if stripped.startswith("- "):
                payload = stripped[2:].strip()
                if ":" in payload and not payload.startswith('"'):
                    field_key, _, field_value = payload.partition(":")
                    item_fields = {field_key.strip(): _scalar(field_value.strip())}
                    list_items.append(item_fields)
                else:
                    item_fields = None
                    list_items.append(_scalar(payload))
            elif stripped.startswith("-"):
                item_fields = None
                list_items.append(None)
            else:
                if item_fields is None:
                    raise ValueError(f"unexpected nested line: {stripped!r}")
                field_key, _, field_value = stripped.partition(":")
                item_fields[field_key.strip()] = _scalar(field_value.strip())
    flush(current_key, list_items)
    return data


def _scalar(text: str) -> object:
    if text == "" or text == "null" or text == "~":
        return None
    if text in ("true", "false"):
        return text == "true"
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return json.loads(text)
    if text.startswith("'") and text.endswith("'") and len(text) >= 2:
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        return text


def check_agents() -> tuple[list[dict[str, str]], dict[str, str]]:
    """エージェント定義を検査し、(問題一覧, {agent_id: model}) を返す。"""
    problems: list[dict[str, str]] = []
    models: dict[str, str] = {}
    agents_dir = store.DATA_DIR / "agents"
    found = {p.stem for p in agents_dir.glob("*.md")}
    for agent_id in sorted(store.USER_AGENT_IDS):
        if agent_id not in found:
            problems.append(problem("agents", _rel(agents_dir / f"{agent_id}.md"), f"managed agent is missing: {agent_id}"))
    for path in sorted(agents_dir.glob("*.md")):
        rel = _rel(path)
        stem = path.stem
        if not SLUG_RE.match(stem):
            problems.append(problem("agents", rel, f"agent id is not a lower-case slug: {stem!r}"))
        text = path.read_text(encoding="utf-8")
        frontmatter, body = split_frontmatter(text)
        if frontmatter is None:
            problems.append(problem("agents", rel, "frontmatter (--- ... ---) is missing"))
            continue
        try:
            data = parse_block_scalars(frontmatter)
        except ValueError as exc:
            problems.append(problem("agents", rel, f"frontmatter is malformed: {exc}"))
            continue
        unknown = sorted(set(data) - AGENT_KEYS)
        if unknown:
            problems.append(problem("agents", rel, f"unknown frontmatter keys: {', '.join(unknown)}"))
        description = data.get("description")
        if not isinstance(description, str) or not description.strip():
            problems.append(problem("agents", rel, "frontmatter 'description' is required"))
        mode = data.get("mode")
        if mode not in AGENT_MODES:
            problems.append(problem("agents", rel, f"frontmatter 'mode' must be one of {sorted(AGENT_MODES)}: {mode!r}"))
        model = data.get("model")
        if not isinstance(model, str):
            problems.append(problem("agents", rel, "frontmatter 'model' is required"))
        else:
            try:
                profiles.parse_model(model)
            except NepoforgeError as exc:
                problems.append(problem("agents", rel, f"invalid model notation: {exc}"))
            if stem in store.USER_AGENT_IDS:
                models[stem] = model
        rules = data.get("permissions", data.get("permission"))
        if rules is not None:
            problems.extend(check_permission_rules(rules, rel))
        if not body.strip():
            problems.append(problem("agents", rel, "body (instructions) is empty"))
    return problems, models


def check_permission_rules(rules: object, rel: str) -> list[dict[str, str]]:
    """権限ルールの形 (action / resource / effect) を検査する。"""
    problems: list[dict[str, str]] = []
    if not isinstance(rules, list):
        return [problem("permissions", rel, "permissions must be a list of rules")]
    for index, rule in enumerate(rules):
        where = f"permissions[{index}]"
        if not isinstance(rule, dict):
            problems.append(problem("permissions", rel, f"{where} must be an object"))
            continue
        extra = sorted(set(rule) - {"action", "resource", "effect"})
        if extra:
            problems.append(problem("permissions", rel, f"{where} has unknown keys: {', '.join(extra)}"))
        action = rule.get("action")
        if action not in PERMISSION_ACTIONS:
            problems.append(problem("permissions", rel, f"{where} action is not allowed: {action!r}"))
        resource = rule.get("resource")
        if not isinstance(resource, str) or not resource:
            problems.append(problem("permissions", rel, f"{where} resource must be a non-empty string"))
        effect = rule.get("effect")
        if effect not in PERMISSION_EFFECTS:
            problems.append(problem("permissions", rel, f"{where} effect is not allowed: {effect!r}"))
    return problems


def check_commands() -> tuple[list[dict[str, str]], set[str]]:
    """コマンド定義を検査し、(問題一覧, {command 名}) を返す。"""
    problems: list[dict[str, str]] = []
    commands: set[str] = set()
    commands_dir = store.DATA_DIR / "commands"
    for path in sorted(commands_dir.glob("*.md")):
        rel = _rel(path)
        commands.add(path.stem)
        frontmatter, body = split_frontmatter(path.read_text(encoding="utf-8"))
        if frontmatter is None:
            problems.append(problem("commands", rel, "frontmatter (--- ... ---) is missing"))
            continue
        try:
            data = parse_block_scalars(frontmatter)
        except ValueError as exc:
            problems.append(problem("commands", rel, f"frontmatter is malformed: {exc}"))
            continue
        unknown = sorted(set(data) - {"description", "agent", "model", "subtask", "argument-hint"})
        if unknown:
            problems.append(problem("commands", rel, f"unknown frontmatter keys: {', '.join(unknown)}"))
        if not isinstance(data.get("description"), str) or not str(data.get("description")).strip():
            problems.append(problem("commands", rel, "frontmatter 'description' is required"))
        if not body.strip():
            problems.append(problem("commands", rel, "command body is empty"))
    for managed in store.USER_COMMANDS:
        if Path(managed).stem not in commands:
            problems.append(problem("commands", _rel(commands_dir / Path(managed).name), "managed command is missing"))
    return problems, commands


def check_plugins() -> list[dict[str, str]]:
    """グローバル管理対象の runtime plugin と依存モジュールを検査する。"""
    problems: list[dict[str, str]] = []
    for rel in store.USER_PLUGINS:
        path = store.DATA_DIR / rel
        if not path.is_file():
            problems.append(problem("plugins", _rel(path), "managed plugin is missing"))
        elif not path.read_text(encoding="utf-8").strip():
            problems.append(problem("plugins", _rel(path), "managed plugin is empty"))
    return problems


def check_role_references(agent_models: dict[str, str], commands: set[str]) -> list[dict[str, str]]:
    """エージェント同士・コマンドとの参照 (役割名・コマンド名) が解決できることを検査する。

    役割名は、前後が単語の区切り (空白・`-`・句読点・行末等) にあるものだけを参照とみなす。
    """
    problems: list[dict[str, str]] = []
    agents_dir = store.DATA_DIR / "agents"
    role_pattern = re.compile(r"(?<![\w-])(" + "|".join(store.USER_AGENT_IDS) + r")(?=-|[\s`）)、。,.]|$)")
    for path in sorted(list(agents_dir.glob("*.md")) + list((store.DATA_DIR / "commands").glob("*.md"))):
        rel = _rel(path)
        text = path.read_text(encoding="utf-8")
        for match in role_pattern.finditer(text):
            role = match.group(1)
            if role not in agent_models:
                problems.append(problem("role-references", rel, f"reference to undefined role: {role}"))
        for name in re.findall(r"/nf[a-z]+", text):
            if name.lstrip("/") not in commands:
                problems.append(problem("role-references", rel, f"reference to undefined command: {name}"))
    return problems


def check_profiles(agent_models: dict[str, str]) -> list[dict[str, str]]:
    """同梱プロファイルの検証と、候補・task_rules・ルートとの整合を検査する。"""
    problems: list[dict[str, str]] = []
    profile_paths = sorted((store.DATA_DIR / "profiles").glob("*.json"))
    if not profile_paths:
        return [problem("profiles", _rel(store.DATA_DIR / "profiles"), "no bundled profiles")]

    routes_path = store.DATA_DIR / "model-routes.json"
    routes: dict = {}
    try:
        loaded = json.loads(routes_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("root must be an object")
        routes = loaded
    except (OSError, ValueError) as exc:
        return [problem("routes", _rel(routes_path), f"model-routes.json is broken: {exc}")]

    for model, route in routes.items():
        rel = _rel(routes_path)
        try:
            profiles.parse_model(model)
        except NepoforgeError as exc:
            problems.append(problem("routes", rel, f"route key is not a model: {exc}"))
        if not isinstance(route, dict):
            problems.append(problem("routes", rel, f"route for {model} must be an object"))
            continue
        allowed = {"no_training", "retention", "destination", "approval_ref", "verified_on"}
        unknown = sorted(set(route) - allowed)
        if unknown:
            problems.append(problem("routes", rel, f"route for {model} has unknown keys: {', '.join(unknown)}"))
        if route.get("no_training") is not None and not isinstance(route["no_training"], bool):
            problems.append(problem("routes", rel, f"route for {model}: 'no_training' must be boolean"))
        if route.get("retention") not in (None, "zero"):
            problems.append(problem("routes", rel, f"route for {model}: 'retention' must be null or 'zero'"))
        if route.get("destination") not in (None, "japan", "global"):
            problems.append(problem("routes", rel, f"route for {model}: unknown destination {route.get('destination')!r}"))
        if not isinstance(route.get("approval_ref"), str) or not route.get("approval_ref"):
            problems.append(problem("routes", rel, f"route for {model}: 'approval_ref' is required"))

    seen_names: set[str] = set()
    for path in profile_paths:
        rel = _rel(path)
        name = path.stem
        if name in seen_names:
            problems.append(problem("profiles", rel, f"duplicate profile name: {name}"))
        seen_names.add(name)
        try:
            config = profiles.load_profile_config(name)
        except NepoforgeError as exc:
            problems.append(problem("profiles", rel, str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - どの例外も設定の破損として報告する
            problems.append(problem("profiles", rel, f"profile '{name}' failed to load: {type(exc).__name__}: {exc}"))
            continue
        candidate_models = {str(c.ref) for c in config.candidates}
        for role, model in config.roles.items():
            if model not in candidate_models:
                problems.append(problem("profiles", rel, f"role '{role}' model is not a candidate: {model}"))
        for rule in config.task_rules:
            if rule.model not in candidate_models:
                problems.append(problem("profiles", rel, f"task_rule model is not a candidate: {rule.model}"))
        for model in candidate_models:
            if model not in routes:
                problems.append(problem("profiles", rel, f"candidate has no route entry in model-routes.json: {model}"))
        if config.primary_model is None and not config.primary_model_by_policy:
            problems.append(problem("profiles", rel, "profile has no primary_model"))
        if config.constraints.max_parallel < 1:
            problems.append(problem("profiles", rel, "constraints.max_parallel must be >= 1"))
        for provider in config.constraints.providers:
            if provider.approved and not provider.endpoint:
                problems.append(problem("profiles", rel, f"approved provider has no endpoint: {provider.id}"))
    return problems


def check_templates() -> list[dict[str, str]]:
    """guardrail テンプレートが read でき、managed マーカーが対になっていることを検査する。

    provider テンプレートは差分計算用の**断片** (オブジェクトの内側) のため、
    JSON として単体では解析しない (#47 の追記方式を参照)。
    """
    problems: list[dict[str, str]] = []
    template_dir = store.TEMPLATE_DIR
    for path in sorted(template_dir.glob("*.jsonc")) + sorted(template_dir.glob("*.json")):
        rel = _rel(path)
        text = path.read_text(encoding="utf-8")
        is_fragment = path.name.startswith("provider-")
        try:
            if not is_fragment:
                jsonc.loads(text)
            else:
                jsonc.loads("{" + text + "}")
        except Exception as exc:  # noqa: BLE001 - 読み込み不能は設定の破損
            problems.append(problem("templates", rel, f"template is not valid JSONC/JSON: {exc}"))
            continue
        begins = text.count(store.MANAGED_MARKER_BEGIN)
        ends = text.count(store.MANAGED_MARKER_END)
        if begins != ends or begins > 1 or (not is_fragment and begins != 1):
            problems.append(
                problem("templates", rel, f"managed markers are unbalanced ({begins} begin / {ends} end)")
            )
    guardrail = template_dir / "guardrail.jsonc"
    if not guardrail.is_file():
        problems.append(problem("templates", _rel(guardrail), "guardrail template is missing"))
    else:
        data = jsonc.loads(guardrail.read_text(encoding="utf-8"))
        rules = data.get("permissions") if isinstance(data, dict) else None
        if not isinstance(rules, list) or not rules:
            problems.append(problem("templates", _rel(guardrail), "guardrail template has no permissions"))
        else:
            problems.extend(check_permission_rules(rules, _rel(guardrail)))
    required_denies = {"gh pr merge*", "git push --force*"}
    if guardrail.is_file():
        rules = jsonc.loads(guardrail.read_text(encoding="utf-8")).get("permissions", [])
        denied = {r.get("resource") for r in rules if isinstance(r, dict) and r.get("effect") == "deny"}
        missing = sorted(required_denies - denied)
        if missing:
            problems.append(problem("templates", _rel(guardrail), f"guardrail must deny: {', '.join(missing)}"))
    return problems


def check_data_policy() -> list[dict[str, str]]:
    """Data Policy の値の集合が既定 (confidential を含む) を満たすこと。"""
    if data_policy.DEFAULT not in data_policy.POLICIES or set(data_policy.POLICIES) != {"open", "private", "confidential"}:
        return [problem("data-policy", "nepoforge/data_policy.py", f"unexpected policies: {data_policy.POLICIES}")]
    return []


def collect_problems(data_dir: Path | None = None) -> list[dict[str, str]]:
    """同梱データを全項目検査し、問題の一覧を返す (空なら整合)。"""
    if data_dir is not None:
        raise ValueError("collect_problems() inspects the packaged data only; data_dir is not supported")
    agent_problems, agent_models = check_agents()
    command_problems, commands = check_commands()
    problems = [
        *agent_problems,
        *command_problems,
        *check_plugins(),
        *check_role_references(agent_models, commands),
        *check_profiles(agent_models),
        *check_templates(),
        *check_data_policy(),
    ]
    return problems


def format_problem(item: dict[str, str]) -> str:
    return f"[{item['kind']}] {item['path']}: {item['message']}"
