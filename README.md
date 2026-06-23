<!-- 代表画像（ヒーロー画像）は後で差し込みます -->
<!-- ![PromptMosaic](docs/images/hero.png) -->

# PromptMosaic

[日本語](README.md) | [English](README_EN.md)

**PromptMosaic** は、画像生成 AI [InvokeAI](https://github.com/invoke-ai/InvokeAI) のための、ローカルで動くプロンプト管理・生成 GUI です。
プロンプトを「タイル」として組み立て、生成の系譜（履歴）をツリーで辿りながら、複数モデルを巡回生成できます。画像生成そのものは InvokeAI が行います。

- 🧩 **タイル方式のプロンプト編集** — 単語・文章をタイルとして並べ替え・強調・ON/OFF
- 🌳 **生成系譜（パラレルワールド履歴マップ）** — どの生成からどの生成が派生したかをツリーで可視化し、過去の任意の時点へジャンプ
- 🧠 **マルチモデルプラン** — 複数のモデル／LoRA／パラメータを一度の生成で巡回
- 🌏 **11 言語対応** — 日本語・英語・中国語（簡体／繁体）・韓国語・ドイツ語・フランス語・スペイン語・イタリア語・ポルトガル語（ブラジル）・ロシア語
- 💾 **シンプルなデータ保全** — `data` フォルダを丸ごとコピーしてバックアップ

> **バージョン:** 1.4.0
> **対象 InvokeAI:** 6.13 以降
> **対応 OS:** Windows 11（PySide6 / Python 3.10〜3.12）

---

## 📖 ドキュメント

| ドキュメント | 内容 |
| --- | --- |
| **[チュートリアル（はじめての方へ）](docs/TUTORIAL.md)** | インストール → InvokeAI 連携 → 最初の 1 枚を生成するまで |
| **[操作説明書（全機能リファレンス）](docs/MANUAL.md)** | 画面構成・各機能の詳しい使い方 |

---

## ⚡ クイックスタート

```bat
:: 1. 依存パッケージのインストール（初回のみ）
install_windows.bat

:: 2. 起動
PromptMosaic.bat
```

初回起動時に **InvokeAI データ取得** ウィザードが開きます。InvokeAI（6.13 以降）を起動した状態で、画面の案内に従ってモデル・LoRA・生成テンプレートを取得してください。詳しくは [チュートリアル](docs/TUTORIAL.md) を参照してください。

---

## 📜 ライセンス

PromptMosaic は **[MIT License](LICENSE)** で公開されています。

- フォーク・改変・再配布・商用利用は自由です（クローズドソース製品への組み込みも可）。
- 再配布の際は、著作権表示とライセンス全文を含めてください。
- 本ソフトウェアは無保証で提供されます。

```
Copyright (c) 2026 i1623
```

> ⚠️ 同梱・依存する各サードパーティ（InvokeAI / Qt / その他ライブラリ）は、それぞれ独自のライセンスに従います。再配布の際は、`requirements.txt` に含まれる依存パッケージと各プロジェクトのライセンス条項を確認してください。特に Qt (PySide6) は LGPL/GPL/商用のいずれかの条項が適用されます。

---

## 🙏 謝辞（Acknowledgments）

PromptMosaic は、多くの素晴らしいオープンソースプロジェクトの上に成り立っています。各プロジェクトの作者・コミュニティに深く感謝します。

### InvokeAI

PromptMosaic は単体では画像を生成しません。実際の画像生成はすべて **[InvokeAI](https://github.com/invoke-ai/InvokeAI)**（[Invoke](https://www.invoke.com/) およびコミュニティ）が行います。PromptMosaic は InvokeAI の txt2img ワークフローグラフを「生成テンプレート」として取得し、プロンプト・シード・パラメータだけを差し替えて InvokeAI のキューへ送信する補助ツールです。

InvokeAI の開発者・コントリビューターの皆さまの長年の取り組みなくして、本アプリは存在し得ません。心より御礼申し上げます。
（プロンプト記法は InvokeAI が採用する **[Compel](https://github.com/damian0815/compel)** スタイルに準拠しています。）

### UI フレームワーク・配色

| プロジェクト | 用途 | 作者 / 提供 |
| --- | --- | --- |
| **[Qt for Python (PySide6 / shiboken6)](https://www.qt.io/qt-for-python)** | GUI フレームワーク全体 | The Qt Company |
| **[Catppuccin](https://github.com/catppuccin/catppuccin)** | テーマ配色（Mocha / Latte） | Catppuccin org |

### Python ライブラリ

| ライブラリ | 用途 | 作者 / 提供 | ライセンス |
| --- | --- | --- | --- |
| **[Pillow](https://github.com/python-pillow/Pillow)** | PNG メタデータ解析・画像処理 | Jeffrey A. Clark and contributors | MIT-CMU (HPND) |
| **[httpx](https://github.com/encode/httpx)** / **[httpcore](https://github.com/encode/httpcore)** | InvokeAI / LLM への HTTP 通信 | Encode (Tom Christie 他) | BSD-3-Clause |
| **[h11](https://github.com/python-hyper/h11)** | HTTP/1.1 プロトコル | Nathaniel J. Smith | MIT |
| **[anyio](https://github.com/agronholm/anyio)** | 非同期 I/O 抽象 | Alex Grönholm | MIT |
| **[certifi](https://github.com/certifi/python-certifi)** | ルート証明書 | Kenneth Reitz / PSF | MPL-2.0 |
| **[idna](https://github.com/kjd/idna)** | 国際化ドメイン名 | Kim Davies | BSD-3-Clause |
| **[exceptiongroup](https://github.com/agronholm/exceptiongroup)** | 例外グループの後方互換 | Alex Grönholm | MIT / PSF |
| **[typing_extensions](https://github.com/python/typing_extensions)** | 型ヒント拡張 | Python core team | PSF |

### 任意連携

- **[LM Studio](https://lmstudio.ai/)** — プロンプトの翻訳・自動分類に使えるローカル LLM サーバー（任意機能）。

> 再配布の際は各依存パッケージのライセンス条項に従ってください（特に Qt は LGPL/GPL/商用のいずれかの条項が適用されます）。

---

## ⚠️ 配布物に含めないもの

公開・再配布する際は、開発者個人のデータを含めないでください。

```
.venv/                     仮想環境（環境依存・非可搬）
__pycache__/               キャッシュ
data/*.db / *.db-wal/-shm  個人の設定・プロンプト・履歴・NSFW フラグ
data/template_cache*.json  InvokeAI 環境固有のワークフローグラフ
data/thumbnails/           履歴サムネイル
data/model_thumbnails/     モデルサムネイル
images/                    生成画像のローカルコピー
```

リリースパッケージは、初回起動時に新しい DB を生成する状態で配布してください。
