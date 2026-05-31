import csv
import functools
import hmac
import io
import json
import os
import secrets
import sqlite3
from pathlib import Path

import chardet
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "attendance.db"
NAME_COLUMN = "名前"
GROUP_COLUMN = "班"

ROLE_ROOT = "root"
ROLE_USER = "user"

ROOT_PASSWORD = os.environ.get("ROOT_PASSWORD")
USER_PASSWORD = os.environ.get("USER_PASSWORD")

# 旧 ATTENDANCE_PASSWORD が残っているケースへの分かりやすいガイド
if not ROOT_PASSWORD or not USER_PASSWORD:
    legacy = os.environ.get("ATTENDANCE_PASSWORD")
    hint = ""
    if legacy:
        hint = (
            "\n（旧 ATTENDANCE_PASSWORD は廃止されました。"
            "ROOT_PASSWORD と USER_PASSWORD を別々に設定してください）"
        )
    raise RuntimeError(
        "ROOT_PASSWORD と USER_PASSWORD を .env に設定してください。"
        ".env.example を参照してください。" + hint
    )

if ROOT_PASSWORD == USER_PASSWORD:
    raise RuntimeError(
        "ROOT_PASSWORD と USER_PASSWORD には別の値を設定してください。"
    )

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# 出欠ステータス: 0=未確認, 1=出席, 2=欠席
STATUS_UNKNOWN = 0
STATUS_PRESENT = 1
STATUS_ABSENT = 2
VALID_STATUSES = {STATUS_UNKNOWN, STATUS_PRESENT, STATUS_ABSENT}


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                extra_data TEXT NOT NULL DEFAULT '{}',
                status INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        # 旧スキーマ (present 列) からのマイグレーション
        cols = {row[1] for row in conn.execute("PRAGMA table_info(members)")}
        if "present" in cols and "status" not in cols:
            conn.execute(
                "ALTER TABLE members ADD COLUMN status INTEGER NOT NULL DEFAULT 0"
            )
            conn.execute(
                "UPDATE members SET status = CASE WHEN present = 1 THEN 1 ELSE 0 END"
            )
            try:
                conn.execute("ALTER TABLE members DROP COLUMN present")
            except sqlite3.OperationalError:
                # SQLite 3.35 未満: 列削除は諦めるが status は使えるので動作はする
                pass
        if "group_num" not in cols:
            conn.execute(
                "ALTER TABLE members ADD COLUMN group_num TEXT NOT NULL DEFAULT ''"
            )


def meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def load_secret_key() -> str:
    env_key = os.environ.get("FLASK_SECRET_KEY")
    if env_key:
        return env_key
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        existing = meta_get(conn, "secret_key")
        if existing:
            return existing
        new_key = secrets.token_hex(32)
        meta_set(conn, "secret_key", new_key)
        conn.commit()
        return new_key


init_db()
app.secret_key = load_secret_key()


def current_role() -> str | None:
    return session.get("role")


def is_authenticated() -> bool:
    return current_role() in {ROLE_ROOT, ROLE_USER}


def is_root() -> bool:
    return current_role() == ROLE_ROOT


def root_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not is_root():
            if request.path.startswith("/api/"):
                abort(403)
            flash("この操作は管理者(root)のみ実行できます", "error")
            return redirect(url_for("index"))
        return view(*args, **kwargs)

    return wrapper


@app.context_processor
def inject_role():
    return {"is_root": is_root(), "current_role": current_role()}


@app.before_request
def require_login():
    allowed = {"login", "static"}
    if request.endpoint in allowed:
        return None
    if not is_authenticated():
        if request.path.startswith("/api/"):
            abort(401)
        return redirect(url_for("login", next=request.path))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        submitted = request.form.get("password", "")
        role = None
        # compare_digest を両方走らせて、結果でロールを判定（タイミング攻撃対策）
        if hmac.compare_digest(submitted, ROOT_PASSWORD):
            role = ROLE_ROOT
        elif hmac.compare_digest(submitted, USER_PASSWORD):
            role = ROLE_USER
        if role:
            session.clear()
            session["role"] = role
            next_url = request.args.get("next") or url_for("index")
            if not next_url.startswith("/"):
                next_url = url_for("index")
            return redirect(next_url)
        flash("パスワードが違います", "error")
    if is_authenticated():
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def status_counts(members: list[dict]) -> dict[str, int]:
    return {
        "present": sum(1 for m in members if m["status"] == STATUS_PRESENT),
        "absent": sum(1 for m in members if m["status"] == STATUS_ABSENT),
        "unknown": sum(1 for m in members if m["status"] == STATUS_UNKNOWN),
        "total": len(members),
    }


@app.route("/")
def index():
    db = get_db()
    extra_columns_raw = meta_get(db, "extra_columns")
    extra_columns = json.loads(extra_columns_raw) if extra_columns_raw else []
    rows = db.execute(
        "SELECT id, name, extra_data, status, group_num FROM members ORDER BY sort_order ASC"
    ).fetchall()
    members = []
    for row in rows:
        try:
            extras = json.loads(row["extra_data"])
        except (json.JSONDecodeError, TypeError):
            extras = {}
        members.append(
            {
                "id": row["id"],
                "name": row["name"],
                "status": row["status"],
                "group_num": row["group_num"],
                "extras": extras,
            }
        )
    counts = status_counts(members)
    filter_columns = build_filter_columns(members, extra_columns)
    group_values = sorted({m["group_num"] or "" for m in members})
    if 2 <= len(group_values) <= MAX_FILTER_VALUES:
        filter_columns = [{"name": "班", "options": group_values}] + filter_columns
    return render_template(
        "index.html",
        members=members,
        extra_columns=extra_columns,
        filter_columns=filter_columns,
        counts=counts,
        STATUS_UNKNOWN=STATUS_UNKNOWN,
        STATUS_PRESENT=STATUS_PRESENT,
        STATUS_ABSENT=STATUS_ABSENT,
    )


# 一意値が少ない (2 <= distinct <= MAX_FILTER_VALUES) extra カラムだけフィルタ対象にする
MAX_FILTER_VALUES = 15


def build_filter_columns(
    members: list[dict], extra_columns: list[str]
) -> list[dict]:
    result = []
    for col in extra_columns:
        values = sorted({(m["extras"].get(col) or "") for m in members})
        if 2 <= len(values) <= MAX_FILTER_VALUES:
            result.append({"name": col, "options": values})
    return result


@app.route("/upload", methods=["GET", "POST"])
@root_required
def upload():
    if request.method == "POST":
        file = request.files.get("csv_file")
        if not file or not file.filename:
            flash("CSV ファイルを選択してください", "error")
            return render_template("upload.html")

        raw = file.read()
        if not raw:
            flash("ファイルが空です", "error")
            return render_template("upload.html")

        encoding = detect_encoding(raw)
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            flash(
                f"文字コードを判定できませんでした (推定: {encoding})。"
                "UTF-8 か Shift-JIS で保存し直してください。",
                "error",
            )
            return render_template("upload.html")

        # BOM を取り除く
        if text.startswith("﻿"):
            text = text.lstrip("﻿")

        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or NAME_COLUMN not in reader.fieldnames:
            flash(
                f"ヘッダーに「{NAME_COLUMN}」カラムが見つかりませんでした。"
                "1行目に列名を入れてください。",
                "error",
            )
            return render_template("upload.html")

        # None 列名（空白ヘッダーや列ずれ）は無視
        # GROUP_COLUMN は group_num 専用列として扱い extra_columns から除外
        has_group_col = GROUP_COLUMN in reader.fieldnames
        extra_columns = [
            c for c in reader.fieldnames
            if c is not None and c != NAME_COLUMN and c != GROUP_COLUMN
        ]

        records = []
        for idx, row in enumerate(reader):
            name = (row.get(NAME_COLUMN) or "").strip()
            if not name:
                continue  # 名前のない行はスキップ
            group_num = (row.get(GROUP_COLUMN) or "").strip() if has_group_col else ""
            extras = {col: (row.get(col) or "") for col in extra_columns}
            records.append(
                (name, json.dumps(extras, ensure_ascii=False), STATUS_UNKNOWN, idx, group_num)
            )

        if not records:
            flash("有効な行がありませんでした（「名前」が全て空です）", "error")
            return render_template("upload.html")

        db = get_db()
        try:
            with db:
                db.execute("DELETE FROM members")
                db.execute("DELETE FROM sqlite_sequence WHERE name = 'members'")
                db.executemany(
                    "INSERT INTO members(name, extra_data, status, sort_order, group_num) "
                    "VALUES(?, ?, ?, ?, ?)",
                    records,
                )
                meta_set(
                    db,
                    "extra_columns",
                    json.dumps(extra_columns, ensure_ascii=False),
                )
        except sqlite3.Error as exc:
            flash(f"DB エラー: {exc}", "error")
            return render_template("upload.html")

        flash(f"{len(records)} 件のメンバーを登録しました", "success")
        return redirect(url_for("index"))

    return render_template("upload.html")


def detect_encoding(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    detected = chardet.detect(raw)
    enc = (detected.get("encoding") or "utf-8").lower()
    # 日本語 CSV では cp932 / shift_jis を統一して扱う
    if enc in {"shift_jis", "shift-jis", "windows-31j", "ms932"}:
        return "cp932"
    return enc


def _counts_payload(db: sqlite3.Connection) -> dict[str, int]:
    row = db.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS present,
            SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS absent,
            SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS unknown
        FROM members
        """,
        (STATUS_PRESENT, STATUS_ABSENT, STATUS_UNKNOWN),
    ).fetchone()
    return {
        "total_count": row["total"] or 0,
        "present_count": row["present"] or 0,
        "absent_count": row["absent"] or 0,
        "unknown_count": row["unknown"] or 0,
    }


@app.post("/api/set/<int:member_id>")
def api_set(member_id: int):
    raw = request.form.get("status", request.json.get("status") if request.is_json else None)
    try:
        new_status = int(raw)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid status"}), 400
    if new_status not in VALID_STATUSES:
        return jsonify({"error": "invalid status"}), 400

    db = get_db()
    row = db.execute("SELECT id FROM members WHERE id = ?", (member_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    with db:
        db.execute(
            "UPDATE members SET status = ? WHERE id = ?", (new_status, member_id)
        )
    payload = {"id": member_id, "status": new_status, **_counts_payload(db)}
    return jsonify(payload)


@app.post("/api/reset")
@root_required
def api_reset():
    db = get_db()
    with db:
        db.execute("UPDATE members SET status = ?", (STATUS_UNKNOWN,))
    return jsonify(_counts_payload(db))


LINE_COLUMN = "LINEの名前"


@app.route("/group")
def group_page():
    db = get_db()
    extra_columns_raw = meta_get(db, "extra_columns")
    extra_columns = json.loads(extra_columns_raw) if extra_columns_raw else []
    has_line_col = LINE_COLUMN in extra_columns
    rows = db.execute(
        "SELECT id, name, extra_data, group_num FROM members ORDER BY sort_order ASC"
    ).fetchall()
    members = []
    for row in rows:
        try:
            extras = json.loads(row["extra_data"])
        except (json.JSONDecodeError, TypeError):
            extras = {}
        members.append(
            {
                "id": row["id"],
                "name": row["name"],
                "group_num": row["group_num"],
                "line_name": extras.get(LINE_COLUMN, "") if has_line_col else None,
            }
        )
    _derived = sorted({m["group_num"] for m in members if m["group_num"]})
    _fixed = [o for o in ["サブ1", "サブ2"] if o not in _derived]
    group_options = _derived + _fixed
    return render_template(
        "group.html",
        members=members,
        has_line_col=has_line_col,
        group_options=group_options,
    )


@app.post("/api/set_group/<int:member_id>")
def api_set_group(member_id: int):
    new_group = (request.form.get("group_num", "")).strip()
    db = get_db()
    row = db.execute(
        "SELECT group_num FROM members WHERE id = ?", (member_id,)
    ).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    old_group = row["group_num"]
    if old_group == new_group:
        return jsonify({"id": member_id, "group_num": new_group, "changed": False})
    with db:
        db.execute(
            "UPDATE members SET group_num = ? WHERE id = ?", (new_group, member_id)
        )
    return jsonify({"id": member_id, "group_num": new_group, "changed": True})


@app.errorhandler(413)
def too_large(_e):
    flash("ファイルが大きすぎます（上限 5MB）", "error")
    return redirect(url_for("upload"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
