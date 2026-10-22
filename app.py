"""
app.py - Flashcard thông minh (Streamlit + SQLite + thuật toán SM-2).

Chạy ứng dụng:
    streamlit run app.py

Cấu trúc dự án:
    app.py       Giao diện Streamlit (file này)
    database.py  Tầng dữ liệu SQLite, nhập/xuất JSON-CSV
    sm2.py       Thuật toán lặp lại ngắt quãng SM-2 (hàm thuần túy)
"""

from __future__ import annotations

import json
import math
import time
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

import database as db
from sm2 import (
    GRADE_EASY,
    GRADE_FORGOT,
    GRADE_GOOD,
    GRADE_HARD,
    GRADE_LABELS,
    MATURE_INTERVAL_DAYS,
    SM2State,
    format_interval,
    preview_intervals,
)

# --------------------------------------------------------------------------
# Hằng số giao diện
# --------------------------------------------------------------------------
PAGE_HOME = "🏠 Tổng quan"
PAGE_STUDY = "🎓 Ôn tập"
PAGE_MANAGE = "🗂️ Quản lý"
PAGE_STATS = "📊 Thống kê"
PAGE_IO = "🔄 Nhập / Xuất"

CARDS_PER_PAGE = 10
DEFAULT_NEW_LIMIT = 20

# (grade, biểu tượng, phím tắt). Phím 1-4 tương ứng grade 0-3.
RATING_BUTTONS = [
    (GRADE_FORGOT, "😵", "1"),
    (GRADE_HARD, "😓", "2"),
    (GRADE_GOOD, "🙂", "3"),
    (GRADE_EASY, "😎", "4"),
]

# --------------------------------------------------------------------------
# CSS: thẻ flashcard giống thẻ ghi chú (index card) có dòng kẻ và vạch đỏ,
# xếp chồng như một bộ bài. Chỉ dùng màu trong suốt để hợp cả giao diện sáng/tối.
# Streamlit >= 1.42 gắn class `st-key-<key>` cho phần tử có tham số key.
# --------------------------------------------------------------------------
APP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700&display=swap');

h1, h2, h3 { font-family: 'Be Vietnam Pro', 'Segoe UI', sans-serif; letter-spacing: -0.01em; }
.block-container { padding-top: 2.2rem; max-width: 1100px; }

.stButton button,
.stDownloadButton button,
[data-testid="stFormSubmitButton"] button {
    width: 100%; border-radius: 8px; font-weight: 600;
}

/* ---------- Thẻ flashcard ---------- */
.st-key-fc_front, .st-key-fc_back {
    --rule: rgba(47, 91, 234, 0.20);
    --head: rgba(229, 72, 77, 0.55);
    position: relative;
    min-height: 320px;
    padding: 1rem 2.2rem 2rem 2.2rem;
    justify-content: center;
    border: 1px solid rgba(127, 127, 127, 0.35);
    border-radius: 6px;
    background-color: rgba(127, 127, 127, 0.07);
    background-image:
        linear-gradient(var(--head), var(--head)),
        repeating-linear-gradient(to bottom, transparent 0, transparent 1.9rem,
                                  var(--rule) 1.9rem, var(--rule) calc(1.9rem + 1px));
    background-size: 100% 2px, 100% 100%;
    background-position: 0 2.7rem, 0 0;
    background-repeat: no-repeat, repeat;
    /* các lớp bóng nhỏ phía dưới tạo cảm giác chồng thẻ */
    box-shadow: 0 1px 0 rgba(127, 127, 127, 0.35),
                0 7px 0 -3px rgba(127, 127, 127, 0.22),
                0 14px 0 -6px rgba(127, 127, 127, 0.12);
}
.st-key-fc_back {
    --rule: rgba(16, 150, 110, 0.22);
    --head: rgba(16, 150, 110, 0.65);
}
.st-key-fc_front { animation: fcDeal 0.28s ease-out; }
.st-key-fc_back  { animation: fcFlip 0.45s ease-out; transform-origin: 50% 0; }

@keyframes fcDeal {
    from { opacity: 0; transform: translateX(28px) rotate(1.2deg); }
    to   { opacity: 1; transform: none; }
}
@keyframes fcFlip {
    from { opacity: 0.2; transform: perspective(1100px) rotateX(-90deg); }
    to   { opacity: 1;   transform: perspective(1100px) rotateX(0deg); }
}
@media (prefers-reduced-motion: reduce) {
    .st-key-fc_front, .st-key-fc_back { animation: none; }
}

.st-key-fc_front [data-testid="stMarkdownContainer"],
.st-key-fc_back [data-testid="stMarkdownContainer"] {
    font-family: 'Be Vietnam Pro', 'Segoe UI', sans-serif;
    font-size: 1.3rem;
    text-align: center;
}
.st-key-fc_front [data-testid="stMarkdownContainer"] p,
.st-key-fc_back [data-testid="stMarkdownContainer"] p {
    line-height: calc(1.9rem + 1px);
    margin: 0 0 calc(1.9rem + 1px) 0;
}
.fc-tag {
    text-align: left; font-size: 0.85rem; line-height: 1.4rem; opacity: 0.75;
    font-weight: 600;
}

/* Câu hỏi thu nhỏ hiển thị phía trên đáp án sau khi lật thẻ */
.st-key-fc_question {
    padding: 0.7rem 1.1rem;
    margin-bottom: 0.4rem;
    border-left: 3px solid rgba(47, 91, 234, 0.65);
    border-radius: 4px;
    background: rgba(127, 127, 127, 0.07);
}
.st-key-fc_question [data-testid="stMarkdownContainer"] { opacity: 0.85; font-size: 0.98rem; }
.st-key-fc_question [data-testid="stMarkdownContainer"] p { margin-bottom: 0.3rem; }

/* ---------- Nút đánh giá: mỗi mức một màu ---------- */
.st-key-rate_0 button { border: 1.5px solid #E5484D; color: #E5484D; background: transparent; }
.st-key-rate_1 button { border: 1.5px solid #E08A00; color: #E08A00; background: transparent; }
.st-key-rate_2 button { border: 1.5px solid #2FA66A; color: #2FA66A; background: transparent; }
.st-key-rate_3 button { border: 1.5px solid #2F5BEA; color: #2F5BEA; background: transparent; }
.st-key-rate_0 button:hover { background: rgba(229, 72, 77, 0.12); border-color: #E5484D; color: #E5484D; }
.st-key-rate_1 button:hover { background: rgba(224, 138, 0, 0.12); border-color: #E08A00; color: #E08A00; }
.st-key-rate_2 button:hover { background: rgba(47, 166, 106, 0.12); border-color: #2FA66A; color: #2FA66A; }
.st-key-rate_3 button:hover { background: rgba(47, 91, 234, 0.12); border-color: #2F5BEA; color: #2F5BEA; }
.fc-hint { text-align: center; font-size: 0.85rem; opacity: 0.7; margin-top: -0.4rem; }
</style>
"""

# --------------------------------------------------------------------------
# Phím tắt: Streamlit không hỗ trợ sự kiện bàn phím, nên ta chèn một đoạn JS
# vào trang cha (qua iframe components.html, cùng origin). JS chỉ "bấm hộ"
# các nút có key tương ứng, nên nếu JS không chạy thì giao diện vẫn dùng bình thường.
#   Space / Enter : lật thẻ; khi đã lật thì chọn "Tốt"
#   1 2 3 4       : Quên, Khó, Tốt, Dễ
# --------------------------------------------------------------------------
SHORTCUT_JS = r"""
(function () {
  if (window.__fcShortcutsInstalled) { return; }
  window.__fcShortcutsInstalled = true;
  var lastFired = 0;
  var swallowSpaceUntil = 0;
  var TEXT_TYPES = ['text', 'search', 'number', 'password', 'email', 'url', 'tel'];

  function isTyping(el) {
    if (!el) { return false; }
    var tag = (el.tagName || '').toLowerCase();
    if (tag === 'textarea' || tag === 'select' || el.isContentEditable) { return true; }
    if (tag === 'input') { return TEXT_TYPES.indexOf((el.type || 'text').toLowerCase()) !== -1; }
    return false;
  }
  function press(selector) {
    var btn = document.querySelector(selector + ' button');
    if (btn && !btn.disabled) { btn.click(); return true; }
    return false;
  }
  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey || e.repeat || isTyping(e.target)) { return; }
    var now = Date.now();
    if (now - lastFired < 250) { return; }
    var handled = false;
    if (e.key === ' ' || e.key === 'Enter') {
      handled = press('.st-key-flip_btn') || press('.st-key-rate_2');
      if (handled && e.key === ' ') { swallowSpaceUntil = now + 600; }
    } else {
      var idx = ['1', '2', '3', '4'].indexOf(e.key);
      if (idx !== -1) { handled = press('.st-key-rate_' + idx); }
    }
    if (handled) { lastFired = now; e.preventDefault(); e.stopPropagation(); }
  }, true);
  document.addEventListener('keyup', function (e) {
    if (e.key === ' ' && Date.now() < swallowSpaceUntil) { e.preventDefault(); e.stopPropagation(); }
  }, true);
})();
"""


def inject_shortcuts() -> None:
    """Chèn JS phím tắt vào trang cha (một lần cho mỗi lần tải trang)."""
    loader = (
        "<script>(function(){try{var d=window.parent.document;"
        "if(!d.getElementById('fc-shortcuts')){var s=d.createElement('script');"
        "s.id='fc-shortcuts';s.textContent=" + json.dumps(SHORTCUT_JS) + ";"
        "d.head.appendChild(s);}}catch(e){console.warn('Phím tắt không khả dụng',e);}})();"
        "</script>"
    )
    components.html(loader, height=0)


# --------------------------------------------------------------------------
# Hàm tiện ích chung
# --------------------------------------------------------------------------
def prepare_md(text: str) -> str:
    """Giữ nguyên xuống dòng đơn khi hiển thị Markdown.

    Markdown chuẩn gộp các dòng liên tiếp thành một đoạn. Ta thêm 2 dấu cách
    cuối dòng (hard break) cho mọi dòng thường, trừ dòng trong khối code
    (```) và khối công thức $$...$$ để không làm hỏng chúng.
    """
    out: List[str] = []
    in_code = in_math = False
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if in_code:
            out.append(line)
            continue
        toggles_math = line.count("$$") % 2 == 1
        if in_math or toggles_math:
            out.append(line)
            if toggles_math:
                in_math = not in_math
            continue
        out.append(line + "  " if line.strip() else line)
    return "\n".join(out)


def render_md(text: str) -> None:
    """Hiển thị nội dung thẻ: Markdown + LaTeX ($...$ và $$...$$)."""
    st.markdown(prepare_md(text))


def safe_label(text: str, limit: int = 60) -> str:
    """Rút gọn văn bản để làm tiêu đề expander (bỏ ký tự Markdown/LaTeX)."""
    cleaned = " ".join(text.split())
    for ch in "$*_`[]#<>~":
        cleaned = cleaned.replace(ch, "")
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def flash(message: str, icon: str = "✅") -> None:
    """Xếp hàng một thông báo toast để hiện sau lần rerun kế tiếp."""
    st.session_state.setdefault("_flash", []).append((message, icon))


def show_flash() -> None:
    for message, icon in st.session_state.pop("_flash", []):
        st.toast(message, icon=icon)


def go_to(page: str) -> None:
    """Callback chuyển trang (đổi giá trị của radio điều hướng)."""
    st.session_state["page"] = page


def init_state() -> None:
    """Khởi tạo các khóa session_state dùng chung."""
    defaults = {
        "page": PAGE_HOME,
        "new_limit": DEFAULT_NEW_LIMIT,
        "study_active": False,
        "study_queue": [],  # danh sách (card_id, is_relearn)
        "study_pos": 0,
        "study_flipped": False,
        "study_counts": {g: 0 for g in GRADE_LABELS},
        "study_relearn": 0,
        "study_deck_id": None,
        "study_started": 0.0,
        "study_celebrated": False,
        "pending_del_card": None,
        "import_ver": 0,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def create_sample_deck() -> None:
    """Callback: tạo bộ thẻ mẫu minh họa Markdown, LaTeX và code."""
    samples = [
        (
            "Nghiệm của phương trình bậc hai $ax^2 + bx + c = 0$ ($a \\neq 0$)?",
            r"$$x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}$$"
            "\n\nPhương trình có nghiệm khi $\\Delta = b^2 - 4ac \\ge 0$.",
        ),
        (
            "Phát biểu **định luật II Newton**.",
            r"$$\vec{F} = m\vec{a}$$"
            "\n\nGia tốc tỉ lệ thuận với lực tác dụng và tỉ lệ nghịch với khối lượng.",
        ),
        (
            "Động năng của vật khối lượng $m$, vận tốc $v$?",
            r"$E_k = \frac{1}{2}mv^2$ (đơn vị: J)",
        ),
        (
            "Cân bằng phương trình: $\\mathrm{Fe} + \\mathrm{O_2} \\rightarrow \\mathrm{Fe_2O_3}$",
            r"$$4\,\mathrm{Fe} + 3\,\mathrm{O_2} \rightarrow 2\,\mathrm{Fe_2O_3}$$",
        ),
        (
            "**ephemeral** (adj)",
            "Ngắn ngủi, phù du, chóng tàn.\n\n*Ví dụ:* Fame can be *ephemeral*.",
        ),
        (
            "Chiến dịch Điện Biên Phủ kết thúc vào năm nào?",
            "**1954** (ngày 7/5/1954).",
        ),
        (
            "Python: tạo danh sách bình phương của 0..4?",
            "```python\nsquares = [x ** 2 for x in range(5)]\n# [0, 1, 4, 9, 16]\n```",
        ),
    ]
    name = "📘 Bộ thẻ mẫu"
    try:
        deck_id = db.create_deck(name, "Ví dụ về Markdown, LaTeX và code")
    except ValueError:
        deck_id = db.create_deck(f"{name} {datetime.now():%H:%M:%S}", "Ví dụ về Markdown, LaTeX và code")
    for front, back in samples:
        db.add_card(deck_id, front, back)
    flash(f"Đã tạo bộ thẻ mẫu với {len(samples)} flashcard.", "✨")


# --------------------------------------------------------------------------
# Logic phiên ôn tập
# --------------------------------------------------------------------------
def start_session(deck_id: Optional[int]) -> bool:
    """Tạo hàng đợi và bắt đầu phiên ôn tập. deck_id=None -> tất cả bộ thẻ."""
    s = st.session_state
    ids = db.build_study_queue(deck_id, int(s.new_limit))
    if not ids:
        flash("Hiện không có thẻ nào cần học.", "🎉")
        return False
    s.study_active = True
    s.study_deck_id = deck_id
    s.study_queue = [(card_id, False) for card_id in ids]
    s.study_pos = 0
    s.study_flipped = False
    s.study_counts = {g: 0 for g in GRADE_LABELS}
    s.study_relearn = 0
    s.study_started = time.time()
    s.study_celebrated = False
    return True


def go_study(deck_id: Optional[int]) -> None:
    """Callback từ trang Tổng quan: bắt đầu học ngay và chuyển sang trang Ôn tập."""
    start_session(deck_id)
    st.session_state["page"] = PAGE_STUDY


def end_session() -> None:
    st.session_state.study_active = False


def flip_card() -> None:
    st.session_state.study_flipped = True


def rate_card(grade: int) -> None:
    """Callback khi người học bấm một mức đánh giá.

    * Lượt học đầu tiên của thẻ: cập nhật SM-2 + ghi log.
    * Bấm "Quên": thẻ được xếp lại cuối hàng đợi để học lại ngay trong phiên
      (lượt học lại không đổi lịch SM-2, xem db.apply_review).
    """
    s = st.session_state
    if s.study_pos >= len(s.study_queue):
        return
    card_id, is_relearn = s.study_queue[s.study_pos]
    db.apply_review(card_id, grade, relearn=is_relearn)
    if is_relearn:
        s.study_relearn += 1
    else:
        s.study_counts[grade] += 1
    if grade == GRADE_FORGOT:
        s.study_queue.append((card_id, True))
    s.study_pos += 1
    s.study_flipped = False


# --------------------------------------------------------------------------
# Trang: Tổng quan
# --------------------------------------------------------------------------
def page_home() -> None:
    s = st.session_state
    st.title("🧠 Flashcard thông minh")

    decks = db.get_decks_with_counts(int(s.new_limit))
    if not decks:
        st.subheader("Bắt đầu với bộ thẻ đầu tiên")
        st.write(
            "Bạn chưa có bộ thẻ nào. Tạo bộ thẻ mẫu để xem thử Markdown, công thức LaTeX "
            "và lịch ôn tập, hoặc tự tạo bộ thẻ trong trang Quản lý."
        )
        left, right = st.columns(2)
        left.button("✨ Tạo bộ thẻ mẫu", type="primary", on_click=create_sample_deck)
        right.button("🗂️ Đi tới Quản lý", on_click=go_to, args=(PAGE_MANAGE,))
        return

    total_due = sum(d["due_cards"] for d in decks)
    total_new = sum(d["new_available"] for d in decks)
    total_cards = sum(d["total"] for d in decks)
    current, longest = db.get_streaks()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Cần ôn hôm nay", total_due)
    m2.metric("Thẻ mới có thể học", total_new)
    m3.metric("Tổng số thẻ", total_cards)
    m4.metric("Chuỗi ngày học", f"{current} ngày", help=f"Kỷ lục của bạn: {longest} ngày")

    todo_all = total_due + total_new
    if todo_all:
        st.button(
            f"▶️ Học tất cả bộ thẻ ({todo_all} thẻ)",
            type="primary",
            on_click=go_study,
            args=(None,),
        )
    else:
        st.success("🎉 Bạn đã hoàn thành mọi thẻ cần học hôm nay. Hẹn gặp lại vào lần ôn kế tiếp!")

    st.subheader("Bộ thẻ của bạn")
    for deck in decks:
        todo = deck["due_cards"] + deck["new_available"]
        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 4, 2], vertical_alignment="center")
            c1.markdown(f"**{deck['name']}**")
            if deck["description"]:
                c1.caption(deck["description"])
            if todo:
                c2.markdown(
                    f"Cần ôn **{deck['due_cards']}** thẻ, thẻ mới **{deck['new_available']}**, "
                    f"tổng **{deck['total']}** thẻ"
                )
            elif deck["total"] == 0:
                c2.markdown("Bộ thẻ trống, hãy thêm flashcard trong trang Quản lý")
            else:
                c2.markdown(f"Đã xong hôm nay (tổng **{deck['total']}** thẻ) ✅")
            c3.button(
                "Học ngay",
                key=f"home_study_{deck['id']}",
                disabled=todo == 0,
                type="primary" if todo else "secondary",
                on_click=go_study,
                args=(deck["id"],),
            )


# --------------------------------------------------------------------------
# Trang: Ôn tập
# --------------------------------------------------------------------------
def page_study() -> None:
    if st.session_state.study_active:
        render_study_session()
    else:
        render_study_setup()


def render_study_setup() -> None:
    """Màn hình chọn bộ thẻ trước khi bắt đầu phiên học."""
    s = st.session_state
    st.title("🎓 Ôn tập")

    decks = db.get_decks_with_counts(int(s.new_limit))
    if not decks:
        st.info("Bạn chưa có bộ thẻ nào. Hãy tạo bộ thẻ và thêm flashcard trước.")
        st.button("🗂️ Đi tới Quản lý", on_click=go_to, args=(PAGE_MANAGE,))
        return

    by_id = {d["id"]: d for d in decks}
    options: List[Optional[int]] = [None] + list(by_id)
    if "study_deck_select" in s and s.study_deck_select not in options:
        s.study_deck_select = None  # bộ thẻ đã bị xóa

    def option_label(deck_id: Optional[int]) -> str:
        if deck_id is None:
            count = sum(d["due_cards"] + d["new_available"] for d in decks)
            return f"Tất cả bộ thẻ ({count} thẻ cần học)"
        deck = by_id[deck_id]
        return f"{deck['name']} ({deck['due_cards'] + deck['new_available']} thẻ cần học)"

    deck_id = st.selectbox("Chọn bộ thẻ", options, format_func=option_label, key="study_deck_select")
    chosen = decks if deck_id is None else [by_id[deck_id]]
    due = sum(d["due_cards"] for d in chosen)
    new = sum(d["new_available"] for d in chosen)

    c1, c2, c3 = st.columns(3)
    c1.metric("Cần ôn hôm nay", due)
    c2.metric("Thẻ mới hôm nay", new)
    c3.metric("Tổng lượt học", due + new)

    if due + new == 0:
        st.success("🎉 Bạn đã hoàn thành tất cả thẻ cần học hôm nay!")
        next_due = db.get_next_due_date(deck_id)
        if next_due:
            delta = (date.fromisoformat(next_due) - date.today()).days
            st.info(f"Thẻ tiếp theo đến hạn vào {date.fromisoformat(next_due):%d/%m/%Y} (sau {delta} ngày).")
        st.caption("Muốn học thêm thẻ mới? Tăng giá trị «Thẻ mới tối đa» ở thanh bên.")
    else:
        st.button(
            f"▶️ Bắt đầu ôn tập ({due + new} thẻ)",
            type="primary",
            on_click=start_session,
            args=(deck_id,),
        )


def render_rating_buttons(card: Dict, is_relearn: bool) -> None:
    """Hàng 4 nút đánh giá, kèm dự đoán khoảng thời gian ôn kế tiếp."""
    state = SM2State(card["ease_factor"], card["repetitions"], card["interval_days"])
    previews = preview_intervals(state)
    columns = st.columns(4)
    for column, (grade, emoji, hotkey) in zip(columns, RATING_BUTTONS):
        with column:
            st.button(
                f"{emoji} {GRADE_LABELS[grade]} [{hotkey}]",
                key=f"rate_{grade}",
                on_click=rate_card,
                args=(grade,),
            )
            if is_relearn:
                hint = "học lại ngay" if grade == GRADE_FORGOT else "hoàn thành"
            elif grade == GRADE_FORGOT:
                hint = f"{format_interval(previews[grade])}, học lại ngay"
            else:
                hint = f"sau {format_interval(previews[grade])}"
            st.markdown(f'<div class="fc-hint">{hint}</div>', unsafe_allow_html=True)


def render_study_session() -> None:
    """Giao diện học: mặt trước -> bấm lật -> mặt sau + 4 nút đánh giá."""
    s = st.session_state
    queue = s.study_queue
    if s.study_pos >= len(queue):
        render_session_complete()
        return

    card_id, is_relearn = queue[s.study_pos]
    card = db.get_card(card_id)
    if card is None:  # thẻ bị xóa giữa chừng: bỏ qua
        s.study_pos += 1
        s.study_flipped = False
        st.rerun()
    deck = db.get_deck(card["deck_id"]) or {"name": "không rõ"}
    is_new = card["last_reviewed"] is None and not is_relearn

    inject_shortcuts()
    _, middle, _ = st.columns([1, 6, 1])
    with middle:
        done, total = s.study_pos, len(queue)
        st.progress(done / total, text=f"Đã xong {done} trên {total} lượt")
        badge = "🔁 Học lại" if is_relearn else ("🆕 Thẻ mới" if is_new else "📖 Ôn tập")
        st.caption(f"{badge} từ bộ «{deck['name']}»")

        if not s.study_flipped:
            with st.container(key="fc_front"):
                st.markdown('<div class="fc-tag">Câu hỏi</div>', unsafe_allow_html=True)
                render_md(card["front"])
            st.button("🔄 Lật thẻ  (Space)", key="flip_btn", type="primary", on_click=flip_card)
        else:
            with st.container(key="fc_question"):
                render_md(card["front"])
            with st.container(key="fc_back"):
                st.markdown('<div class="fc-tag">Đáp án</div>', unsafe_allow_html=True)
                render_md(card["back"])
            render_rating_buttons(card, is_relearn)

        st.caption(
            "⌨️ Phím tắt: Space hoặc Enter để lật thẻ. Sau khi lật: 1 = Quên, 2 = Khó, "
            "3 = Tốt, 4 = Dễ (Space hoặc Enter = Tốt)."
        )
        st.button("⏹️ Kết thúc phiên", key="end_btn", on_click=end_session)


def finish_to_home() -> None:
    end_session()
    go_to(PAGE_HOME)


def render_session_complete() -> None:
    """Thông báo chúc mừng khi hết hàng đợi."""
    s = st.session_state
    counts = s.study_counts
    reviewed = sum(counts.values())
    if not s.study_celebrated:
        st.balloons()
        s.study_celebrated = True

    st.success(f"🎉 Chúc mừng! Bạn đã hoàn thành tất cả thẻ cần học hôm nay ({reviewed} thẻ).")
    cols = st.columns(5)
    cols[0].metric("Thẻ đã học", reviewed)
    for col, grade in zip(cols[1:], GRADE_LABELS):
        col.metric(GRADE_LABELS[grade], counts[grade])

    minutes = max(1, round((time.time() - s.study_started) / 60))
    st.caption(f"Bạn học khoảng {minutes} phút, trong đó {s.study_relearn} lượt học lại thẻ đã quên.")

    next_due = db.get_next_due_date(s.study_deck_id)
    if next_due:
        delta = (date.fromisoformat(next_due) - date.today()).days
        st.info(f"Lượt ôn tiếp theo: {date.fromisoformat(next_due):%d/%m/%Y} (sau {delta} ngày).")

    left, right = st.columns(2)
    left.button("🏠 Về tổng quan", on_click=finish_to_home)
    right.button("🎓 Chọn bộ thẻ khác", on_click=end_session)


# --------------------------------------------------------------------------
# Trang: Quản lý bộ thẻ & flashcard
# --------------------------------------------------------------------------
def page_manage() -> None:
    st.title("🗂️ Quản lý bộ thẻ và flashcard")
    tab_decks, tab_cards = st.tabs(["📁 Bộ thẻ", "🃏 Flashcard"])
    with tab_decks:
        render_deck_manager()
    with tab_cards:
        render_card_manager()


def render_deck_manager() -> None:
    s = st.session_state
    with st.form("create_deck_form", clear_on_submit=True):
        name = st.text_input("Tên bộ thẻ mới", placeholder="Ví dụ: Từ vựng tiếng Anh")
        description = st.text_input("Mô tả (tuỳ chọn)", placeholder="Ví dụ: 600 từ TOEIC")
        submitted = st.form_submit_button("➕ Tạo bộ thẻ", type="primary")
    if submitted:
        try:
            new_id = db.create_deck(name, description)
        except ValueError as exc:
            st.error(str(exc))
        else:
            s["manage_deck_id"] = new_id  # chọn sẵn bộ thẻ vừa tạo ở tab Flashcard
            flash(f"Đã tạo bộ thẻ «{name.strip()}».", "📁")
            st.rerun()

    decks = db.get_decks_with_counts(int(s.new_limit))
    if not decks:
        st.info("Chưa có bộ thẻ nào. Hãy tạo bộ thẻ đầu tiên ở trên.")
        return

    st.subheader("Danh sách bộ thẻ")
    for deck in decks:
        with st.expander(f"📁 {deck['name']} ({deck['total']} thẻ)"):
            with st.form(f"edit_deck_{deck['id']}"):
                new_name = st.text_input("Tên bộ thẻ", value=deck["name"])
                new_desc = st.text_input("Mô tả", value=deck["description"])
                saved = st.form_submit_button("💾 Lưu thay đổi")
            if saved:
                try:
                    db.update_deck(deck["id"], new_name, new_desc)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    flash("Đã cập nhật bộ thẻ.", "💾")
                    st.rerun()

            confirm = st.checkbox(
                "Tôi hiểu rằng xóa bộ thẻ sẽ xóa vĩnh viễn mọi flashcard bên trong",
                key=f"confirm_del_deck_{deck['id']}",
            )
            if st.button("🗑️ Xóa bộ thẻ", key=f"del_deck_{deck['id']}", disabled=not confirm):
                db.delete_deck(deck["id"])
                flash(f"Đã xóa bộ thẻ «{deck['name']}».", "🗑️")
                st.rerun()


def add_card_callback(deck_id: int) -> None:
    """Callback nút Thêm thẻ: lưu rồi xóa nội dung hai ô nhập."""
    s = st.session_state
    try:
        db.add_card(deck_id, s.get("new_front", ""), s.get("new_back", ""))
    except ValueError as exc:
        flash(str(exc), "⚠️")
        return
    s["new_front"] = ""
    s["new_back"] = ""
    flash("Đã thêm flashcard.", "✅")


def card_status_text(card: Dict) -> str:
    if card["last_reviewed"] is None:
        return "🆕 mới"
    days = (date.fromisoformat(card["due_date"]) - date.today()).days
    return "🔴 đến hạn" if days <= 0 else f"⏳ còn {days} ngày"


def render_card_manager() -> None:
    s = st.session_state
    decks = db.get_decks_with_counts(int(s.new_limit))
    if not decks:
        st.info("Hãy tạo bộ thẻ ở tab «Bộ thẻ» trước.")
        return

    names = {d["id"]: d["name"] for d in decks}
    if s.get("manage_deck_id") not in names:
        s["manage_deck_id"] = decks[0]["id"]
    deck_id = st.selectbox(
        "Chọn bộ thẻ", list(names), format_func=lambda i: names[i], key="manage_deck_id"
    )

    # ----- Thêm thẻ mới (có xem trước trực tiếp) -----
    st.subheader("Thêm flashcard")
    editor, preview = st.columns(2)
    with editor:
        st.text_area(
            "Mặt trước (câu hỏi)", key="new_front", height=140,
            placeholder="Ví dụ: Công thức nghiệm của phương trình bậc hai?",
        )
        st.text_area(
            "Mặt sau (đáp án)", key="new_back", height=140,
            placeholder=r"Ví dụ: $$x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}$$",
        )
        st.button(
            "➕ Thêm thẻ", type="primary", on_click=add_card_callback, args=(deck_id,),
            key="add_card_btn",
        )
    with preview:
        st.markdown("**Xem trước**")
        with st.container(border=True):
            render_md(s.get("new_front") or "*Mặt trước sẽ hiển thị ở đây*")
        with st.container(border=True):
            render_md(s.get("new_back") or "*Mặt sau sẽ hiển thị ở đây*")
        st.caption("Nhấn Ctrl+Enter hoặc bấm ra ngoài ô nhập để cập nhật xem trước.")

    with st.expander("📐 Hướng dẫn Markdown và LaTeX"):
        st.markdown(
            r"""
| Muốn hiển thị | Gõ |
|---|---|
| Chữ đậm, chữ nghiêng | `**đậm**`, `*nghiêng*` |
| Danh sách | `- mục 1` rồi xuống dòng `- mục 2` |
| Code | khối ba dấu huyền (```) kèm tên ngôn ngữ |
| Công thức cùng dòng | `$E = mc^2$` |
| Công thức riêng một dòng | `$$\frac{a}{b}$$` |
| Phân số, căn, tổng | `\frac{a}{b}`, `\sqrt{x}`, `\sum_{i=1}^{n} i` |
| Hóa học | `\mathrm{H_2O}`, `\rightarrow` |
| Ký tự đô la bình thường | `\$` |
"""
        )

    # ----- Danh sách thẻ: tìm kiếm + phân trang -----
    st.subheader("Danh sách flashcard")
    search = st.text_input("🔍 Tìm trong bộ thẻ", key=f"search_{deck_id}")
    cards = db.list_cards(deck_id, search)
    st.caption(f"{len(cards)} flashcard")
    if not cards:
        st.info("Chưa có flashcard nào khớp. Hãy thêm thẻ ở phía trên.")
        return

    total_pages = max(1, math.ceil(len(cards) / CARDS_PER_PAGE))
    page_key = f"cards_page_{deck_id}"
    if s.get(page_key, 1) > total_pages:
        s[page_key] = total_pages
    page_no = 1
    if total_pages > 1:
        page_no = int(st.number_input(
            f"Trang (tổng {total_pages})", min_value=1, max_value=total_pages, step=1, key=page_key
        ))
    start = (page_no - 1) * CARDS_PER_PAGE
    for card in cards[start:start + CARDS_PER_PAGE]:
        render_card_editor(card)


def render_card_editor(card: Dict) -> None:
    """Một dòng expander cho phép sửa / xem trước / đặt lại / xóa thẻ."""
    s = st.session_state
    cid = card["id"]
    with st.expander(f"#{cid} {safe_label(card['front'])} ({card_status_text(card)})"):
        with st.form(f"edit_card_{cid}"):
            new_front = st.text_area("Mặt trước", value=card["front"], height=110)
            new_back = st.text_area("Mặt sau", value=card["back"], height=110)
            saved = st.form_submit_button("💾 Lưu thay đổi")
        if saved:
            try:
                db.update_card(cid, new_front, new_back)
            except ValueError as exc:
                st.error(str(exc))
            else:
                flash("Đã lưu thẻ.", "💾")
                st.rerun()

        if st.checkbox("Xem trước thẻ đã lưu", key=f"preview_{cid}"):
            with st.container(border=True):
                render_md(card["front"])
            with st.container(border=True):
                render_md(card["back"])

        if card["last_reviewed"] is not None:
            st.caption(
                f"Hệ số dễ {card['ease_factor']:.2f}, đã nhớ liên tiếp {card['repetitions']} lần, "
                f"khoảng cách {card['interval_days']} ngày, đã quên {card['lapses']} lần, "
                f"hạn ôn {date.fromisoformat(card['due_date']):%d/%m/%Y}."
            )
        else:
            st.caption("Thẻ chưa được học lần nào.")

        if s.pending_del_card == cid:
            st.warning("Xóa vĩnh viễn thẻ này?")
            yes, no = st.columns(2)
            if yes.button("Xóa thẻ", key=f"yes_del_{cid}", type="primary"):
                db.delete_card(cid)
                s.pending_del_card = None
                flash("Đã xóa thẻ.", "🗑️")
                st.rerun()
            if no.button("Giữ lại", key=f"no_del_{cid}"):
                s.pending_del_card = None
                st.rerun()
        else:
            reset_col, del_col = st.columns(2)
            if reset_col.button("↩️ Đặt lại tiến độ", key=f"reset_{cid}"):
                db.reset_card_progress(cid)
                flash("Thẻ đã trở về trạng thái mới.", "↩️")
                st.rerun()
            if del_col.button("🗑️ Xóa thẻ", key=f"del_{cid}"):
                s.pending_del_card = cid
                st.rerun()


# --------------------------------------------------------------------------
# Trang: Thống kê
# --------------------------------------------------------------------------
STATUS_COLORS = {"Mới": "#8B93A7", "Đang học": "#E08A00", "Đã nhớ": "#2FA66A"}


def classify_cards(cards: pd.DataFrame) -> pd.DataFrame:
    """Gán trạng thái cho từng thẻ: Mới / Đang học / Đã nhớ (interval >= 21 ngày)."""
    cards = cards.copy()
    cards["status"] = "Đang học"
    cards.loc[cards["last_reviewed"].isna(), "status"] = "Mới"
    mature = cards["last_reviewed"].notna() & (cards["interval_days"] >= MATURE_INTERVAL_DAYS)
    cards.loc[mature, "status"] = "Đã nhớ"
    return cards


def chart_layout(fig: go.Figure, title: str) -> go.Figure:
    fig.update_layout(
        title=title, height=330, margin=dict(l=10, r=10, t=50, b=10), legend_title_text=""
    )
    return fig


def build_activity_chart(logs: List[Dict], today: date) -> go.Figure:
    """Cột chồng: số thẻ mới và thẻ ôn tập mỗi ngày trong 30 ngày gần nhất."""
    counter = Counter((r["review_date"], r["was_new"]) for r in logs)
    rows = []
    for offset in range(29, -1, -1):
        day = today - timedelta(days=offset)
        label = day.strftime("%d/%m")
        rows.append({"Ngày": label, "Loại": "Thẻ mới", "Số thẻ": counter.get((day.isoformat(), 1), 0)})
        rows.append({"Ngày": label, "Loại": "Ôn tập", "Số thẻ": counter.get((day.isoformat(), 0), 0)})
    fig = px.bar(
        pd.DataFrame(rows), x="Ngày", y="Số thẻ", color="Loại",
        color_discrete_map={"Thẻ mới": "#2F5BEA", "Ôn tập": "#2FA66A"},
    )
    fig.update_xaxes(type="category", title_text="")
    return chart_layout(fig, "Số thẻ đã học mỗi ngày (30 ngày gần nhất)")


def build_status_chart(cards: pd.DataFrame) -> go.Figure:
    order = ["Mới", "Đang học", "Đã nhớ"]
    values = [int((cards["status"] == name).sum()) for name in order]
    fig = go.Figure(go.Pie(
        labels=order, values=values, hole=0.55, sort=False,
        marker=dict(colors=[STATUS_COLORS[n] for n in order]),
    ))
    return chart_layout(fig, f"Mức độ ghi nhớ (đã nhớ = khoảng cách từ {MATURE_INTERVAL_DAYS} ngày)")


def build_forecast_chart(cards: pd.DataFrame, today: date) -> go.Figure:
    """Dự báo số thẻ đến hạn trong 14 ngày tới (thẻ quá hạn tính vào hôm nay)."""
    reviewed = cards[cards["status"] != "Mới"]
    offsets = Counter(
        max(0, (date.fromisoformat(d) - today).days) for d in reviewed["due_date"]
    )
    rows = []
    for i in range(14):
        label = "Hôm nay" if i == 0 else (today + timedelta(days=i)).strftime("%d/%m")
        rows.append({"Ngày": label, "Số thẻ": offsets.get(i, 0)})
    fig = px.bar(pd.DataFrame(rows), x="Ngày", y="Số thẻ", color_discrete_sequence=["#E08A00"])
    fig.update_xaxes(type="category", title_text="")
    return chart_layout(fig, "Dự báo thẻ đến hạn trong 14 ngày tới")


def page_stats() -> None:
    s = st.session_state
    st.title("📊 Thống kê học tập")

    decks = db.get_decks_with_counts(int(s.new_limit))
    if not decks:
        st.info("Chưa có dữ liệu. Hãy tạo bộ thẻ và bắt đầu học.")
        return
    names = {d["id"]: d["name"] for d in decks}
    options: List[Optional[int]] = [None] + list(names)
    if "stats_deck" in s and s.stats_deck not in options:
        s.stats_deck = None
    deck_id = st.selectbox(
        "Phạm vi thống kê", options, key="stats_deck",
        format_func=lambda i: "Tất cả bộ thẻ" if i is None else names[i],
    )

    cards = pd.DataFrame(db.fetch_cards_for_stats(deck_id))
    if cards.empty:
        st.info("Bộ thẻ này chưa có flashcard nào.")
        return
    cards = classify_cards(cards)
    logs = db.fetch_review_log(deck_id)
    today = date.today()
    today_iso = today.isoformat()

    n_new = int((cards["status"] == "Mới").sum())
    n_learning = int((cards["status"] == "Đang học").sum())
    n_mature = int((cards["status"] == "Đã nhớ").sum())
    due_now = int(((cards["status"] != "Mới") & (cards["due_date"] <= today_iso)).sum())

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Tổng số thẻ", len(cards))
    c2.metric("Thẻ mới", n_new)
    c3.metric("Đang học", n_learning)
    c4.metric("Đã nhớ", n_mature, help=f"Thẻ có khoảng cách ôn từ {MATURE_INTERVAL_DAYS} ngày trở lên")
    c5.metric("Đến hạn hôm nay", due_now)

    current, longest = db.get_streaks()
    reviews_today = sum(1 for r in logs if r["review_date"] == today_iso)
    cutoff = (today - timedelta(days=29)).isoformat()
    recent = [r for r in logs if r["review_date"] >= cutoff]
    retention = (
        f"{sum(1 for r in recent if r['grade'] >= GRADE_HARD) / len(recent) * 100:.0f}%"
        if recent else "chưa có"
    )

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Đã học hôm nay", reviews_today)
    d2.metric("Chuỗi ngày học", f"{current} ngày", help="Tính trên toàn bộ lịch sử học")
    d3.metric("Chuỗi dài nhất", f"{longest} ngày")
    d4.metric("Tỉ lệ nhớ (30 ngày)", retention, help="Tỉ lệ lượt trả lời không bấm «Quên»")

    st.plotly_chart(build_activity_chart(logs, today), key="chart_activity")
    left, right = st.columns(2)
    with left:
        st.plotly_chart(build_status_chart(cards), key="chart_status")
    with right:
        st.plotly_chart(build_forecast_chart(cards, today), key="chart_forecast")

    reviewed = cards[cards["status"] != "Mới"]
    if not reviewed.empty:
        fig = px.histogram(
            reviewed, x="ease_factor", nbins=15, color_discrete_sequence=["#2F5BEA"],
            labels={"ease_factor": "Hệ số dễ (EF)", "count": "Số thẻ"},
        )
        fig.update_layout(yaxis_title="Số thẻ", bargap=0.08)
        st.plotly_chart(
            chart_layout(fig, "Phân bố hệ số dễ (EF càng thấp, thẻ càng khó)"), key="chart_ease"
        )


# --------------------------------------------------------------------------
# Trang: Nhập / Xuất
# --------------------------------------------------------------------------
def page_io() -> None:
    st.title("🔄 Nhập và xuất dữ liệu")
    tab_export, tab_import = st.tabs(["📤 Xuất", "📥 Nhập"])
    with tab_export:
        render_export_tab()
    with tab_import:
        render_import_tab()


def render_export_tab() -> None:
    s = st.session_state
    decks = db.get_decks_with_counts(int(s.new_limit))
    if not decks:
        st.info("Chưa có bộ thẻ nào để xuất.")
        return
    names = {d["id"]: f"{d['name']} ({d['total']} thẻ)" for d in decks}
    ids = list(names)
    selected = st.multiselect(
        "Chọn bộ thẻ cần xuất", ids, default=ids, format_func=lambda i: names[i]
    )
    include_progress = st.checkbox(
        "Kèm tiến độ học (hệ số dễ, ngày ôn kế tiếp...)", value=False,
        help="Bỏ chọn nếu bạn chỉ muốn chia sẻ nội dung thẻ cho người khác.",
    )
    if not selected:
        st.warning("Hãy chọn ít nhất một bộ thẻ.")
        return

    stamp = date.today().strftime("%Y%m%d")
    left, right = st.columns(2)
    left.download_button(
        "⬇️ Tải file JSON", data=db.export_json(selected, include_progress),
        file_name=f"flashcards_{stamp}.json", mime="application/json",
    )
    right.download_button(
        "⬇️ Tải file CSV", data=db.export_csv(selected, include_progress),
        file_name=f"flashcards_{stamp}.csv", mime="text/csv",
    )
    st.caption("File CSV dùng mã hóa UTF-8 có BOM nên mở bằng Excel hoặc Google Sheets vẫn đúng tiếng Việt.")


def render_import_tab() -> None:
    s = st.session_state
    st.markdown(
        "Chọn file **JSON** (xuất từ ứng dụng này) hoặc **CSV** có dòng tiêu đề "
        "`front`, `back` và tuỳ chọn `deck`."
    )
    st.download_button(
        "📄 Tải file CSV mẫu", data=db.csv_template(),
        file_name="flashcards_mau.csv", mime="text/csv",
    )
    uploaded = st.file_uploader(
        "Chọn file để nhập", type=["json", "csv"], key=f"import_file_{s.import_ver}"
    )
    if uploaded is None:
        return

    stem = uploaded.name.rsplit(".", 1)[0]
    default_name = st.text_input(
        "Tên bộ thẻ mặc định", value=stem, key=f"import_name_{s.import_ver}_{uploaded.name}",
        help="Dùng cho các thẻ mà file không ghi rõ thuộc bộ thẻ nào.",
    )
    try:
        parsed = db.parse_import_file(uploaded.name, uploaded.getvalue(), default_name.strip() or stem)
    except ValueError as exc:
        st.error(str(exc))
        return

    existing = {d["name"].casefold() for d in db.get_decks_with_counts()}
    st.dataframe(
        pd.DataFrame([
            {
                "Bộ thẻ": d["name"],
                "Số thẻ": len(d["cards"]),
                "Thẻ có tiến độ học": sum(1 for c in d["cards"] if c["progress"]),
                "Đã tồn tại": "Có" if d["name"].casefold() in existing else "Chưa",
            }
            for d in parsed
        ]),
        hide_index=True,
    )

    merge_label = "Gộp vào bộ thẻ hiện có (bỏ qua thẻ trùng nội dung)"
    mode = st.radio(
        "Khi trùng tên bộ thẻ", [merge_label, "Luôn tạo bộ thẻ mới"], key=f"import_mode_{s.import_ver}"
    )
    has_progress = any(c["progress"] for d in parsed for c in d["cards"])
    keep_progress = (
        st.checkbox("Giữ tiến độ học có trong file", value=True, key=f"import_keep_{s.import_ver}")
        if has_progress else False
    )

    if st.button("📥 Nhập dữ liệu", type="primary", key="do_import"):
        try:
            result = db.import_decks(parsed, merge=(mode == merge_label), keep_progress=keep_progress)
        except Exception as exc:  # noqa: BLE001 - hiển thị mọi lỗi DB cho người dùng
            st.error(f"Nhập dữ liệu thất bại, chưa có gì bị thay đổi: {exc}")
            return
        s.import_ver += 1  # đổi key để làm trống ô tải file
        flash(
            f"Đã thêm {result['cards_added']} thẻ ({result['decks_created']} bộ mới, "
            f"{result['decks_merged']} bộ gộp), bỏ qua {result['cards_skipped']} thẻ trùng.",
            "📥",
        )
        st.rerun()


# --------------------------------------------------------------------------
# Điều hướng & điểm vào chương trình
# --------------------------------------------------------------------------
PAGES: Dict[str, Callable[[], None]] = {
    PAGE_HOME: page_home,
    PAGE_STUDY: page_study,
    PAGE_MANAGE: page_manage,
    PAGE_STATS: page_stats,
    PAGE_IO: page_io,
}


def render_sidebar() -> None:
    s = st.session_state
    with st.sidebar:
        st.markdown("## 🧠 Flashcard")
        st.radio("Điều hướng", list(PAGES), key="page", label_visibility="collapsed")
        st.divider()
        st.number_input(
            "Thẻ mới tối đa mỗi bộ mỗi ngày", min_value=0, max_value=500, step=5, key="new_limit",
            help="Giới hạn số thẻ chưa học xuất hiện trong mỗi phiên để tránh bị quá tải.",
        )
        current, longest = db.get_streaks()
        st.metric("🔥 Chuỗi ngày học", f"{current} ngày", help=f"Kỷ lục: {longest} ngày")
        due = sum(d["due_cards"] for d in db.get_decks_with_counts(int(s.new_limit)))
        st.caption(f"Đang có {due} thẻ cần ôn hôm nay.")


def main() -> None:
    st.set_page_config(page_title="Flashcard thông minh", page_icon="🧠", layout="wide")
    st.markdown(APP_CSS, unsafe_allow_html=True)
    db.init_db()
    init_state()
    render_sidebar()
    show_flash()
    PAGES[st.session_state.page]()


if __name__ == "__main__":
    main()
