# 調査報告: 並列委譲の実機検証とセッション計測

- Issue: [#49](https://github.com/jo7ueb/nepoforge/issues/49)
- 検証日: 2026-11-25
- 検証環境: OpenCode CLI **v2.4.0** (`opencode --version`)、共有バックグラウンドサービス (`opencode api get /api/info` → version 2.4.0)。公式最新は v2.4.1 (2026-11-23、[changelog](https://opencode.ai/changelog/v2.4.1))。先行調査 [0001](0001-opencode-agent-delegation.md) は v2.4.1 で検証済みであり、v2.4.0 → v2.4.1 間に委譲仕様の変更は確認されていない (expert 調査、[changelog v2.3.0](https://opencode.ai/changelog/v2.3.0) が直近の大幅変更)。
- 性質: 読み取り専用調査。プロジェクトコードへの変更なし。

## 1. 出典

| 出典 | URL | 用途 |
| --- | --- | --- |
| Tools ガイド (Subagent) | https://opencode.ai/docs/tools/ | subagent は常時バックグラウンド、即時リターン |
| Agents ガイド | https://opencode.ai/docs/agents/ | モード、親の `subagent` 権限、orchestrator 例 |
| Permissions ガイド | https://opencode.ai/docs/permissions/ | 子は自分自身の権限で動く、`subagent` 権限の resource はエージェント ID |
| Config ガイド (Experimental) | https://opencode.ai/docs/config/ | `experimental.subagent_depth` 既定 1 |
| OpenAPI / API ガイド | https://opencode.ai/docs/api/ | `session.list?parentID=`、`/api/experimental/job`、`/api/session/active`、`experimental.session.wait` |
| v2.4.1 実装 | https://github.com/anomalyco/opencode/blob/v2.4.1/packages/core/src/session/subagent-completion.ts ほか | 報告形式 (`<background-result>`)、depth 判定 |
| ローカルセッション DB | `~/.local/share/opencode/opencode.db` (SQLite) | 実測 (本報告 §3)。トラブルシュート文書に所在記載あり (https://opencode.ai/docs/troubleshooting/) |

いずれも 2026-11-25 に取得。バージョンと出典は issue #4 の検証 ([0001](0001-opencode-agent-delegation.md)) を引き継ぐ。

## 2. 公式資料から確認した仕様 (事実)

0001 の結論を補強・具体化したもの。詳細は expert 調査報告 (PR 本文に要約) を参照。

1. **並列起動**: 1 ターンで複数の subagent を起動すると、各起動は即座に子セッション ID を返し、子は独立したバックグラウンドジョブとして並行実行される (v2.3.0「Background Subagents by Default」以降)。並列数の公式上限は確認できなかった (不在の確認)。
2. **報告形式**: 子が idle になるたび、親に synthetic メッセージ `<background-result kind="subagent" sessionID=... state="completed|error|cancelled|running" ...>` が配信される。state=`running` は子がバックグラウンド作業を残したまま idle になった中間報告であり、完了と誤認してはならない。
3. **再委譲**: 子からの再委譲には `experimental.subagent_depth` (既定 1) の引き上げが必要。experimental 設定は予告なく変更されうる。また depth (深さ) と permission (起動可否) は独立した 2 機構であり、組み込み `general` は権限ポリシーとして起動を deny している。
4. **権限**: 子は親の権限のサブセットではなく、自分に設定された権限で動く。並列に起動した子は独立セッションとして個別に権限評価される。
5. **完了待ち**: 親は子の完了を同期待ちできない (呼び出しは即時リターン)。待ちが必要な外部手続き向けに `POST /api/experimental/session/{id}/wait`、`GET /api/session/active`、SSE (`GET /api/event`) がある。
6. **計測**: `Session.Info` に `parentID`・`time {created, busy, idle, ...}`・tokens/cost 合計が機械可読で含まれる。

## 3. 実機検証 (v2.4.0、2026-11-25)

### 3.1 手法

親セッションから subagent を起動し、ローカルセッション DB を読み取り専用 (`mode=ro`) で照会して、各子セッションの `parent_id`・`time_busy`・`time_idle` を機械可読に記録した。公式 API の代替手段 (`opencode api session.list --param parentID=<親ID>`、`GET /api/experimental/job` の `time.started/completed`) も確認済みで、本検証では DB 直読みを採用した (バックアップ不要な読み取りのみ)。

「並列に実行された」の判定基準: 2 子の busy 区間 (`time_busy` 〜 `time_idle`) が重なること。

### 3.2 検証結果

| # | 検証 | 手法 | 結果 | 証拠 (セッション ID / 時刻 JST) |
| --- | --- | --- | --- | --- |
| P1 | 1 ターン 2 起動の並列性 | 最小タスク A/B を同一ブロックから起動 | **並列** (busy 区間が 531 ms 重複) | A `ses_df1673c9cffepqumexy934a1M0` busy 02:27:02.259–02:27:02.807 / B `ses_df1673c9affe3KRJAit1C49jT2` busy 02:27:02.276–02:27:02.825。parent_id はともに親セッション `ses_df16860fbffeQa6O0s3giLKUdX` |
| P2 | 逐次対照 (serial control) | 最小タスク S1 の完了報告を受けてから S2 を起動 | **逐次** (重複 0 ms、起動往復で約 4.5 秒のギャップ) | S1 `ses_df1619cbaffeGGt9nYV1oP1q77` busy 02:33:10.865–02:33:11.096 / S2 `ses_df16189ffffe3a0vQvrEe9AwTQ` busy 02:33:15.657–02:33:15.855 |
| P3 | 実ワークロードでの並列委譲 | 本 Issue の調査 2 件 (ローカル現状把握 + 公式仕様調査) を同一ブロックから起動 | **並列** (重複 92.975 秒) | explore `ses_df1676f9fffedfjnDLs2P0giW4` busy 02:26:49.213–02:28:22.208 (93 秒) / expert `ses_df1676f9dffe0ych6IPzF7sIkc` busy 02:26:49.233–02:32:25.222 (336 秒) |

P3 の効果: 逐次起動なら壁時間は 93 + 336 ≒ **429 秒**。並列では expert の 336 秒がクリティカルパスとなり、explore の 93 秒を **完全に隠蔽**した (約 22% 短縮)。

### 3.3 «before» 側の実測 (逐次委譲の実害)

同一共有サービス上の過去実行を同じ方法で計測した。

| 実行 | 壁時間 | 内訳 | 観察 |
| --- | --- | --- | --- |
| `/nfgo 18` (research プロジェクト、2026-11-25 02:04–02:27、ルート `ses_df17c1b5dffeGaqCXvAt2z0Wlx`) | **23 分 25 秒** | explore 子 83 秒 (02:04:50–02:06:13) → ルート単独稼働 8 分 22 秒 (02:06:13–02:14:35) → **architect 子 12 分 44 秒** (02:14:35–02:27:19、入力 226k トークン) | 2 つの委譲が完全逐次。architect が調査全体を抱え込んだ結果、クリティカルパスの過半を占めた。issue #49 が想定するボトルネックと整合 |
| `/nfgo 47` (nepoforge、2026-11-25 00:04–01:31、ルート `ses_df1e9fe08ffe1KWSAlaUE78e4P`) | 1 時間 28 分 | reviewer 子 5 分 27 秒 (01:10:06–01:15:33) | 実装完了後にレビューが逐次で開始される構造 |

### 3.4 変更後 (本 Issue の実行) との比較

- 同一コマンド (`/nfgo`)・同一モデル (fastmetal、費用計上 0)・同一共有サービスでの比較であり、モデル・環境差は生じていない。
- ただし対象 Issue・リポジトリ・日時が異なるため、**厳密な意味での「同一代表タスクの前後比較」ではない** (参考値)。壁時間の統計的な比較には複数回の反復測定が必要であり、本 PR の残存課題とする。
- 本実行では調査 2 件を並列化し (P3)、設計委譲の入力を「全調査結果の転載」ではなく要点ダイジェストに絞る方針を採った (architect 子の入力トークン 226k 問題への対策。効果測定は PR 本文に記録)。

## 4. 制約・留意点

1. **報告は「最後の text 応答」のみ**が親に転送される。構造化結果が必要な委譲は、子に text で要点を返させるプロンプト設計が必要。
2. `state="running"` の中間報告を完了と誤認しないこと (§2.2)。
3. `experimental.subagent_depth` による子からの再委譲は、experimental 設定の不安定さと最小権限への影響から、本検証では**採用しない**判断とした (設計文書参照)。
4. 並列数の公式上限がないため、同時実行数のガードは設計側 (委譲方針) で持つ必要がある。
5. セッション DB のスキーマ (`session_v2` 等) は実装詳細であり、長期的な計測基盤 (#12) では公式 API (`session.list`、experimental job/stats API) を主系統にすべきである。

## 5. 結論

OpenCode 標準機能のみで (1) 1 ターンからの複数 subagent 並列起動、(2) 実ワークロードでの時間的重複、(3) 機械可読な実行区間の記録、がすべて確認できた。子からの再委譲は標準機能では既定不可であり、委譲の集約先を primary に置く方式が実験的設定なしで安全に実現できる。詳細は設計文書 `docs/design/0004-parallel-delegation.md` を参照。
