# DataMate – Zalo AI Challenge 2026

## 1. Bối cảnh

Bảng tính và cơ sở dữ liệu đơn giản là hệ thống dữ liệu được dùng phổ biến nhất thế giới. Chủ cửa hàng lưu bán hàng và tồn kho bằng Excel; giáo viên lưu điểm số và điểm danh; phòng khám lưu lượt khám và lô thuốc; cán bộ nhân sự lưu bảng chấm công; hợp tác xã lưu năng suất và chi phí đầu vào; UBND xã lưu thống kê hộ dân; nhà báo tải dữ liệu mở từ Tổng cục Thống kê; nhân viên công ty lớn cần một con số từ kho dữ liệu mà không muốn chờ bộ phận dữ liệu.

Tất cả đều có dữ liệu, nhưng gần như không ai viết được truy vấn. Họ muốn hỏi bằng tiếng Việt thường ngày, ví dụ:

- "Tháng này bán chạy nhất mặt hàng gì?"
- "Tuần rồi bán được bao nhiêu ly trà sữa?"
- "Còn ai nợ tiền chưa trả?"
- "Thuốc nào sắp hết hạn trong kho?"

và nhận được câu trả lời đúng.

## 2. Nhiệm vụ

Xây dựng một **AI agent** trả lời câu hỏi tiếng Việt tự nhiên trực tiếp trên dữ liệu có cấu trúc (CSV, Excel, SQLite). Với các tệp dữ liệu gốc và một câu hỏi, agent phải:

- trả về câu trả lời **đúng**, hoặc
- **từ chối trả lời** khi dữ liệu không đủ để trả lời.

Dữ liệu cuộc thi xoay quanh các doanh nghiệp nhỏ của Việt Nam (nhóm người dùng bảng tính lớn nhất, với dữ liệu thực tế phong phú và lộn xộn nhất), nhưng năng lực cần xây dựng mang tính tổng quát: hỏi đáp ngôn ngữ tự nhiên trên dữ liệu dạng bảng.

## 3. Đầu vào

- **Gói dữ liệu** của một doanh nghiệp: một hoặc nhiều tệp (`.csv`, `.xlsx`, `.sqlite`) kèm `README.md` mô tả ngắn về doanh nghiệp.
- **Câu hỏi** bằng tiếng Việt đời thường.
- **Ngày tham chiếu (as-of date)**: "hôm nay" dùng để quy đổi các cách nói thời gian tương đối ("tháng này", "tuần rồi", "cùng kỳ năm ngoái").
- **Rulebook** và **lịch âm – dương**, dùng chung cho mọi doanh nghiệp trong cùng một tập (xem phần Dữ liệu).

## 4. Đầu ra

Một câu trả lời thuộc một trong các kiểu ở mục 6: giá trị (number, percent, string, date, boolean, list, table), `unanswerable` khi dữ liệu không đủ để trả lời, hoặc `abstain` khi agent chọn không trả lời.

## 5. Ví dụ

Đầu vào – một dòng của `questions.jsonl`:

```json
{"id": "q_06de33fa21cc", "business_id": "biz_118", "question": "sp bán dc nhiều nhất tuần rồi là gì", "as_of_date": "2026-03-04"}
```

Đầu ra – một dòng của `submission.jsonl`:

```json
{"id": "q_06de33fa21cc", "type": "string", "answer": "Xi măng Hà Tiên PCB40 bao 50kg"}
```

## 6. Đánh giá

### 6.1. Chấm điểm từng câu

Mỗi câu trả lời được chấm là **đúng**, **sai** hoặc **bỏ qua (abstain)**.

- Câu trả lời kiểu `abstain` (hoặc thiếu câu trả lời) được tính là abstain.
- Mọi câu trả lời khác chỉ đúng khi **kiểu trùng với kiểu của đáp án chuẩn** và **giá trị khớp** theo quy tắc dưới đây; nếu không là sai.
- Không có điểm từng phần.

| Kiểu | Nội dung trả lời | Ví dụ | Quy tắc khớp |
|---|---|---|---|
| `number` | Giá trị số (tiền VND, số đếm, số lượng) | `48250000` | Khớp chính xác. Giá trị chuẩn là số nguyên (tiền VND, số đếm, số lượng) hoặc làm tròn 2 chữ số thập phân. |
| `percent` | Giá trị phần trăm, đơn vị là % | `-12.45` | Khớp chính xác sau khi làm tròn 2 chữ số thập phân; **dấu phải đúng**. |
| `string` | Tên (sản phẩm, khách hàng, chi nhánh, thứ trong tuần, …) | `"Sting dâu 330ml"` | Cả câu trả lời và từng dạng được chấp nhận của đáp án chuẩn đều được chuẩn hóa rồi so sánh bằng nhau tuyệt đối. Chuẩn hóa: (1) Unicode NFC; (2) chuyển chữ thường; (3) mọi ký tự không phải chữ, chữ số hoặc khoảng trắng được thay bằng dấu cách; (4) cắt khoảng trắng đầu/cuối và gộp các chuỗi khoảng trắng thành một dấu cách. **Không bỏ dấu tiếng Việt.** Mỗi đáp án chuẩn liệt kê các dạng chấp nhận: mọi cách viết của tên đó xuất hiện trong dữ liệu của doanh nghiệp, cộng với mã của nó, sau chuẩn hóa, ví dụ `sting dâu 330ml`, `sp0042`. |
| `date` | Ngày theo chuẩn ISO | `"2026-08-15"`, `"2025-09"` | Khớp chính xác. `yyyy-mm-dd`; câu hỏi về tháng dùng `yyyy-mm` (ví dụ `"2025-09"`). |
| `boolean` | `true` / `false` | `true` | Khớp chính xác. |
| `list` | Danh sách có thứ tự các tên hoặc số, theo thứ tự câu hỏi yêu cầu (hoặc mặc định của rulebook) | `["Cà phê sữa", "Bạc xỉu", "Trà đào"]` | Luôn có thứ tự, khớp theo vị trí. Thứ tự là thứ tự câu hỏi yêu cầu, hoặc mặc định của rulebook nếu câu hỏi không nêu (xem "Câu hỏi xếp hạng"). Từng phần tử khớp theo quy tắc `string` hoặc `number`. |
| `table` | Bảng nhỏ: tên cột và các dòng | `{"columns": [...], "rows": [[...], ...]}` | Cột khớp theo tên đã chuẩn hóa; nếu tên không khớp thì khớp theo vị trí. Các dòng khớp như một **tập hợp**; ô tên theo quy tắc `string`, ô số theo quy tắc `number`. |
| `unanswerable` | Dữ liệu không chứa thông tin cần thiết để trả lời | — | Đúng khi và chỉ khi đáp án chuẩn là unanswerable. |
| `abstain` | Agent chọn không trả lời | — | Được tính là abstain bất kể đáp án chuẩn. |

### 6.2. Câu hỏi xếp hạng

- Xếp hạng dùng tiêu chí nêu trong câu hỏi, hoặc **mặc định của rulebook** nếu câu hỏi không nêu, theo thứ tự **giảm dần** của tiêu chí, chỉ tính trên các giá trị hợp lệ.
- Các phần tử bằng giá trị được sắp theo **mã, tăng dần**.
- Các câu có đồng hạng tại ranh giới top-k (hoặc ở hạng 1 với câu "… nhất") bị loại khỏi tập kiểm tra.
- Nếu có ít hơn k ứng viên, đáp án chuẩn là toàn bộ ứng viên.

### 6.3. Điểm số – Penalized Accuracy (PA)

Chỉ số xếp hạng trên bảng điểm là PA trên N câu hỏi của một tập kiểm tra:

$$
PA = \frac{1}{N}\sum_{i=1}^{N} s_i,\qquad
s_i = \begin{cases} 1 & \text{câu } i \text{ đúng}\\ 0 & \text{câu } i \text{ là abstain}\\ -1 & \text{câu } i \text{ sai}\end{cases}
$$

- Mọi câu trả lời cho câu hỏi `unanswerable` (khác chính `unanswerable`) đều tính là **sai**.
- Mọi câu có trọng số như nhau.
- Đồng điểm: xét **tổng thời gian suy luận** khi BTC chạy lại, sau đó xét **thời điểm nộp sớm hơn**.

> Hệ quả chiến lược: trả lời sai bị phạt −1, còn bỏ qua chỉ 0. Chỉ nên trả lời khi đủ tự tin; với câu dữ liệu không hỗ trợ, nên trả `unanswerable` thay vì đoán.
