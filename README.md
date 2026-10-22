# 🧠 Flashcard thông minh

Ứng dụng web học flashcard viết bằng **Streamlit** + **SQLite**, dùng thuật toán lặp lại ngắt quãng **SM-2** để hẹn đúng ngày ôn lại từng thẻ. Hỗ trợ **Markdown** và **công thức LaTeX**.

## Cấu trúc thư mục

```
flashcards/
├── app.py              # Giao diện Streamlit (chạy file này)
├── database.py         # Tầng SQLite: CRUD, hàng đợi ôn tập, thống kê, nhập/xuất
├── sm2.py              # Thuật toán SM-2 (hàm thuần túy, dễ kiểm thử)
├── requirements.txt    # Thư viện cần cài
├── .streamlit/
│   └── config.toml     # Màu chủ đạo (tuỳ chọn)
└── flashcards.db       # Tự tạo ở lần chạy đầu tiên
```

## Cài đặt và chạy từng bước

Yêu cầu: **Python 3.9 trở lên**.

**Bước 1. Mở terminal trong thư mục dự án**

```bash
cd flashcards
```

**Bước 2. Tạo môi trường ảo (khuyến nghị)**

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Bước 3. Cài thư viện**

```bash
pip install -r requirements.txt
```

**Bước 4. Chạy ứng dụng**

```bash
streamlit run app.py
```

Trình duyệt sẽ tự mở tại `http://localhost:8501`. Nếu không, hãy mở địa chỉ đó thủ công. Muốn đổi cổng: `streamlit run app.py --server.port 8600`.

**Bước 5. Dùng thử nhanh**

1. Ở trang **Tổng quan**, bấm **Tạo bộ thẻ mẫu** để có sẵn 7 thẻ (Markdown, LaTeX, code).
2. Bấm **Học ngay** để vào phiên ôn tập.
3. Hoặc vào **Quản lý**, tạo bộ thẻ riêng ở tab *Bộ thẻ*, thêm thẻ ở tab *Flashcard*.

## Cách học

| Thao tác | Phím tắt |
|---|---|
| Lật thẻ | `Space` hoặc `Enter` |
| Quên | `1` |
| Khó | `2` |
| Tốt | `3` (hoặc `Space` / `Enter` sau khi đã lật thẻ) |
| Dễ | `4` |

Dưới mỗi nút đánh giá có dòng dự đoán khi nào thẻ quay lại (ví dụ *sau 6 ngày*). Khi bấm **Quên**, thẻ được xếp lại cuối phiên để học lại ngay; lượt học lại không làm thay đổi lịch SM-2 lần thứ hai.

Mỗi bộ thẻ chỉ đưa vào phiên học tối đa số thẻ mới đặt ở thanh bên (mặc định 20 thẻ mỗi ngày), tránh bị quá tải khi vừa nhập nhiều thẻ.

## Thuật toán SM-2 trong ứng dụng

Mỗi thẻ lưu ba thông số: hệ số dễ **EF** (mặc định 2.5, tối thiểu 1.3), số lần nhớ đúng liên tiếp **n** và khoảng cách ôn **I** (ngày).

| Nút | Mức q của SM-2 | Kết quả |
|---|---|---|
| Quên (0) | 1 | n = 0, I = 1 ngày, EF giảm 0.54 |
| Khó (1) | 3 | tiếp tục chuỗi, EF giảm 0.14 |
| Tốt (2) | 4 | tiếp tục chuỗi, EF giữ nguyên |
| Dễ (3) | 5 | tiếp tục chuỗi, EF tăng 0.10 |

Khi nhớ đúng (Khó, Tốt, Dễ): lần 1 hẹn 1 ngày, lần 2 hẹn 6 ngày, từ lần 3 trở đi `I = làm tròn(I cũ × EF)`, sau đó n tăng 1.

Công thức cập nhật EF: `EF' = EF + (0.1 − (5 − q) × (0.08 + (5 − q) × 0.02))`, chặn dưới 1.3. Toàn bộ mã nằm trong `sm2.py` kèm chú thích; muốn đổi cách ánh xạ nút sang mức q, sửa `GRADE_TO_QUALITY`.

## Markdown và LaTeX trong thẻ

````markdown
**Chữ đậm**, *chữ nghiêng*, danh sách `- mục`, bảng, khối code...

Công thức cùng dòng: $E = mc^2$

Công thức riêng một dòng:
$$x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}$$

Hóa học: $\mathrm{2H_2 + O_2 \rightarrow 2H_2O}$
````

Mẹo: muốn gõ ký tự đô la bình thường (ví dụ giá tiền) hãy viết `\$`. Xuống dòng đơn trong ô nhập được giữ nguyên khi hiển thị.

## Nhập và xuất dữ liệu

**Xuất**: chọn một hoặc nhiều bộ thẻ, tải về JSON hoặc CSV. Có thể bỏ chọn "Kèm tiến độ học" khi chỉ muốn chia sẻ nội dung thẻ.

**Nhập**: tải lên file JSON (xuất từ ứng dụng) hoặc CSV. Ứng dụng hiển thị bản xem trước, rồi cho chọn gộp vào bộ thẻ trùng tên (bỏ qua thẻ trùng nội dung) hoặc luôn tạo bộ thẻ mới.

Định dạng CSV tối thiểu (lưu UTF-8, dấu phân cách `,` hoặc `;`):

```csv
deck,front,back
Từ vựng tiếng Anh,**abandon** (v),"từ bỏ, bỏ rơi"
Vật lý,Công thức động năng?,$E_k = \frac{1}{2}mv^2$
```

Cột `deck` có thể bỏ trống hoặc không có; khi đó thẻ vào bộ thẻ mang tên bạn nhập ở bước nhập. Trong trang **Nhập / Xuất** có nút tải file CSV mẫu.

## Dữ liệu và sao lưu

Toàn bộ dữ liệu nằm trong một file `flashcards.db` cạnh `app.py`. Để sao lưu, chỉ cần copy file này. Muốn đặt file ở nơi khác, đặt biến môi trường trước khi chạy:

```bash
# macOS / Linux
FLASHCARD_DB=/duong/dan/flashcards.db streamlit run app.py
# Windows (PowerShell)
$env:FLASHCARD_DB="D:\data\flashcards.db"; streamlit run app.py
```

Xóa thẻ hoặc bộ thẻ không xóa lịch sử học, nên chuỗi ngày học (streak) và biểu đồ vẫn được giữ.

## Xử lý sự cố

- **Phím tắt không hoạt động**: bấm vào vùng trống của trang rồi thử lại (phím tắt bị bỏ qua khi con trỏ đang ở ô nhập chữ). Các nút bấm vẫn dùng bình thường.
- **Giao diện thẻ không có dòng kẻ hoặc màu**: cần Streamlit từ 1.45 trở lên, hãy chạy `pip install -U streamlit`.
- **Công thức hiển thị sai**: kiểm tra đã đóng đủ cặp `$...$` hoặc `$$...$$`; ký tự `$` đơn lẻ cần viết `\$`.
- **Font chữ Be Vietnam Pro không tải được** (máy offline): ứng dụng tự dùng font hệ thống thay thế.
