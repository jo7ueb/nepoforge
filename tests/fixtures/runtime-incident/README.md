# PR #82 匿名化事故 fixture

このディレクトリは公開 PR #82 の匿名化 CSV をそのまま収録する。
出典: [Issue comment 7188328906](https://github.com/jo7ueb/nepoforge/pull/82#issuecomment-7188328906)

- `launches.csv`: 起動記録
- `messages.csv`: メッセージごとの token/cost/finish 観測
- `tools.csv`: tool 呼び出しごとの結果・経過時間

データ内の session は alias 化済みで、本文・実 session ID・認証情報は含まれない。
CSV は実請求明細ではなく、`cost_recorded=0` も無料を意味しない。掲載単価
(2026-11-26) による推定ではキャッシュ読取を通常入力単価として扱う。
fixture からは承認済み総予算、途中 gate snapshot、interrupt 主体/時刻、
実請求額、キャッシュ実単価は確認できない。
