#!/usr/bin/env python3
"""派生エージェント定義の生成と Model Resolver の実機確認 (#52)。

課金なしで実行できる検証ハーネス。一時ディレクトリへ隔離した
XDG_CONFIG_HOME / XDG_STATE_HOME を使うため、利用者の
~/.config/opencode や ~/.config/nepoforge には一切触れない。

確認する項目 (設計メモ 0007 §8.3 の課金なし部分):

    1. `nepoforge profile show` が constraints を表示する
    2. `nepoforge profile derive --dry-run` が書き込まずに計画を表示する
    3. `nepoforge profile derive` が派生定義を生成し、再実行が冪等である
    4. 派生定義が元 role 定義と `model:` 行のみ異なる (権限は複製)
    5. 内容が異なる既存ファイルに対しては停止する (黙って上書きしない)
    6. Resolver が task_rules により役割既定と異なるモデルを選ぶ

OpenCode への登録確認 (`opencode api get /api/agent`) は別途、生成した
定義を実配置したうえで人間が行う。並列実行と actual_model の突合は
課金を伴うため本スクリプトの対象外。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nepoforge import cli, derive, profiles  # noqa: E402


def _profile(lighter: str, default_implementer: str) -> dict:
    """テスト用の local プロファイル。モデル名は同梱プロファイルから読む。"""
    balanced = profiles.load_profile("balanced")
    return {
        "roles": dict(balanced),
        "task_rules": [
            {
                "when": {"role": "expert", "complexity": "low"},
                "model": lighter,
                "note": "小さな調査は軽量モデルへ割り当てる",
            },
            {
                "when": {"role": "implementer", "risk": "high"},
                "model": default_implementer,
                "note": "高リスクの実装はコーディング特化モデルを維持する",
            },
        ],
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="nf52-check-") as tmp:
        base = Path(tmp)
        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        os.environ["XDG_STATE_HOME"] = str(base / "state")
        local = base / "config" / "nepoforge" / "profile.json"
        local.parent.mkdir(parents=True, exist_ok=True)

        balanced = profiles.load_profile("balanced")
        # 軽量モデルとして expert の役割既定とは別の候補を使う
        lighter = next(
            m for m in sorted({*profiles.load_profile("economy").values(), *balanced.values()})
            if m != balanced["expert"]
        )
        local.write_text(json.dumps(_profile(lighter, balanced["implementer"])), encoding="utf-8")

        print("== 1. profile show")
        cli.main(["profile", "show", "local"])

        print("\n== 2. profile derive --dry-run (書き込みなし)")
        cli.main(["profile", "derive", "--profile", "local", "--dry-run"])
        agents_dir = base / "config" / "opencode" / "agents"
        before = sorted(p.name for p in agents_dir.glob("*.md")) if agents_dir.is_dir() else []
        print(f"files after dry-run: {before}")

        print("\n== 3. profile derive (生成) と冪等性")
        cli.main(["profile", "derive", "--profile", "local"])
        cli.main(["profile", "derive", "--profile", "local"])
        after = sorted(p.name for p in agents_dir.glob("*.md"))
        print(f"files after derive: {after}")

        print("\n== 4. 内容は元 role 定義と model 行のみ異なる")
        for name in after:
            text = (agents_dir / name).read_text(encoding="utf-8")
            # 派生 ID から role を求めるのではなく、元 role と突き合わせる
            for cand_role in ("architect", "expert", "explore", "implementer", "reviewer"):
                src = (profiles.store.DATA_DIR / "agents" / f"{cand_role}.md").read_text(encoding="utf-8")
                stripped_new = [l for l in text.splitlines() if not l.startswith("model:")]
                stripped_src = [l for l in src.splitlines() if not l.startswith("model:")]
                if stripped_new == stripped_src:
                    print(f"  ok: {name} == agents/{cand_role}.md (model 行のみ差分)")
                    break
            else:
                print(f"  NG: {name} は元 role 定義と一致しない")
                return 1

        print("\n== 5. 内容の異なる既存ファイルでは停止する")
        target = agents_dir / after[0]
        target.write_text("---\ndescription: user owned\n---\n", encoding="utf-8")
        try:
            cli.main(["profile", "derive", "--profile", "local"])
            code = 0
        except SystemExit as exc:
            code = exc.code
        print(f"  exit code (conflict): {code}")
        if code != 2:
            print("  NG: 衝突時に停止していない")
            return 1

        print("\n== 6. Resolver: task_rules により役割既定と異なるモデルを選ぶ")
        config = profiles.load_profile_config("local")
        from nepoforge.resolver import TaskInput, resolve

        small = resolve(TaskInput(task_id="T1", role="expert", complexity="low"), config)
        large = resolve(TaskInput(task_id="T2", role="expert", complexity="high"), config)
        print(f"  expert/complexity=low  -> {profiles.format_model(small.resolved_model)} "
              f"binding={small.binding} reason={small.reason}")
        print(f"  expert/complexity=high -> {profiles.format_model(large.resolved_model)} "
              f"binding={large.binding} reason={large.reason}")
        if small.reason == large.reason:
            print("  NG: タスク別ルールが効いていない")
            return 1
        if small.binding != derive.agent_id_for("expert", small.resolved_model):
            print("  NG: 派生定義の binding が命名規則と一致しない")
            return 1

    print("\nall checks passed (charge-free)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
