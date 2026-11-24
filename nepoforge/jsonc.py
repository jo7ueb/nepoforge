"""JSONC (コメント付き JSON) の読み込み (#47)。

OpenCode の設定ファイルは JSONC (`opencode.jsonc`) として配布される。
provider 定義の有無確認 (#47) や doctor の構文診断のために読む必要があるが、
独自パーサは持たず成熟した `json5` に委ねる (コメント・trailing comma・
単一引用符を許容して解析できる)。

書き込みは行わない。利用者所有のグローバル設定への追記はテンプレートの
テキスト挿入のみ (コメントや書式を保持するためパースし直さない)。
"""

from __future__ import annotations

from typing import Any

import json5

from .store import NepoforgeError


def loads(text: str) -> Any:
    """JSONC テキストを解析して Python オブジェクトを返す。失敗は NepoforgeError。"""
    try:
        return json5.loads(text)
    except ValueError as exc:
        raise NepoforgeError(f"invalid JSONC: {exc}") from exc
