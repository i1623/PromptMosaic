![PromptMosaic and Invoke side by side](docs/images/hero.jpg)

# PromptMosaic

[日本語](README.md) | [English](README_EN.md)

**PromptMosaic** は、画像生成ツール [Invoke](https://invoke.ai/) のための、ローカルで動くプロンプト管理・生成 GUI です。
Invoke と画面を分割して並べ、Invoke 側で生成結果を見ながら PromptMosaic 側でプロンプトを編集・再生成する使い方を想定しています。英語プロンプトと日本語などの表示名を同時に扱いながら、プロンプトを「タイル」として組み立てられることを重視しています。LM Studio などのローカル LLM を翻訳用に設定すると、日本語の単語や文章を英語プロンプトへ変換してタイル化できます。画像生成そのものは Invoke が行います。

- 🖥️ **Invoke との分割画面運用** — Invoke のビューアを横で開き、PromptMosaic でプロンプト編集・履歴管理・再生成
- 🧩 **二言語表示のタイル編集** — 英語プロンプトと訳文の日本語などの表示名を並べて見ながら、単語・文章をタイルとして D&D で並べ替え・強調・ON/OFF
- 🧱 **タイルグループ** — タイルをグループ化し、全て・順番・ランダム選択で差分生成に利用できます。D&D 可能で、グループとして保存もできます。
- 🌐 **翻訳支援** — LM Studio などのローカル LLM を使い、日本語の単語や文章を英語プロンプト用タイルに変換
- 🌳 **生成系譜（パラレルワールド履歴マップ）** — どの生成からどの生成が派生したかをツリーで可視化し、過去の任意の時点へジャンプ
- 🧠 **マルチモデルプラン** — 複数のモデル／LoRA／パラメータを一度の生成で巡回
- 🌏 **11 言語対応** — 日本語・英語・中国語（簡体／繁体）・韓国語・ドイツ語・フランス語・スペイン語・イタリア語・ポルトガル語（ブラジル）・ロシア語
- 💾 **シンプルなデータ保全** — `data` フォルダを丸ごとコピーしてバックアップ

> **バージョン:** 1.4.5
> **対象 Invoke:** 6.13 以降
> **対応 OS:** Windows 11（PySide6 / Python 3.11 推奨）

---

## 📖 ドキュメント

| ドキュメント | 内容 |
| --- | --- |
| **[チュートリアル（はじめての方へ）](docs/TUTORIAL.md)** | インストール → Invoke 連携 → 最初の 1 枚を生成するまで |
| **[操作説明書（全機能リファレンス）](docs/MANUAL.md)** | 画面構成・各機能の詳しい使い方 |

---

## ⚡ クイックスタート

### 1. ファイル一式をダウンロードする

このページの右上あたりにある緑色の **Code** ボタンを押し、**Download ZIP** を選びます。ダウンロードした ZIP ファイルを右クリックして **すべて展開** を選び、好きな場所に展開してください。

> ⚠️ `install_windows.bat` だけを単体で保存しても動きません。必ず ZIP を丸ごと展開して、PromptMosaic のフォルダごと使ってください。

### 2. インストールする

展開したフォルダを開き、`install_windows.bat` をダブルクリックします。黒い画面が開いて、必要なものを自動で入れます。

Windows が「発行元を確認できませんでした」と表示した場合は、ファイル名が PromptMosaic フォルダ内の `install_windows.bat` であることを確認して **実行** を押します。インストーラは起動用の `PromptMosaic.bat` から同じ警告をできるだけ解除します。詳しくは [チュートリアル](docs/TUTORIAL.md) を見てください。

### 3. 起動する

インストールが終わったら、同じフォルダにある `PromptMosaic.bat` をダブルクリックします。

```bat
:: 初回だけ
install_windows.bat

:: 2回目以降はこちら
PromptMosaic.bat
```

初回起動時に **Invoke データ取得** ウィザードが開きます。Invoke（6.13 以降）を起動した状態で、画面の案内に従ってモデル・LoRA・生成テンプレートを取得してください。詳しくは [チュートリアル](docs/TUTORIAL.md) を参照してください。

失敗した場合は黒い画面に理由が表示されます。内容を確認してから、キーボードの何かのキーを押して閉じてください。

---

## 🔄 アップデート

PromptMosaic のプロンプト、履歴、取得済みモデル情報、生成テンプレートなどは `data` フォルダに保存されます。アップデートでは **古い PromptMosaic フォルダを削除しないでください**。

1. PromptMosaic を終了します。
2. 今まで使っていた PromptMosaic フォルダを開きます。
3. `update_windows.bat` をダブルクリックします。
4. 黒い画面で `Update complete.` と表示されたら完了です。

`update_windows.bat` は、先に `data` フォルダを `_update_backups` にコピーしてから、Git 版では `git pull`、ZIP 版では GitHub から最新版 ZIP を自動取得して本体ファイルを更新します。その後、必要な Python パッケージも更新します。

---

## 📜 ライセンス

PromptMosaic は **[MIT License](LICENSE)** で公開されています。

- フォーク・改変・再配布・商用利用は自由です（クローズドソース製品への組み込みも可）。
- 再配布の際は、著作権表示とライセンス全文を含めてください。
- 本ソフトウェアは無保証で提供されます。

```
Copyright (c) 2026 i1623
```

> ⚠️ 同梱・依存する各サードパーティ（Invoke / Qt / その他ライブラリ）は、それぞれ独自のライセンスに従います。再配布の際は、`requirements.txt` に含まれる依存パッケージと各プロジェクトのライセンス条項を確認してください。PySide6 / shiboken6 の wheel は `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` として配布されています。

---

## 🛠️ 開発方針とサポート範囲

PromptMosaic は個人開発のツールです。作者自身の Invoke 制作環境で継続して使える状態を保つこと、特に Invoke の変更へ追従することを主な目的にしています。

不具合報告や改善提案は歓迎しますが、すべての要望への対応や継続的な個別サポートは保証できません。機能追加は、作者自身の制作フローに必要なもの、または Invoke 連携の維持に必要なものを優先します。

PromptMosaic は、Claude Code と OpenAI Codex を使った AI 支援の「バイブコーディング」によって開発されています。仕様決定、確認、テスト、公開判断は i1623 が行っています。

ドキュメントやUI文言の多言語翻訳も AI 支援で作成しています。確認はしていますが、誤訳や不自然な表現が残る場合があります。見つけた場合は、やさしく報告してもらえると助かります。

---

## 🙏 謝辞（Acknowledgments）

PromptMosaic は、多くの素晴らしいオープンソースプロジェクトの上に成り立っています。各プロジェクトの作者・コミュニティに深く感謝します。

### Invoke

PromptMosaic は単体では画像を生成しません。実際の画像生成はすべて **[Invoke](https://invoke.ai/)** とそのコミュニティが行います。PromptMosaic は Invoke の txt2img ワークフローグラフを「生成テンプレート」として取得し、プロンプト・シード・パラメータだけを差し替えて Invoke のキューへ送信する補助ツールです。

Invoke の開発者・コントリビューターの皆さまの長年の取り組みなくして、本アプリは存在し得ません。心より御礼申し上げます。
（タイルの強調表記は、Invoke / Compel 風の `+` / `-` や数値ウェイトの一部を扱います。ただし、PromptMosaic が Compel の全構文を実装・保証するものではありません。）

### UI フレームワーク・配色

| プロジェクト | 用途 | 作者 / 提供 |
| --- | --- | --- |
| **[Qt for Python (PySide6 / shiboken6)](https://www.qt.io/qt-for-python)** | GUI フレームワーク全体 | The Qt Company（LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only） |
| **[Catppuccin](https://github.com/catppuccin/catppuccin)** | テーマ配色（Mocha / Latte） | Catppuccin org |

### Python ライブラリ

| ライブラリ | 用途 | 作者 / 提供 | ライセンス |
| --- | --- | --- | --- |
| **[Pillow](https://github.com/python-pillow/Pillow)** | PNG メタデータ解析・画像処理 | Jeffrey A. Clark and contributors | MIT-CMU (HPND) |
| **[httpx](https://github.com/encode/httpx)** / **[httpcore](https://github.com/encode/httpcore)** | Invoke / LLM への HTTP 通信 | Encode (Tom Christie 他) | BSD-3-Clause |
| **[h11](https://github.com/python-hyper/h11)** | HTTP/1.1 プロトコル | Nathaniel J. Smith | MIT |
| **[anyio](https://github.com/agronholm/anyio)** | 非同期 I/O 抽象 | Alex Grönholm | MIT |
| **[certifi](https://github.com/certifi/python-certifi)** | ルート証明書 | Kenneth Reitz / PSF | MPL-2.0 |
| **[idna](https://github.com/kjd/idna)** | 国際化ドメイン名 | Kim Davies | BSD-3-Clause |
| **[exceptiongroup](https://github.com/agronholm/exceptiongroup)** | 例外グループの後方互換 | Alex Grönholm | MIT / PSF |
| **[typing_extensions](https://github.com/python/typing_extensions)** | 型ヒント拡張 | Python core team | PSF |

### 任意連携

- **[LM Studio](https://lmstudio.ai/)** — プロンプトの翻訳・自動分類に使えるローカル LLM サーバー（任意機能）。

> 再配布の際は各依存パッケージのライセンス条項に従ってください。特に PySide6 / shiboken6 は LGPL/GPL 系ライセンスの条件を確認してください。

