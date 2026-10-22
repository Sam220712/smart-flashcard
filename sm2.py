"""
sm2.py - Thuật toán lặp lại ngắt quãng SM-2 (SuperMemo 2).

Module này KHÔNG phụ thuộc Streamlit hay SQLite: chỉ chứa các hàm thuần túy
(pure functions) nên rất dễ kiểm thử.

Tóm tắt thuật toán SM-2
-----------------------
Mỗi thẻ có 3 thông số:
    * ease_factor (EF)  - hệ số dễ, mặc định 2.5, tối thiểu 1.3.
    * repetitions (n)   - số lần nhớ đúng liên tiếp.
    * interval (I)      - số ngày chờ đến lần ôn kế tiếp.

Sau mỗi lần ôn, người học chấm "chất lượng nhớ" q (0..5). Ứng dụng này chỉ
có 4 nút bấm nên ta ánh xạ sang thang q của SM-2 như sau:

    Nút (grade)   Ý nghĩa   q của SM-2
    -----------   --------  ----------
    0  Quên       Không nhớ      1   (q < 3: thất bại)
    1  Khó        Nhớ vất vả     3   (đạt, nhưng khó)
    2  Tốt        Nhớ đúng       4
    3  Dễ         Nhớ ngay       5

Quy tắc cập nhật:
    * Nếu q < 3 (Quên): n = 0, I = 1 ngày (học lại từ đầu).
    * Nếu q >= 3:
        n == 0  ->  I = 1
        n == 1  ->  I = 6
        n >= 2  ->  I = round(I_cũ * EF_cũ)
        sau đó n = n + 1
    * EF mới = EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)),
      và luôn được chặn dưới ở 1.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, Optional

# --------------------------------------------------------------------------
# Hằng số
# --------------------------------------------------------------------------
MIN_EASE: float = 1.3          # EF không bao giờ thấp hơn mức này
DEFAULT_EASE: float = 2.5      # EF khởi tạo cho thẻ mới
MATURE_INTERVAL_DAYS: int = 21  # Thẻ có interval >= 21 ngày được coi là "đã nhớ"

# Bốn mức đánh giá hiển thị trên giao diện (khớp yêu cầu: 0, 1, 2, 3).
GRADE_FORGOT: int = 0
GRADE_HARD: int = 1
GRADE_GOOD: int = 2
GRADE_EASY: int = 3

GRADE_LABELS: Dict[int, str] = {
    GRADE_FORGOT: "Quên",
    GRADE_HARD: "Khó",
    GRADE_GOOD: "Tốt",
    GRADE_EASY: "Dễ",
}

# Ánh xạ nút bấm (0..3) sang thang chất lượng q (0..5) của SM-2.
GRADE_TO_QUALITY: Dict[int, int] = {
    GRADE_FORGOT: 1,
    GRADE_HARD: 3,
    GRADE_GOOD: 4,
    GRADE_EASY: 5,
}


# --------------------------------------------------------------------------
# Kiểu dữ liệu
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SM2State:
    """Trạng thái lập lịch hiện tại của một thẻ."""

    ease_factor: float = DEFAULT_EASE
    repetitions: int = 0
    interval: int = 0  # số ngày


@dataclass(frozen=True)
class SM2Result:
    """Kết quả sau khi áp dụng một lần đánh giá."""

    ease_factor: float
    repetitions: int
    interval: int
    due_date: date
    lapsed: bool  # True nếu người học bấm "Quên"


# --------------------------------------------------------------------------
# Các hàm tính toán
# --------------------------------------------------------------------------
def update_ease_factor(ease_factor: float, quality: int) -> float:
    """Tính hệ số dễ (EF) mới theo công thức gốc của SM-2.

    EF' = EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))

    Ví dụ với EF = 2.5:
        q = 5 (Dễ)   -> +0.10  => 2.60
        q = 4 (Tốt)  ->  0.00  => 2.50
        q = 3 (Khó)  -> -0.14  => 2.36
        q = 1 (Quên) -> -0.54  => 1.96
    """
    gap = 5 - quality
    delta = 0.1 - gap * (0.08 + gap * 0.02)
    return max(MIN_EASE, round(ease_factor + delta, 4))


def next_interval(repetitions: int, interval: int, ease_factor: float) -> int:
    """Tính số ngày đến lần ôn kế tiếp khi người học trả lời ĐÚNG (q >= 3).

    Lưu ý: dùng EF *trước khi* cập nhật, đúng như mô tả của SM-2.
    Dùng int(x + 0.5) thay cho round() để tránh làm tròn kiểu "banker's"
    (round(2.5) == 2 trong Python).
    """
    if repetitions == 0:
        return 1
    if repetitions == 1:
        return 6
    return max(1, int(interval * ease_factor + 0.5))


def schedule(
    grade: int,
    state: SM2State,
    today: Optional[date] = None,
) -> SM2Result:
    """Áp dụng một lần đánh giá lên trạng thái thẻ và trả về lịch mới.

    Args:
        grade: 0 (Quên), 1 (Khó), 2 (Tốt) hoặc 3 (Dễ).
        state: trạng thái hiện tại của thẻ.
        today: ngày ôn tập (mặc định là hôm nay) - tiện cho việc kiểm thử.

    Returns:
        SM2Result chứa EF, số lần lặp, interval và ngày đến hạn mới.
    """
    if grade not in GRADE_TO_QUALITY:
        raise ValueError(f"grade phải thuộc {sorted(GRADE_TO_QUALITY)}, nhận {grade!r}")

    today = today or date.today()
    quality = GRADE_TO_QUALITY[grade]
    new_ease = update_ease_factor(state.ease_factor, quality)

    if quality < 3:
        # Thất bại: bắt đầu lại chuỗi lặp, hẹn ôn lại vào ngày mai.
        repetitions, interval, lapsed = 0, 1, True
    else:
        interval = next_interval(state.repetitions, state.interval, state.ease_factor)
        repetitions, lapsed = state.repetitions + 1, False

    return SM2Result(
        ease_factor=new_ease,
        repetitions=repetitions,
        interval=interval,
        due_date=today + timedelta(days=interval),
        lapsed=lapsed,
    )


def preview_intervals(state: SM2State) -> Dict[int, int]:
    """Dự đoán interval (ngày) cho từng nút bấm - hiển thị dưới nút đánh giá."""
    return {grade: schedule(grade, state).interval for grade in GRADE_TO_QUALITY}


def format_interval(days: int) -> str:
    """Định dạng số ngày thành chuỗi dễ đọc: '6 ngày', '2.5 tháng', '1.2 năm'."""
    if days < 30:
        return f"{days} ngày"
    if days < 365:
        value, unit = days / 30, "tháng"
    else:
        value, unit = days / 365, "năm"
    return f"{value:.1f}".rstrip("0").rstrip(".") + f" {unit}"
