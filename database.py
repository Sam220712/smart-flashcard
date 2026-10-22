"""
database.py - Tầng dữ liệu (SQLite) cho ứng dụng Flashcard.

Bao gồm:
    1. Kết nối + khởi tạo schema.
    2. CRUD cho bộ thẻ (deck) và flashcard.
    3. Hàng đợi ôn tập + ghi nhận kết quả ôn (áp dụng SM-2).
    4. Truy vấn phục vụ thống kê.
    5. Xuất / nhập JSON và CSV.

Mỗi thao tác mở một kết nối riêng (rẻ với SQLite) để tránh lỗi đa luồng của
Streamlit, và được bọc trong một transaction: thành công thì commit, lỗi thì
rollback.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from sm2 import DEFAULT_EASE, MIN_EASE, SM2Result, SM2State, schedule

# Có thể đổi vị trí file DB bằng biến môi trường FLASHCARD_DB.
DB_PATH = Path(os.environ.get("FLASHCARD_DB") or Path(__file__).with_name("flashcards.db"))

EXPORT_FORMAT = "smart-flashcards"
EXPORT_VERSION = 1

# --------------------------------------------------------------------------
# 1. Kết nối & schema
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS decks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id       INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    front         TEXT NOT NULL,
    back          TEXT NOT NULL,
    -- Các cột phục vụ thuật toán SM-2
    ease_factor   REAL NOT NULL DEFAULT 2.5,
    repetitions   INTEGER NOT NULL DEFAULT 0,
    interval_days INTEGER NOT NULL DEFAULT 0,
    due_date      TEXT NOT NULL,   -- ISO yyyy-mm-dd
    last_reviewed TEXT,            -- NULL nghĩa là thẻ mới, chưa học lần nào
    lapses        INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cards_deck_due ON cards(deck_id, due_date);

-- Nhật ký ôn tập: phục vụ biểu đồ và chuỗi ngày học (streak).
-- Xóa thẻ/bộ thẻ không xóa lịch sử học (SET NULL) để streak không bị mất.
CREATE TABLE IF NOT EXISTS review_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id       INTEGER REFERENCES cards(id) ON DELETE SET NULL,
    deck_id       INTEGER REFERENCES decks(id) ON DELETE SET NULL,
    review_date   TEXT NOT NULL,
    reviewed_at   TEXT NOT NULL,
    grade         INTEGER NOT NULL,
    was_new       INTEGER NOT NULL,
    interval_days INTEGER NOT NULL,
    ease_after    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_date ON review_log(review_date);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Mở kết nối SQLite trong một transaction (commit/rollback tự động)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite mặc định TẮT ràng buộc khóa ngoại
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Tạo bảng nếu chưa có. An toàn khi gọi nhiều lần."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


# --------------------------------------------------------------------------
# 2a. CRUD bộ thẻ (Deck)
# --------------------------------------------------------------------------
def create_deck(name: str, description: str = "") -> int:
    """Tạo bộ thẻ mới, trả về id. Ném ValueError nếu tên rỗng hoặc đã tồn tại."""
    name = name.strip()
    if not name:
        raise ValueError("Tên bộ thẻ không được để trống.")
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO decks (name, description, created_at) VALUES (?, ?, ?)",
                (name, description.strip(), _now()),
            )
            return int(cur.lastrowid)
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"Bộ thẻ «{name}» đã tồn tại.") from exc


def update_deck(deck_id: int, name: str, description: str = "") -> None:
    """Đổi tên / mô tả bộ thẻ."""
    name = name.strip()
    if not name:
        raise ValueError("Tên bộ thẻ không được để trống.")
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE decks SET name = ?, description = ? WHERE id = ?",
                (name, description.strip(), deck_id),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"Bộ thẻ «{name}» đã tồn tại.") from exc


def delete_deck(deck_id: int) -> None:
    """Xóa bộ thẻ cùng toàn bộ flashcard bên trong (ON DELETE CASCADE)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))


def get_deck(deck_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM decks WHERE id = ?", (deck_id,)).fetchone()
    return dict(row) if row else None


def new_studied_today_by_deck() -> Dict[int, int]:
    """Số thẻ MỚI đã học hôm nay theo từng bộ thẻ (để áp giới hạn thẻ mới/ngày)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT deck_id, COUNT(*) AS n FROM review_log "
            "WHERE review_date = ? AND was_new = 1 AND deck_id IS NOT NULL "
            "GROUP BY deck_id",
            (_today(),),
        ).fetchall()
    return {int(r["deck_id"]): int(r["n"]) for r in rows}


def get_decks_with_counts(new_limit: int = 20) -> List[Dict[str, Any]]:
    """Danh sách bộ thẻ kèm số liệu:

    total          - tổng số thẻ
    new_cards      - số thẻ chưa học lần nào
    due_cards      - số thẻ ĐÃ học và đến hạn ôn (due_date <= hôm nay)
    new_available  - số thẻ mới còn được phép học hôm nay (theo new_limit)
    """
    studied = new_studied_today_by_deck()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.name, d.description,
                   COUNT(c.id) AS total,
                   COALESCE(SUM(CASE WHEN c.last_reviewed IS NULL THEN 1 ELSE 0 END), 0)
                       AS new_cards,
                   COALESCE(SUM(CASE WHEN c.last_reviewed IS NOT NULL
                                      AND c.due_date <= ? THEN 1 ELSE 0 END), 0)
                       AS due_cards
            FROM decks d
            LEFT JOIN cards c ON c.deck_id = d.id
            GROUP BY d.id
            ORDER BY d.name COLLATE NOCASE
            """,
            (_today(),),
        ).fetchall()

    decks: List[Dict[str, Any]] = []
    for row in rows:
        deck = dict(row)
        allowance = max(0, new_limit - studied.get(deck["id"], 0))
        deck["new_available"] = min(deck["new_cards"], allowance)
        decks.append(deck)
    return decks


# --------------------------------------------------------------------------
# 2b. CRUD flashcard
# --------------------------------------------------------------------------
def add_card(deck_id: int, front: str, back: str) -> int:
    """Thêm flashcard (nội dung hỗ trợ Markdown + LaTeX). Thẻ mới đến hạn ngay."""
    front, back = front.strip(), back.strip()
    if not front or not back:
        raise ValueError("Cần nhập đủ cả mặt trước và mặt sau.")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO cards (deck_id, front, back, ease_factor, repetitions, "
            "interval_days, due_date, created_at) VALUES (?, ?, ?, ?, 0, 0, ?, ?)",
            (deck_id, front, back, DEFAULT_EASE, _today(), _now()),
        )
        return int(cur.lastrowid)


def update_card(card_id: int, front: str, back: str) -> None:
    """Sửa nội dung thẻ (giữ nguyên tiến độ học)."""
    front, back = front.strip(), back.strip()
    if not front or not back:
        raise ValueError("Cần nhập đủ cả mặt trước và mặt sau.")
    with get_conn() as conn:
        conn.execute(
            "UPDATE cards SET front = ?, back = ? WHERE id = ?", (front, back, card_id)
        )


def delete_card(card_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))


def reset_card_progress(card_id: int) -> None:
    """Đưa thẻ về trạng thái 'mới' (xóa tiến độ SM-2)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE cards SET ease_factor = ?, repetitions = 0, interval_days = 0, "
            "due_date = ?, last_reviewed = NULL, lapses = 0 WHERE id = ?",
            (DEFAULT_EASE, _today(), card_id),
        )


def get_card(card_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return dict(row) if row else None


def list_cards(deck_id: int, search: str = "") -> List[Dict[str, Any]]:
    """Liệt kê thẻ của một bộ (mới nhất trước), có thể lọc theo từ khóa.

    Việc lọc làm bằng Python (casefold) vì LIKE của SQLite không phân biệt
    hoa/thường đúng cho chữ có dấu tiếng Việt.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE deck_id = ? ORDER BY id DESC", (deck_id,)
        ).fetchall()
    cards = [dict(r) for r in rows]
    keyword = search.strip().casefold()
    if keyword:
        cards = [
            c for c in cards
            if keyword in c["front"].casefold() or keyword in c["back"].casefold()
        ]
    return cards


# --------------------------------------------------------------------------
# 3. Hàng đợi ôn tập & ghi nhận kết quả (SM-2)
# --------------------------------------------------------------------------
def build_study_queue(deck_id: Optional[int], new_limit: int) -> List[int]:
    """Tạo danh sách id thẻ cần học hôm nay.

    Thứ tự: thẻ ĐẾN HẠN ôn (quá hạn lâu nhất trước), sau đó là thẻ MỚI
    (tối đa `new_limit` thẻ mới mỗi bộ mỗi ngày, trừ số đã học hôm nay).
    deck_id = None nghĩa là gom tất cả bộ thẻ.
    """
    studied = new_studied_today_by_deck()
    with get_conn() as conn:
        if deck_id is None:
            deck_ids = [r["id"] for r in conn.execute("SELECT id FROM decks ORDER BY id")]
            due_rows = conn.execute(
                "SELECT id FROM cards WHERE last_reviewed IS NOT NULL AND due_date <= ? "
                "ORDER BY due_date, id",
                (_today(),),
            ).fetchall()
        else:
            deck_ids = [deck_id]
            due_rows = conn.execute(
                "SELECT id FROM cards WHERE deck_id = ? AND last_reviewed IS NOT NULL "
                "AND due_date <= ? ORDER BY due_date, id",
                (deck_id, _today()),
            ).fetchall()

        queue = [int(r["id"]) for r in due_rows]
        for did in deck_ids:
            allowance = max(0, new_limit - studied.get(did, 0))
            if allowance == 0:
                continue
            new_rows = conn.execute(
                "SELECT id FROM cards WHERE deck_id = ? AND last_reviewed IS NULL "
                "ORDER BY id LIMIT ?",
                (did, allowance),
            ).fetchall()
            queue.extend(int(r["id"]) for r in new_rows)
    return queue


def apply_review(card_id: int, grade: int, relearn: bool = False) -> Optional[SM2Result]:
    """Ghi nhận một lần ôn tập: tính lại EF / số lần lặp / interval bằng SM-2.

    Args:
        card_id: thẻ vừa được đánh giá.
        grade: 0 (Quên), 1 (Khó), 2 (Tốt), 3 (Dễ).
        relearn: True nếu đây là lượt "học lại" trong cùng phiên sau khi bấm
            Quên. Lượt này KHÔNG đổi lịch SM-2 (đã phạt ở lần Quên đầu tiên) và
            không ghi log, tránh tính một thẻ hai lần.

    Returns:
        SM2Result mới, hoặc None nếu không có gì thay đổi.
    """
    if relearn:
        return None

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if row is None:
            return None

        was_new = row["last_reviewed"] is None
        state = SM2State(
            ease_factor=row["ease_factor"],
            repetitions=row["repetitions"],
            interval=row["interval_days"],
        )
        result = schedule(grade, state)

        conn.execute(
            "UPDATE cards SET ease_factor = ?, repetitions = ?, interval_days = ?, "
            "due_date = ?, last_reviewed = ?, lapses = lapses + ? WHERE id = ?",
            (
                result.ease_factor,
                result.repetitions,
                result.interval,
                result.due_date.isoformat(),
                _today(),
                1 if result.lapsed else 0,
                card_id,
            ),
        )
        conn.execute(
            "INSERT INTO review_log (card_id, deck_id, review_date, reviewed_at, grade, "
            "was_new, interval_days, ease_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                card_id,
                row["deck_id"],
                _today(),
                _now(),
                grade,
                1 if was_new else 0,
                result.interval,
                result.ease_factor,
            ),
        )
    return result


def get_next_due_date(deck_id: Optional[int] = None) -> Optional[str]:
    """Ngày sớm nhất (sau hôm nay) có thẻ đến hạn - dùng cho thông báo hoàn thành."""
    sql = (
        "SELECT MIN(due_date) AS d FROM cards "
        "WHERE last_reviewed IS NOT NULL AND due_date > ?"
    )
    params: Tuple[Any, ...] = (_today(),)
    if deck_id is not None:
        sql += " AND deck_id = ?"
        params += (deck_id,)
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    return row["d"] if row and row["d"] else None


# --------------------------------------------------------------------------
# 4. Truy vấn thống kê
# --------------------------------------------------------------------------
def fetch_cards_for_stats(deck_id: Optional[int] = None) -> List[Dict[str, Any]]:
    sql = (
        "SELECT id, deck_id, ease_factor, repetitions, interval_days, due_date, "
        "last_reviewed, lapses FROM cards"
    )
    params: Tuple[Any, ...] = ()
    if deck_id is not None:
        sql += " WHERE deck_id = ?"
        params = (deck_id,)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def fetch_review_log(deck_id: Optional[int] = None) -> List[Dict[str, Any]]:
    sql = "SELECT review_date, grade, was_new FROM review_log"
    params: Tuple[Any, ...] = ()
    if deck_id is not None:
        sql += " WHERE deck_id = ?"
        params = (deck_id,)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def compute_streaks(days: "set[date]", today: Optional[date] = None) -> Tuple[int, int]:
    """Tính (chuỗi hiện tại, chuỗi dài nhất) từ tập các ngày có học.

    Chuỗi hiện tại vẫn được giữ nếu hôm qua có học nhưng hôm nay chưa học
    (người dùng còn cả ngày để duy trì chuỗi).
    """
    if not days:
        return 0, 0
    today = today or date.today()
    ordered = sorted(days)

    longest = run = 1
    for prev, cur in zip(ordered, ordered[1:]):
        run = run + 1 if (cur - prev).days == 1 else 1
        longest = max(longest, run)

    if (today - ordered[-1]).days > 1:
        return 0, longest

    current = 1
    for i in range(len(ordered) - 1, 0, -1):
        if (ordered[i] - ordered[i - 1]).days == 1:
            current += 1
        else:
            break
    return current, longest


def get_streaks() -> Tuple[int, int]:
    """(chuỗi ngày học hiện tại, chuỗi dài nhất) tính trên toàn bộ lịch sử."""
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT review_date FROM review_log").fetchall()
    days = {date.fromisoformat(r["review_date"]) for r in rows}
    return compute_streaks(days)


# --------------------------------------------------------------------------
# 5a. Xuất dữ liệu
# --------------------------------------------------------------------------
_PROGRESS_COLUMNS = ["ease_factor", "repetitions", "interval", "due_date", "last_reviewed", "lapses"]


def _export_decks(deck_ids: Sequence[int], include_progress: bool) -> List[Dict[str, Any]]:
    """Gom dữ liệu các bộ thẻ được chọn thành cấu trúc dict thuần túy."""
    decks: List[Dict[str, Any]] = []
    with get_conn() as conn:
        for deck_id in deck_ids:
            deck = conn.execute("SELECT * FROM decks WHERE id = ?", (deck_id,)).fetchone()
            if deck is None:
                continue
            cards = []
            for row in conn.execute(
                "SELECT * FROM cards WHERE deck_id = ? ORDER BY id", (deck_id,)
            ):
                card: Dict[str, Any] = {"front": row["front"], "back": row["back"]}
                if include_progress:
                    card.update(
                        ease_factor=row["ease_factor"],
                        repetitions=row["repetitions"],
                        interval=row["interval_days"],
                        due_date=row["due_date"],
                        last_reviewed=row["last_reviewed"],
                        lapses=row["lapses"],
                    )
                cards.append(card)
            decks.append(
                {"name": deck["name"], "description": deck["description"], "cards": cards}
            )
    return decks


def export_json(deck_ids: Sequence[int], include_progress: bool = False) -> str:
    """Xuất các bộ thẻ ra chuỗi JSON (UTF-8, giữ nguyên tiếng Việt)."""
    payload = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": _now(),
        "decks": _export_decks(deck_ids, include_progress),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def export_csv(deck_ids: Sequence[int], include_progress: bool = False) -> bytes:
    """Xuất CSV (UTF-8 có BOM để Excel hiển thị đúng tiếng Việt)."""
    columns = ["deck", "front", "back"] + (_PROGRESS_COLUMNS if include_progress else [])
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for deck in _export_decks(deck_ids, include_progress):
        for card in deck["cards"]:
            row = [deck["name"], card["front"], card["back"]]
            if include_progress:
                row += [card[c] if card[c] is not None else "" for c in _PROGRESS_COLUMNS]
            writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def csv_template() -> bytes:
    """File CSV mẫu cho người dùng điền rồi nhập vào."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["deck", "front", "back"])
    writer.writerow(["Từ vựng tiếng Anh", "**abandon** (v)", "từ bỏ, bỏ rơi"])
    writer.writerow(["Vật lý", "Công thức tính động năng?", "$E_k = \\frac{1}{2}mv^2$"])
    return buffer.getvalue().encode("utf-8-sig")


# --------------------------------------------------------------------------
# 5b. Nhập dữ liệu
# --------------------------------------------------------------------------
# Chấp nhận nhiều tên cột để dễ nhập file từ nguồn khác.
_FRONT_KEYS = ("front", "question", "q", "mặt trước", "mat truoc", "mat_truoc", "câu hỏi", "cau hoi")
_BACK_KEYS = ("back", "answer", "a", "mặt sau", "mat sau", "mat_sau", "đáp án", "dap an")
_DECK_KEYS = ("deck", "bộ thẻ", "bo the", "bo_the")


def _first(row: Dict[str, Any], keys: Sequence[str]) -> Any:
    """Lấy giá trị đầu tiên không rỗng theo danh sách tên cột (đã lowercase)."""
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = DEFAULT_EASE) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_date(value: Any) -> Optional[str]:
    """Trả về chuỗi ISO yyyy-mm-dd hợp lệ hoặc None."""
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10]).isoformat()
    except ValueError:
        return None


def _normalize_card(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Chuẩn hóa một thẻ đọc từ file. Trả về None nếu thiếu mặt trước/sau."""
    row = {str(k).strip().lower(): v for k, v in raw.items() if k is not None}
    front = str(_first(row, _FRONT_KEYS) or "").strip()
    back = str(_first(row, _BACK_KEYS) or "").strip()
    if not front or not back:
        return None

    progress = None
    last_reviewed = _parse_date(row.get("last_reviewed"))
    if last_reviewed:  # chỉ giữ tiến độ nếu thẻ thực sự đã được học
        progress = {
            "ease_factor": max(MIN_EASE, _to_float(row.get("ease_factor"))),
            "repetitions": max(0, _to_int(row.get("repetitions"))),
            "interval": max(0, _to_int(_first(row, ("interval", "interval_days")))),
            "due_date": _parse_date(row.get("due_date")) or _today(),
            "last_reviewed": last_reviewed,
            "lapses": max(0, _to_int(row.get("lapses"))),
        }
    return {"front": front, "back": back, "progress": progress}


def _parse_json(raw: bytes, default_name: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"File JSON không hợp lệ: {exc}") from exc

    if isinstance(data, dict) and isinstance(data.get("decks"), list):
        deck_list = data["decks"]
    elif isinstance(data, dict) and isinstance(data.get("cards"), list):
        deck_list = [data]  # một bộ thẻ đơn lẻ
    elif isinstance(data, list):
        if data and all(isinstance(x, dict) and "cards" in x for x in data):
            deck_list = data
        else:  # danh sách thẻ trần
            deck_list = [{"name": default_name, "cards": data}]
    else:
        raise ValueError("Cấu trúc JSON không đúng: cần có khóa 'decks' hoặc 'cards'.")

    decks: List[Dict[str, Any]] = []
    for deck in deck_list:
        if not isinstance(deck, dict):
            continue
        cards = [
            card
            for card in (
                _normalize_card(c) for c in deck.get("cards", []) if isinstance(c, dict)
            )
            if card
        ]
        if cards:
            name = str(deck.get("name") or default_name).strip() or default_name
            decks.append(
                {"name": name, "description": str(deck.get("description") or ""), "cards": cards}
            )
    return decks


def _parse_csv(raw: bytes, default_name: str) -> List[Dict[str, Any]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Không đọc được file CSV. Hãy lưu file với mã hóa UTF-8.") from exc

    # Đoán dấu phân cách từ dòng tiêu đề (Excel một số vùng dùng dấu ';').
    first_line = text.splitlines()[0] if text.strip() else ""
    delimiter = max((",", ";", "\t"), key=first_line.count)

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = {h.strip().lower() for h in (reader.fieldnames or []) if h}
    if not headers & set(_FRONT_KEYS) or not headers & set(_BACK_KEYS):
        raise ValueError(
            "CSV cần dòng tiêu đề có cột 'front' và 'back' (tùy chọn thêm cột 'deck')."
        )

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for raw_row in reader:
        card = _normalize_card(raw_row)
        if card is None:
            continue
        lowered = {str(k).strip().lower(): v for k, v in raw_row.items() if k is not None}
        deck_name = str(_first(lowered, _DECK_KEYS) or default_name).strip() or default_name
        groups.setdefault(deck_name, []).append(card)

    return [{"name": n, "description": "", "cards": c} for n, c in groups.items()]


def parse_import_file(filename: str, raw: bytes, default_name: str) -> List[Dict[str, Any]]:
    """Đọc file JSON/CSV thành danh sách bộ thẻ chuẩn hóa.

    Ném ValueError (kèm thông báo tiếng Việt) nếu file sai định dạng hoặc rỗng.
    """
    lower = filename.lower()
    if lower.endswith(".json"):
        decks = _parse_json(raw, default_name)
    elif lower.endswith(".csv"):
        decks = _parse_csv(raw, default_name)
    else:
        raise ValueError("Chỉ hỗ trợ file .json hoặc .csv.")
    if not decks:
        raise ValueError("Không tìm thấy flashcard hợp lệ trong file (cần cả front và back).")
    return decks


def _unique_deck_name(conn: sqlite3.Connection, name: str) -> str:
    """Thêm hậu tố ' (2)', ' (3)'... nếu tên bộ thẻ đã tồn tại."""
    candidate, n = name, 2
    while conn.execute("SELECT 1 FROM decks WHERE name = ?", (candidate,)).fetchone():
        candidate = f"{name} ({n})"
        n += 1
    return candidate


def import_decks(
    decks: List[Dict[str, Any]], merge: bool = True, keep_progress: bool = True
) -> Dict[str, int]:
    """Ghi các bộ thẻ đã parse vào DB trong MỘT transaction (lỗi thì hoàn tác hết).

    Args:
        decks: kết quả của parse_import_file().
        merge: True  -> gộp vào bộ thẻ trùng tên, bỏ qua thẻ trùng nội dung.
               False -> luôn tạo bộ thẻ mới (tự thêm hậu tố nếu trùng tên).
        keep_progress: giữ tiến độ SM-2 nếu file có.
    """
    stats = {"decks_created": 0, "decks_merged": 0, "cards_added": 0, "cards_skipped": 0}
    with get_conn() as conn:
        for deck in decks:
            existing = conn.execute(
                "SELECT id FROM decks WHERE name = ?", (deck["name"],)
            ).fetchone()
            if existing and merge:
                deck_id = int(existing["id"])
                stats["decks_merged"] += 1
            else:
                name = _unique_deck_name(conn, deck["name"])
                cur = conn.execute(
                    "INSERT INTO decks (name, description, created_at) VALUES (?, ?, ?)",
                    (name, deck.get("description", ""), _now()),
                )
                deck_id = int(cur.lastrowid)
                stats["decks_created"] += 1

            seen = {
                (r["front"].strip(), r["back"].strip())
                for r in conn.execute(
                    "SELECT front, back FROM cards WHERE deck_id = ?", (deck_id,)
                )
            }
            for card in deck["cards"]:
                key = (card["front"], card["back"])
                if key in seen:
                    stats["cards_skipped"] += 1
                    continue
                seen.add(key)

                progress = card.get("progress") if keep_progress else None
                if progress:
                    values = (
                        progress["ease_factor"], progress["repetitions"], progress["interval"],
                        progress["due_date"], progress["last_reviewed"], progress["lapses"],
                    )
                else:
                    values = (DEFAULT_EASE, 0, 0, _today(), None, 0)
                conn.execute(
                    "INSERT INTO cards (deck_id, front, back, ease_factor, repetitions, "
                    "interval_days, due_date, last_reviewed, lapses, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (deck_id, card["front"], card["back"], *values, _now()),
                )
                stats["cards_added"] += 1
    return stats
