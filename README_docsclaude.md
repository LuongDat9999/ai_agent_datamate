# DataMate – Zalo AI Challenge 2026

> **Talking To Your Data Through Agentic AI**
> Tác nhân AI trả lời câu hỏi tiếng Việt tự nhiên trực tiếp trên dữ liệu có cấu trúc (CSV, Excel, SQLite) của doanh nghiệp.

---

## Mục lục

1. [Tổng quan bài toán](#1-tổng-quan-bài-toán)
2. [Dữ liệu](#2-dữ-liệu)
3. [Thang điểm và quy tắc so khớp](#3-thang-điểm-và-quy-tắc-so-khớp)
4. [Ràng buộc kỹ thuật](#4-ràng-buộc-kỹ-thuật)
5. [Các "bẫy" dữ liệu và cách xử lý](#5-các-bẫy-dữ-liệu-và-cách-xử-lý)
6. [Kiến trúc giải pháp](#6-kiến-trúc-giải-pháp)
7. [Pipeline tiền xử lý dữ liệu](#7-pipeline-tiền-xử-lý-dữ-liệu)
8. [Tool cho agent](#8-tool-cho-agent)
12. [Đánh giá cục bộ](#12-đánh-giá-cục-bộ)
13. [Cấu trúc thư mục đề xuất](#13-cấu-trúc-thư-mục-đề-xuất)
9. [Thiết kế prompt](#9-thiết-kế-prompt)
10. [Chiến lược abstain / unanswerable](#10-chiến-lược-abstain--unanswerable)
11. [Cải tiến đề xuất sau review](#11-cải-tiến-đề-xuất-sau-review)
15. [Checklist trước khi nộp](#15-checklist-trước-khi-nộp)
16. [Rủi ro và lưu ý](#16-rủi-ro-và-lưu-ý)

---

## 1. Tổng quan bài toán

Người dùng (chủ doanh nghiệp) hỏi một câu đơn giản bằng tiếng Việt về dữ liệu kinh doanh của họ. Agent phải:

1. Hiểu câu hỏi (kể cả viết tắt, không dấu, teencode).
2. Tạo truy vấn / mã để lấy dữ liệu từ các file thô của doanh nghiệp.
3. Trả về **đáp án đúng**, hoặc **từ chối** khi dữ liệu không hỗ trợ câu hỏi.

**Input**

| Thành phần | Mô tả |
| --- | --- |
| Bộ dữ liệu doanh nghiệp | Một hoặc nhiều file `.csv`, `.xlsx`, `.sqlite` kèm `README.md` mô tả doanh nghiệp |
| Câu hỏi | Tiếng Việt tự nhiên |
| `as_of_date` | Mốc "ngày hôm nay" để hiểu từ chỉ thời gian tương đối ("tháng này", "tuần rồi", "cùng kỳ năm ngoái") |
| Rulebook và lịch âm–dương | Bộ quy tắc chung (`rulebook.md`) và `calendar.csv` (Tết...) dùng cho toàn bộ bài toán |

**Output**: một dòng JSON cho mỗi câu hỏi.

```json
// Input
{"id": "q_06de33fa21cc", "business_id": "biz_118", "question": "sp bán dc nhiều nhất tuần rồi là gì", "as_of_date": "2026-03-04"}

// Output
{"id": "q_06de33fa21cc", "type": "string", "answer": "Xi măng Hà Tiên PCB40 bao 50kg"}
```

Các `type` hợp lệ: `number`, `percent`, `string`, `date`, `boolean`, `list`, `table`, `unanswerable`, `abstain`.

---

## 2. Dữ liệu

### 2.1 Cấu trúc thư mục

```
<split>/
├── data/<business_id>/
│   ├── *.csv | *.xlsx | *.sqlite     # một hoặc nhiều file dữ liệu
│   └── README.md                     # mô tả doanh nghiệp và ghi chú
├── rulebook.md                       # quy tắc mặc định và từ vựng
├── calendar.csv                      # lịch âm–dương (Tết)
├── questions.jsonl                   # mỗi dòng một câu hỏi
└── answers.jsonl                     # chỉ có ở tập train
```

`business_id` thay đổi theo split (train: `biz_1, biz_2, ...`; public test: `dev_001, dev_002, ...`).

### 2.2 Quy mô các tập

| Tập | Doanh nghiệp | Câu hỏi | Ghi chú |
| --- | --- | --- | --- |
| Train | 30 | ~3.000 | 107 Excel, 20 CSV, 1 SQLite (339 sheet/bảng), **có đáp án** |
| Public test | 14 | 1.000 | 69 Excel, 10 CSV, 1 SQLite (163 sheet/bảng) |
| Private test | 10–20 | ~500 | **Ngành nghề hoàn toàn mới** → bắt buộc tổng quát hóa, không hard-code theo schema |

### 2.3 Các dạng sổ trong public test

| Cơ sở | Số câu | Cấu trúc cần hỗ trợ |
| --- | --- | --- |
| dev_001 | 87 | Sổ chi tiết Excel và danh mục hàng |
| dev_002 | 41 | 17 file bán theo tháng và quy đổi đơn vị |
| dev_003 | 129 | POS, sổ tay, tồn kho và tiền mặt theo ca |
| dev_004 | 96 | Bán, giao, hạn nợ, nhập, tồn, giá |
| dev_005 | 87 | Bán theo tháng, thuốc và lô |
| dev_006 | 111 | SQLite POS với 17 bảng |
| dev_007 | 60 | CSV chi tiết hóa đơn, hàng và nhập |
| dev_008 | 99 | Chi tiết hóa đơn chia hai file Excel |
| dev_009 | 21 | CSV cấp đơn; không có chi tiết mặt hàng |
| dev_010 | 59 | CSV đơn, dòng hàng, sản phẩm, khách, thu tiền |
| dev_011 | 40 | Điện thoại và bảng giải thích ghi tắt |
| dev_012 | 50 | Sửa xe: 14 sheet tháng, danh sách thợ |
| dev_013 | 57 | Mẹ và bé: tiêu đề hai tầng, voucher, điểm |
| dev_014 | 63 | Điện thoại: máy cũ, nợ, góp, bảo hành |

### 2.4 Dữ liệu có giới hạn thông tin (nguồn của `unanswerable`)

| Có trong dữ liệu | Có thể hỏi | Không tự suy ra |
| --- | --- | --- |
| dev_009: tiền hàng mỗi đơn | Tổng doanh thu | Sản phẩm bán chạy |
| dev_012: tổng tiền mỗi phiếu | Doanh thu phiếu sửa | Doanh thu riêng từng việc |
| Snapshot tồn cuối kỳ | Tồn ở ngày chụp | Tồn từng ngày quá khứ |

### 2.5 Đừng đồng nhất các khái niệm

| Khác nhau | Vì sao |
| --- | --- |
| Doanh thu ≠ tiền phải trả ≠ tiền đã thu | Khác cột, ngày ghi nhận và khoản điều chỉnh |
| Số đơn ≠ số dòng hàng | Một đơn có thể có nhiều dòng |
| Bán chạy ≠ được ưa chuộng | Số lượng đơn vị cơ bản khác số đơn chứa sản phẩm |

> Chất lượng `README.md` của từng doanh nghiệp rất không đồng đều: có nơi ghi chi tiết, có nơi chỉ vài dòng, có nơi gần như không có gì. Pipeline không được phụ thuộc vào README.

---

## 3. Thang điểm và quy tắc so khớp

**Penalized Accuracy (PA)**

```
PA = (1/N) · Σ sᵢ      sᵢ = +1 nếu đúng | 0 nếu abstain | -1 nếu sai
```

Không có điểm thành phần. **Sai bị trừ nặng hơn abstain** → chỉ trả lời khi đủ tự tin (xác suất đúng > 50%).

| Type | Nội dung | Quy tắc so khớp |
| --- | --- | --- |
| `number` | Số nguyên/thực | Khớp chính xác; làm tròn 2 chữ số thập phân |
| `percent` | Phần trăm | Làm tròn 2 chữ số thập phân, đúng dấu (vd `-12.45`) |
| `string` | Tên sản phẩm, khách hàng, chi nhánh | NFC, viết thường, thay ký tự đặc biệt bằng khoảng trắng, xóa khoảng trắng thừa |
| `date` | Ngày tháng | `yyyy-mm-dd` hoặc `yyyy-mm` (nếu hỏi theo tháng) |
| `boolean` | Đúng/Sai | `true` / `false` |
| `list` | Danh sách có thứ tự | Khớp từng vị trí theo thứ tự câu hỏi hoặc theo rulebook |
| `table` | Bảng nhỏ | JSON `columns` và `rows`; cột khớp theo tên/thứ tự, hàng theo tập hợp |
| `unanswerable` | Dữ liệu không hỗ trợ | Đúng khi và chỉ khi nhãn gốc cũng là `unanswerable` (**+1 điểm**) |
| `abstain` | Agent chọn không trả lời | Luôn **0 điểm** |

---

## 4. Ràng buộc kỹ thuật

| Hạng mục | Quy định |
| --- | --- |
| Model | Mọi model công khai trước **26/10/2026** có giấy phép phù hợp |
| Tổng tham số | **≤ 15B** cho toàn bộ model chạy ở inference (LLM, embedding, reranker, classifier...), tính cả MoE |
| Quantization | Cho phép tự do |
| Môi trường | **Offline hoàn toàn** trong Docker, không internet, không gọi API LLM thương mại |
| Tự động hóa | Không can thiệp thủ công; **cấm hard-code theo ID câu hỏi/doanh nghiệp** |
| Thực thi mã | Agent được sinh và chạy Python/SQL trong container; thư mục dữ liệu mount **read-only**, file tạm ghi ở nơi khác |
| Phần cứng chấm | 2 × RTX 5090 (32 GB/card), 512 GB RAM |
| Thời gian | **≤ 6 giờ** wall-clock cho toàn bộ Private Test (gồm cả load model) |
| Tái lập | \|PA_nộp − PA_chạy lại\| ≤ **0.03** |

---

## 5. Các "bẫy" dữ liệu và cách xử lý

| Quan sát | Hậu quả | Cách xử lý |
| --- | --- | --- |
| biz_110: 429 dòng "Cộng ngày" / 29.317 dòng | Cộng doanh thu hai lần | Phân loại và đánh cờ dòng tổng trước khi cộng |
| "Ngày 1 thg 6 2025"; serial `45839` | Lọc sai thời gian | Parser theo định dạng và epoch workbook |
| Tiền `19tr990`, `120k`, `20,1tr` | Sai đơn vị tiền | Đổi về số VND; giữ giá trị gốc |
| Tiêu đề hai tầng; ngày trống trong cùng đơn | Sai cột hoặc mất dòng | Nhận diện tiêu đề; forward-fill theo nhóm |
| Một đơn nhiều dòng hàng và nhiều lần thu | Nhân dòng khi JOIN | Aggregate đúng cấp độ trước khi JOIN |
| Cột ngày lẫn định dạng (`1/23/2025`, `1/5`) | Parse sai ngày | Suy luận định dạng **ở mức cột** (xem 7.2) |
| Câu hỏi không dấu, viết tắt, teencode | Không khớp thực thể | Từ điển cố định + gợi ý thực thể bằng fuzzy match + hướng dẫn trong prompt |

---

## 6. Kiến trúc giải pháp

Agent đóng vai **Code Generator và Executor** thay vì để LLM trả lời trực tiếp.

```
 questions.jsonl ─┐
                  ▼
        ┌──────────────────────┐
        │ Tiền xử lý dữ liệu   │  (code xác định, chạy 1 lần / doanh nghiệp)
        │ làm sạch → DuckDB    │
        │ schema profile       │
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────┐
        │ Chuẩn bị ngữ cảnh    │  date_resolver · entity hints ·
        │ cho từng câu hỏi     │  README + luật ngành · few-shot tương tự
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────┐
        │ LLM sinh code × K    │  K candidate (self-consistency)
        │ chạy sandbox         │  retry khi traceback
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────┐
        │ Voting + kiểm tra    │  đồng thuận ≥ ngưỡng → trả lời
        │ unanswerable/abstain │  ngược lại → abstain
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────┐
        │ format_enforcer      │  ép đúng type/định dạng BTC
        └──────────┬───────────┘
                   ▼
              answers.jsonl
```

**Model**: ban đầu dự kiến Qwen2.5-Coder-14B-Instruct (AWQ/GPTQ/GGUF Q8). Sau review nên **benchmark 2–3 ứng viên** trên ~200 câu train (Qwen3-14B, Qwen3-8B và các model ≤15B mới ra) rồi chọn theo PA. Lưu ý model ~14.8B gần như dùng hết ngân sách 15B, không còn chỗ cho embedding/reranker. Chạy bằng vLLM, mỗi GPU một instance.

---

## 7. Pipeline tiền xử lý dữ liệu

Toàn bộ bằng code Python xác định (Pandas, Openpyxl), chạy trước khi nạp DuckDB.

### Bước 1 – Quét và phân loại theo `business_id`

- File dữ liệu thô: `.csv`, `.xlsx`, `.sqlite`. File ngữ cảnh: `README.md`.
- Nhận diện mẫu phân mảnh (nhiều file/sheet theo tháng, vd dev_002 có 17 file, dev_012 có 14 sheet) để gộp bảng.

### Bước 2 – Làm sạch thô

1. **Ô gộp và tiêu đề nối dòng**: unmerge cell, phát hiện dòng tiêu đề (heuristic: dòng đầu tiên có nhiều ô chuỗi, ít trùng), forward-fill các cột nhóm.
   ```python
   df[group_columns] = df[group_columns].ffill()
   ```
2. **Dòng tổng/cộng**: **đánh cờ `is_total`** thay vì xóa; regex phải **neo ở đầu ô** để không xóa nhầm tên như "Cộng Cà Phê".
   ```python
   total_pat = r"^\s*(cộng|tổng cộng|tổng|grand total|tổng ngày|cộng ngày)\b"
   df["is_total"] = df[label_col].astype(str).str.lower().str.contains(total_pat, regex=True)
   ```
   Có thể kiểm chứng thêm: giá trị dòng tổng ≈ tổng các dòng phía trên.
3. **Ngày tháng và Excel serial**: xử lý chuỗi nhiều kiểu, serial Excel (epoch workbook), "Ngày 1 thg 6 2025".
4. **Chuỗi tiền tệ**: `19tr990 → 19.990.000`, `120k → 120.000`, `20,1tr → 20.100.000`. Giữ cả cột gốc.

### 7.2 Quy tắc suy luận định dạng ngày (mức cột)

- Có giá trị mà phần đầu > 12 → cột là **d/m**; phần sau > 12 → cột là **m/d**.
- Giá trị thiếu năm (`1/5`): lấy năm từ tên file/sheet, từ các dòng lân cận (dữ liệu thường tăng dần theo ngày), hoặc từ `as_of_date`.
- Luôn giữ `ngay_raw` và `ngay` (đã parse).

### Bước 3 – Hợp nhất và chống nhân đôi

- Concat các file/sheet theo tháng thành bảng master (vd `sales_master`).
- **Anti-duplicate**: `GROUP BY` đúng cấp độ (đơn hoặc dòng hàng) **trước** khi JOIN, tránh tích Descartes giữa đơn × chi tiết hàng × lịch sử thu tiền.

### Bước 4 – Nạp DuckDB và trích danh mục thực thể

- Nạp bảng đã sạch vào DuckDB in-memory.
- Quét cột tên sản phẩm, khách hàng, chi nhánh thành danh mục cho module fuzzy match (RapidFuzz).
- **Schema profile** (xem mục 11): giá trị distinct của cột ít giá trị, khoảng ngày min/max, bảng "khả năng của dữ liệu".

### Bước 5 – Chuẩn hóa đầu ra (`format_enforcer`)

| Type | Quy tắc | Ví dụ |
| --- | --- | --- |
| string | NFC → lower → thay ký tự đặc biệt bằng khoảng trắng → strip | `" Xi măng Hà Tiên-PCB40 "` → `"xi măng hà tiên pcb40"` |
| number | Ép số; số thực làm tròn 2 | `12500.5678` → `12500.57` |
| percent | Làm tròn 2, giữ dấu (dạng `12.45`, không phải `0.1245`) | `-12.454` → `-12.45` |
| date | ISO `YYYY-MM-DD` hoặc `YYYY-MM` | `2026/03/04` → `"2026-03-04"` |
| boolean | `true` / `false` | `True` → `true` |
| list | Mảng JSON đúng thứ tự yêu cầu | `["sp_b", "sp_a"]` |
| table | DataFrame → `{"columns": [...], "rows": [...]}` | |

### Bước 6 – Rulebook

- Qua đối chiếu `data/train/rulebook.md` và `data/public_test/rulebook.md`, các phần luật chung có cùng ý nghĩa nghiệp vụ; phần thay đổi theo dữ liệu là **mục `## 6. Theo ngành`**.
- Vì code đã xử lý các quy tắc trạng thái, dòng tổng, ngày tháng, đơn vị và kiểm tra SQL, prompt **không đưa nguyên văn toàn bộ rulebook**. Thay vào đó, prompt nhận:
  1. `rulebook_core.md`: phần luật chung còn cần LLM suy luận, đã bỏ các luật do code đảm nhiệm.
  2. Toàn bộ mục `## 6. Theo ngành` của split hiện tại: đây là phần có thể khác giữa ngành/doanh nghiệp và không được suy đoán.
  3. README doanh nghiệp, schema/header, khoảng thời gian đã giải mã và gợi ý thực thể.
- Không được dùng một bản viết tay cố định thay cho mục 6. Luôn đọc mục này từ `rulebook.md` của split đang chạy để tránh làm mất luật riêng của ngành.
- Khi có xung đột, luật ngành trong mục 6 được ưu tiên trong phạm vi nghiệp vụ của ngành; các quy tắc an toàn do code áp đặt (chỉ SELECT, không truy cập raw, kiểm tra type/kết quả) vẫn luôn có hiệu lực.

---

## 8. Tool cho agent

| File | Chức năng |
| --- | --- |
| `date_resolver.py` | Giải mã thời gian tương đối và lịch âm–dương (Tết, mùng 1, rằm...). **Trả về dict các khoảng có tên** (`current`, `previous`, `same_period_last_year`...) thay vì một cặp start/end |
| `entity_matcher.py` | So khớp thực thể, khôi phục tên gốc (bỏ dấu cả hai phía + RapidFuzz). Chạy **trước** LLM để tạo gợi ý, vd `"dc" → "Xi măng Hà Tiên PCB40"` |
| `sandbox_helpers.py` | Hàm tiện ích dùng trong sandbox: `flexible_contains`, `find_best_match`, `clean_currency` |
| `format_enforcer.py` | Chuẩn hóa đầu ra hậu kỳ theo từng `type` |

---

## 12. Đánh giá cục bộ

- Script tính PA theo đúng công thức và quy tắc so khớp ở mục 3.
- **Chia dev theo `business_id`**, không theo câu hỏi, để mô phỏng việc gặp ngành mới.
- Báo cáo PA theo nhóm lỗi: thời gian, thực thể, JOIN/nhân dòng, unanswerable/abstain, định dạng đầu ra.
- Đo thời gian toàn bộ run; mục tiêu **≤ 3 giờ** để còn dư địa so với giới hạn 6 giờ.
- Chạy hai lần liên tiếp, kiểm tra chênh lệch PA < 0.03 (cố định seed, kiểm soát temperature).

Ước lượng thông lượng: ~500 câu × 5 candidate × ~2k token ≈ 5M token, hoàn toàn khả thi với vLLM trên 2 GPU.

---

## 13. Cấu trúc thư mục đề xuất

```
ai_agent_datamate/
├── main.py                      # entrypoint (nối các module)
├── requirements.txt
├── dataprocess/                 # Module 1
│   ├── loader.py
│   ├── cleaner.py
│   ├── profiler.py
│   ├── parse_data.py
│   └── duckdb_manager.py
├── tool/                        # Module 2
│   ├── date_resolver.py
│   ├── entity_matcher.py
│   ├── abbrev.py
│   ├── prompts.py
│   └── fewshot.py
├── llm/                         # Module 3
│   ├── llm_backend.py
│   ├── generator.py
│   └── sql_agent.py
├── sqlrunner/                   # Module 4: kiểm tra và chạy SQL
│   ├── validator.py
│   └── executor.py
├── decision/                    # Module 5: kiểm tra kết quả và quyết định
│   ├── checker.py
│   └── voting.py
├── formatting/                  # Module 6: chuẩn hóa và ghi đầu ra
│   ├── format_enforcer.py
│   └── writer.py
├── eval/
│   └── score.py
├── tests/
└── outputs/                     # kết quả chạy, debug (nên cho vào .gitignore)
```

---

## 9. Thiết kế prompt

Ba template tương ứng ba giai đoạn.

### 9.1 System prompt (cố định)

```text
[VAI TRÒ]
Bạn là chuyên gia phân tích dữ liệu, viết SQL (DuckDB) / Python cho hệ thống DataMate.
Nhiệm vụ: sinh mã để tính câu trả lời chính xác từ dữ liệu doanh nghiệp.

[HÀM TIỆN ÍCH SẴN CÓ TRONG SANDBOX]
- flexible_contains(df, col, search_term)
- find_best_match(query_str, entity_list)
- clean_currency(val)

[LUẬT CHUNG ĐÃ LỌC]
<nội dung từ tool/rulebook_core.md sau khi bỏ các luật do code xử lý>

[LUẬT NGÀNH]
<toàn bộ mục ## 6. Theo ngành được đọc từ rulebook.md của split hiện tại>

[TRẠNG THÁI]
<tên cột, ánh xạ giá trị → trạng thái chuẩn và hướng dẫn dùng _hop_le/_khong_huy>

[QUY ĐỊNH BẢO VỆ ĐIỂM SỐ]
- Dữ liệu KHÔNG có cột/thông tin để trả lời → type = "unanswerable".
- Không chắc chắn về logic tính toán → trả về None để hệ thống ABSTAIN.
```

### 9.2 Dynamic user prompt (theo từng câu hỏi)

```text
[BỐI CẢNH]
- business_id: {business_id}
- as_of_date: {as_of_date}
- Khoảng thời gian đã giải mã: {resolved_periods}

[GHI CHÚ DOANH NGHIỆP]
{readme_notes}  # vd: 1 lốc = 6 chai; 1 bao = 50kg

[SCHEMA DUCKDB + PROFILE]
{schema, sample rows, distinct values, date range, data capabilities}

[GỢI Ý THỰC THỂ]
{entity_hints}

[VÍ DỤ TƯƠNG TỰ ĐÃ XÁC MINH]
{few_shot_examples}

[CÂU HỎI]
"{question}"

[YÊU CẦU ĐẦU RA]
Viết mã trong block ```python ... ```. Gán kết quả vào:
final_result = {"type": <number|percent|string|date|boolean|list|table|unanswerable>, "answer": ...}
```

### 9.3 Retry prompt (khi traceback)

```text
Đoạn mã bạn vừa tạo gặp lỗi khi chạy trong sandbox.

[MÃ ĐÃ SINH]  {failed_code}
[THÔNG BÁO LỖI] {traceback}

Hãy phân tích lỗi (sai tên cột, lỗi ép kiểu, thiếu xử lý NULL...) và sinh lại mã
hoàn chỉnh. Đảm bảo gán kết quả cuối vào final_result.
```

> Nên chọn **một đường chính** để sinh code: khuyến nghị DuckDB SQL (model cỡ 14B sinh SQL ổn định và dễ kiểm tra), Python dùng khi retry.

---

## 10. Chiến lược abstain / unanswerable

Vì sai = −1, abstain = 0, `unanswerable` đúng = +1:

1. **Self-consistency voting**: sinh K = 5 candidate, chạy hết; nếu ≥ 3/5 cho cùng kết quả (sau chuẩn hóa) thì trả lời, ngược lại abstain. Chọn ngưỡng bằng dữ liệu train.
2. **Phân biệt `unanswerable` và `abstain`**: `unanswerable` là nhãn đúng riêng; đo tỉ lệ của nó ở train để biết đáng bao nhiêu điểm.
3. **Kiểm tra khả năng dữ liệu một cách xác định** (có bảng chi tiết mặt hàng? có giá vốn? có tồn theo ngày hay chỉ snapshot?) và đưa vào prompt, thay vì để LLM đoán.
4. **Timeout sandbox** → abstain.

---

## 11. Cải tiến đề xuất sau review

Xếp theo mức ảnh hưởng đến điểm:

| # | Cải tiến | Lý do |
| --- | --- | --- |
| 1 | **Eval loop** trên train (chia theo doanh nghiệp) | Không đo được thì không cải thiện được; mô phỏng private test ngành mới |
| 2 | **Voting + ngưỡng abstain** | Tối ưu trực tiếp công thức PA, giảm dao động giữa các lần chạy |
| 3 | **Few-shot từ code đã xác minh** (TF-IDF/BM25, 0 tham số) | Tăng chính xác rẻ, không cần fine-tune |
| 4 | **Benchmark model** (Qwen3-14B/8B, model mới ≤15B) | Qwen2.5-Coder yếu tiếng Việt/teencode; chú ý ngân sách 15B |
| 5 | **Schema profile** (distinct values, date range, data capabilities) | Giúp lọc đúng trạng thái đơn và nhận diện unanswerable |
| 6 | **LLM gán `type` cùng đáp án** | `format_enforcer` cần biết type; quy ước percent rõ ràng |
| 7 | **Regex dòng tổng neo đầu ô, đánh cờ thay vì xóa** | Tránh xóa nhầm dữ liệu thật |
| 8 | **Entity hints chạy trước LLM** | Xác định, ít lỗi hơn để LLM tự gọi |
| 9 | **`date_resolver` trả nhiều khoảng có tên + unit test** | Câu so sánh kỳ cần ≥ 2 khoảng; lỗi ở đây lan ra mọi câu có thời gian |
| 10 | **Suy luận định dạng ngày ở mức cột** | Giải quyết cột ngày lẫn d/m và m/d, thiếu năm |
| 11 | **Docker offline test sớm** | RTX 5090 cần CUDA 12.8+ và PyTorch/vLLM đủ mới |

---

## 15. Checklist trước khi nộp

- [ ] Chạy hoàn toàn offline trong Docker (weights, thư viện đã nằm trong image)
- [ ] Tổng tham số mọi model inference ≤ 15B
- [ ] Không hard-code theo ID câu hỏi hoặc `business_id`
- [ ] Thư mục dữ liệu mount read-only; file tạm ghi ra `/tmp`
- [ ] Sandbox có timeout; lỗi hoặc timeout → abstain
- [ ] Prompt không đưa nguyên văn toàn bộ rulebook; giữ `rulebook_core.md` đã lọc và toàn bộ mục `## 6. Theo ngành`
- [ ] Đã đối chiếu luật chung giữa train/public test; luật ngành luôn đọc từ split đang chạy
- [ ] Toàn bộ run private test ≤ 6 giờ (gồm load model), có dư địa
- [ ] Hai lần chạy liên tiếp lệch PA < 0.03
- [ ] Output đúng schema JSON từng dòng cho mọi `type`
- [ ] Percent ở dạng `12.45`; date ở dạng ISO; string đã chuẩn hóa

---

## 16. Rủi ro và lưu ý

- **Tổng quát hóa sang ngành mới**: tránh quy tắc gắn với tên cột/ngành cụ thể; ưu tiên suy luận từ schema profile.
- **Dòng tổng bị xóa nhầm hoặc sót** gây sai SUM; luôn đánh cờ và kiểm chứng.
- **Nhân dòng khi JOIN** là nguồn lỗi số liệu phổ biến nhất; aggregate trước khi JOIN.
- **Phân biệt khái niệm** (doanh thu ≠ đã thu, đơn ≠ dòng hàng, bán chạy ≠ nhiều đơn) phải nằm trong rulebook/prompt.
- **Dao động giữa các lần chạy**: cố định seed, dùng voting, tránh temperature cao.
- **Tương thích GPU**: RTX 5090 cần CUDA/PyTorch/vLLM đủ mới, hãy test image sớm.
- **Ví dụ trong prompt chỉ là minh họa**: luôn đối chiếu với `rulebook.md` thật (vd trạng thái "Đã hủy"/"Trả hàng").
