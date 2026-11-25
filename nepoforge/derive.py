"""派生エージェント定義の生成・命名・対象ペア決定 (#52)。

設計メモ 0007 §5 に従い、元 role 定義 (`nepoforge/data/agents/<role>.md`) の
`model:` 行のみを差し替えた派生定義を生成する。権限・本文は元 role から複製され、
モデル変更で権限は拡大しない。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import store
from .store import NepoforgeError
from . import profiles

BUILTIN_AGENT_IDS = {"build", "plan", "general", "explore", "title", "summary", "compaction"}


class DeriveItem:
    """派生定義 1 件の計画項目。"""

    def __init__(
        self,
        agent_id: str,
        role: str,
        model: Any,
        expected_text: str,
        state: str,
        path: Path | None = None,
        rel: str | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.id = agent_id
        self.agent = agent_id
        self.role = role
        self.model = model
        self.model_ref = model
        self.expected_text = expected_text
        self.text = expected_text
        self.expected = expected_text
        self.content = expected_text
        self.state = state
        self.status = state
        self.path = path
        self.rel = rel if rel is not None else f"agents/{agent_id}.md"
        self.relative_path = self.rel

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


def slug_of(model: Any) -> str:
    """モデルから slug を作る (provider を除き、# を - に置換)。"""
    text = model if isinstance(model, str) else profiles.format_model(model)
    if "/" in text:
        text = text.split("/", 1)[1]
    return text.replace("#", "-")


def agent_id_for(role: str, model: Any) -> str:
    return f"nf-{role}-{slug_of(model)}"


def _validate(role: str, model: Any) -> tuple[Any, str]:
    if role not in store.USER_AGENT_IDS:
        raise NepoforgeError(f"unknown role: {role}")
    model_str = model if isinstance(model, str) else profiles.format_model(model)
    ref = profiles.parse_model(model_str)
    agent_id = agent_id_for(role, ref)
    if not re.fullmatch(r"nf-[a-z0-9][a-z0-9._-]*", agent_id):
        raise NepoforgeError(f"derived agent id has invalid characters: {agent_id}")
    if agent_id in BUILTIN_AGENT_IDS or agent_id in set(store.USER_AGENT_IDS):
        raise NepoforgeError(f"derived agent id collides with built-in/role id: {agent_id}")
    return ref, agent_id


def derive_text(role: str, model: Any) -> str:
    """DATA_DIR 正本の model 行のみを差し替えた派生定義の全文を返す。"""
    ref, _agent_id = _validate(role, model)
    model_str = profiles.format_model(ref)
    src = store.DATA_DIR / "agents" / f"{role}.md"
    if not src.is_file():
        raise NepoforgeError(f"source agent not found: {src}")
    text = src.read_text(encoding="utf-8")
    return profiles.apply_to_agent(role, text, model_str)


def derive_plan(
    config: Any,
    role: str | None = None,
    model: Any = None,
    target_dir: Any = None,
) -> list[DeriveItem]:
    """対象ペアを決定し、各派生定義の計画 (ok/write/conflict) を返す。"""
    if target_dir is None:
        target_dir = store.opencode_dir()
    target_dir = Path(target_dir)

    # 対象ペアの決定
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    if role is not None or model is not None:
        if role is None or model is None:
            raise NepoforgeError("both --role and --model are required")
        model_str = model if isinstance(model, str) else profiles.format_model(model)
        ref = profiles.parse_model(model_str)
        if role not in store.USER_AGENT_IDS:
            raise NepoforgeError(f"unknown role: {role}")
        candidate_strs = {profiles.format_model(c.ref) for c in config.candidates}
        for r, m in config.roles.items():
            candidate_strs.add(m)
        if model_str not in candidate_strs:
            raise NepoforgeError(f"model not in profile candidates: {model_str}")
        key = (role, model_str)
        if key not in seen:
            seen.add(key)
            pairs.append(key)
    else:
        for rule in getattr(config, "task_rules", ()):
            when = getattr(rule, "when", None)
            if not isinstance(when, dict):
                continue
            model_str = getattr(rule, "model", None)
            if not isinstance(model_str, str):
                continue
            r = when.get("role")
            # role 非指定ルールは全役割に適用されるため、全役割の派生定義を計画する
            # (黙って対象から漏らすと binding 未生成のまま実行され得る — #52 レビュー指摘)
            roles = (r,) if r in store.USER_AGENT_IDS else store.USER_AGENT_IDS
            for target_role in roles:
                key = (target_role, model_str)
                if key not in seen:
                    seen.add(key)
                    pairs.append(key)

    # 役割既定と同一のものは除外
    filtered: list[tuple[str, str]] = []
    for r, m in pairs:
        default = config.roles.get(r) if isinstance(config.roles, dict) else None
        if default == m:
            continue
        filtered.append((r, m))

    items: list[DeriveItem] = []
    for r, m in filtered:
        ref, agent_id = _validate(r, m)
        expected_text = derive_text(r, ref)
        rel = f"agents/{agent_id}.md"
        target_file = target_dir / rel
        if not target_file.is_file():
            state = "write"
        else:
            current = target_file.read_text(encoding="utf-8")
            if current == expected_text:
                state = "ok"
            else:
                state = "conflict"
        items.append(
            DeriveItem(
                agent_id=agent_id,
                role=r,
                model=ref,
                expected_text=expected_text,
                state=state,
                path=target_file,
                rel=rel,
            )
        )
    return items


def safe_texts() -> set[str]:
    """現在の生成規則で再現できる派生定義の全文集合を返す。

    `--prune` と uninstall が「正規の生成物」と「利用者改変・所有物」を
    区別するために使う。既存導入環境の破壊を避けるため、内容が一致しない
    ファイルは既定では削除しない。

    task_rules のモデルは candidates にも自動で入るが、明示的に取り込んで
    おく (生成・削除の判定基準が candidates の作り方に依存しないようにする)。
    """
    models: set[str] = set()
    for name in profiles.available_profiles():
        try:
            config = profiles.load_profile_config(name)
        except NepoforgeError:
            continue
        models.update(config.roles.values())
        models.update(profiles.format_model(c.ref) for c in config.candidates)
        for rule in getattr(config, "task_rules", ()):
            model = getattr(rule, "model", None)
            if isinstance(model, str):
                models.add(model)
    texts: set[str] = set()
    for role in store.USER_AGENT_IDS:
        for model in models:
            try:
                texts.add(derive_text(role, model))
            except NepoforgeError:
                continue
    return texts
