"""導入先のパス解決・マニフェスト・衝突分類などの中核ユーティリティ (#47)。

既存の scripts/nf_workbench.py (#9) の安全機構をユーザー単位導入向けに
移植したもの。方針は同じ:

    - dry-run で計画と差分を表示し、何も変更しない
    - 管理対象の記録 (マニフェスト) と sha256 による改変検知
    - 衝突 (管理外の既存ファイル) は停止し、上書きしない
    - 上書き・削除前にはバックアップを作る
"""

from __future__ import annotations

import datetime
import difflib
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import NoReturn

from platformdirs import user_config_dir, user_state_dir

from . import __version__ as VERSION

# パッケージ同梱の配布物の正本 (#47 §4.1)。
DATA_DIR = Path(__file__).resolve().parent / "data"
TEMPLATE_DIR = DATA_DIR / "templates"

# ユーザーグローバル (~/.config/opencode) に置く管理対象ファイル。
# 相対パスは ~/.config/opencode 起点で、key はエージェント ID 等に使う。
USER_AGENT_IDS = ("architect", "expert", "explore", "implementer", "reviewer")
USER_COMMANDS = ("commands/nfgo.md",)
USER_PLUGINS = ("plugins/runtime-guard.ts",)
USER_MANAGED_FILES = tuple(f"agents/{a}.md" for a in USER_AGENT_IDS) + USER_COMMANDS + USER_PLUGINS

STATE_DIRNAME = "nepoforge"
MANIFEST_NAME = "manifest.json"
PROJECTS_NAME = "projects.json"
BACKUP_DIRNAME = "backup"

# プロジェクト側 (#47 §4.3)。旧 #9 のマニフェストと同じパスだが kind で区別する。
PROJECT_MANIFEST_PATH = ".opencode/.nepoforge.json"
PROJECT_CONFIG_PATH = ".opencode/opencode.jsonc"
MANAGED_MARKER_BEGIN = ">>> nepoforge (managed) >>>"
MANAGED_MARKER_END = "<<< nepoforge (managed) <<<"


class NepoforgeError(Exception):
    """明確なメッセージを持つ停止エラー。"""


def fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def opencode_dir() -> Path:
    """OpenCode のユーザーグローバル設定ディレクトリ (~/.config/opencode)。"""
    return Path(user_config_dir("opencode"))


def state_dir() -> Path:
    """Nepoforge の状態ディレクトリ (~/.local/state/nepoforge)。"""
    return Path(user_state_dir(STATE_DIRNAME))


def manifest_path() -> Path:
    return state_dir() / MANIFEST_NAME


def projects_path() -> Path:
    return state_dir() / PROJECTS_NAME


def backup_root() -> Path:
    return state_dir() / BACKUP_DIRNAME


def local_profile_path() -> Path:
    """利用者所有の個人上書きプロファイル (#6 の profiles/local.json に相当)。"""
    return Path(user_config_dir(STATE_DIRNAME)) / "profile.json"


def sha256_of(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path: Path) -> dict | None:
    """マニフェストを読む。無ければ None。他ツールのものなら停止する。"""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"manifest is broken: {path} ({exc})")
    if not isinstance(data, dict) or data.get("tool") != "nepoforge":
        fail(f"manifest belongs to another tool: {path}")
    return data


def classify(target: Path, manifest: dict | None, expected: dict[str, str | None]) -> dict[str, str]:
    """各管理対象ファイルの状態を分類する (#9 の語彙に updatable を追加)。

    expected は「現行パッケージ + 現行プロファイルであるべき内容」のハッシュ。
    改変判定はマニフェスト記録との比較のみで行う (設計 0003 §6)。expected と
    の相違はパッケージ更新由来の正規の差分であり、updatable として別扱いする:
    - missing     : 対象に存在しない
    - unchanged   : マニフェスト記録と一致し、現行パッケージとも一致 (最新)
    - updatable   : マニフェスト記録と一致するが、現行パッケージと異なる
                    (パッケージ更新後の正規の更新対象。--force 不要)
    - modified    : 存在するがマニフェスト記録と一致しない (利用者改変の疑い)
    - conflict    : 存在するがマニフェストに記録がない (非管理ファイル)
    """
    state: dict[str, str] = {}
    for rel in expected:
        current = sha256_of(target / rel)
        if current is None:
            state[rel] = "missing"
        elif manifest is None or rel not in manifest.get("files", {}):
            state[rel] = "conflict"
        elif manifest["files"][rel] != current:
            state[rel] = "modified"
        elif expected[rel] != current:
            state[rel] = "updatable"
        else:
            state[rel] = "unchanged"
    return state


def backup_file(target: Path, rel: str) -> Path | None:
    """上書き・削除前に 1 ファイルを state 配下へバックアップする。"""
    src = target / rel
    if not src.is_file():
        return None
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    dest = backup_root() / stamp / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def diff_text(old: Path | None, new: Path) -> str:
    """既存ファイルと配布物の差分 (unified diff) を返す。"""
    return diff_text_vs(old, new.read_text(encoding="utf-8"), str(new))


def diff_text_vs(old: Path | None, new_text: str, new_label: str = "new") -> str:
    """既存ファイルとテキスト (中間ファイルを作らない) の差分を返す。"""
    new_lines = new_text.splitlines(keepends=True)
    if old is not None and old.is_file():
        old_lines = old.read_text(encoding="utf-8").splitlines(keepends=True)
        label = str(old)
    else:
        old_lines = []
        label = "/dev/null"
    return "".join(difflib.unified_diff(old_lines, new_lines, fromfile=label, tofile=new_label))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def store_write_manifest(profile_name: str, files: dict[str, str]) -> None:
    """ユーザーグローバル導入のマニフェストを書く (kind: "user")。"""
    write_json(
        manifest_path(),
        {
            "tool": "nepoforge",
            "kind": "user",
            "version": VERSION,
            "installed_at": now_iso(),
            "profile": profile_name,
            "files": files,
        },
    )


def load_projects() -> list[dict]:
    """中央の有効化済みプロジェクト記録を読む。無ければ空。壊れていれば停止。"""
    path = projects_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"projects record is broken: {path} ({exc})")
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, list) or any(not isinstance(p, dict) for p in projects):
        fail(f"projects record is broken: {path} ('projects' must be a list of objects)")
    return list(projects)


def save_projects(projects: list[dict]) -> None:
    write_json(projects_path(), {"projects": projects})


def record_project(project_root: Path) -> None:
    projects = load_projects()
    resolved = str(project_root.resolve())
    projects = [p for p in projects if p.get("path") != resolved]
    projects.append({"path": resolved, "enabled_at": now_iso()})
    save_projects(projects)


def forget_project(project_root: Path) -> None:
    resolved = str(project_root.resolve())
    projects = [p for p in load_projects() if p.get("path") != resolved]
    save_projects(projects)


def find_project_root(start: Path | None = None) -> Path:
    """Git リポジトリのルートを探す。見つからなければ cwd を返す。"""
    import subprocess

    cwd = (start or Path.cwd()).resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).resolve()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return cwd
