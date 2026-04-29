# Attendance_CSV

CSV から名簿を読み込んで、Web UI 上で出欠をチェックできるシンプルなアプリ。

## 主な機能

- CSV 名簿の取り込み（「名前」カラム必須、他カラムは自動表示）
- 文字コード自動判定（UTF-8 / UTF-8 BOM / Shift-JIS）
- **3 状態の出欠管理**: 出席 / 欠席 / 未確認（初期値）
- ドロップダウン (`<select>`) で状態を切替 - スマホではネイティブピッカーが開く
- 全員を未確認に戻すリセットボタン
- 出席 / 欠席 / 未確認 の人数カウンタを表示
- **検索 & フィルタ**:
  - 名前や任意項目を含むキーワード検索
  - 出欠状態でフィルタ
  - 一意値が 2〜15 個の項目（学年・所属など）に自動でフィルタ追加
- **2 ロールのアクセス制御**:
  - `root`: CSVアップロード・全員リセットなどの管理操作が可能
  - `user`: 出欠の閲覧と各メンバーの状態変更のみ可能

## セットアップ

```bash
# 1. 仮想環境を作成
python -m venv .venv

# 2. 仮想環境を有効化
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Windows (cmd)
.venv\Scripts\activate.bat
# macOS / Linux
source .venv/bin/activate

# 3. 依存ライブラリをインストール
pip install -r requirements.txt

# 4. .env を作成してパスワードを設定
copy .env.example .env       # Windows
# cp .env.example .env       # macOS / Linux
# .env を編集して ROOT_PASSWORD と USER_PASSWORD に別々の値を記入

# 5. 起動
python app.py
```

ブラウザで <http://localhost:5000> にアクセス。

## 使い方

1. ログイン画面で root か user のパスワードを入力（入力欄は1つ、入力値で自動判定）
2. **(root のみ)** 「CSV取込」から名簿 CSV をアップロード
   - 1 行目はヘッダー、`名前` カラムを必ず含めてください
   - 他カラム（学年、所属、メモなど）は自動で一覧表示されます
   - 取り込んだ直後は全員「未確認」状態になります
3. 出欠一覧で各メンバーのドロップダウンから 出席 / 欠席 / 未確認 を選択
   - select の色（緑/赤/無色）で現在の状態が一目で分かります
4. 上部の検索ボックスやフィルタで対象を絞り込み（一意値が少ない項目は自動でフィルタ化）
5. **(root のみ)** 「全員を未確認に戻す」で出欠状態を一括クリア

## ロールと権限

| 操作 | root | user |
|---|---|---|
| 出欠一覧の閲覧・検索・フィルタ | ✓ | ✓ |
| 各メンバーの出欠状態の変更 | ✓ | ✓ |
| CSV アップロード（名簿の入れ替え） | ✓ | ✗ |
| 全員リセット | ✓ | ✗ |

user で root 専用の操作を試みると 403 になります。

## CSV サンプル

```csv
名前,学年,所属
山田太郎,3,A組
佐藤花子,3,B組
```

## 出欠ステータス

| 値 | 意味 | 表示 |
|---|---|---|
| 0 | 未確認 | 灰色（初期値） |
| 1 | 出席   | 緑 |
| 2 | 欠席   | 赤 |

## ファイル構成

```
.
├── app.py              # Flask アプリ本体
├── requirements.txt
├── .env.example
├── templates/          # Jinja2 テンプレート
│   ├── base.html
│   ├── login.html
│   ├── index.html
│   └── upload.html
├── static/             # CSS / JS
│   ├── style.css
│   └── app.js
└── attendance.db       # 起動時に自動生成（要 .env）
```

## API

| メソッド | パス | 用途 |
|---|---|---|
| POST | `/api/set/<id>` | 指定メンバーの status を設定（form: `status=0\|1\|2`） |
| POST | `/api/reset`    | 全員を未確認 (status=0) に戻す |

レスポンスは出席 / 欠席 / 未確認 / 全体 のカウントを含む JSON を返します。
