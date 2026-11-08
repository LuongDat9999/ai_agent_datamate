# DataMate – AI hỏi đáp dữ liệu doanh nghiệp

## 1. Ý tưởng dự án

DataMate cho phép người dùng đặt câu hỏi tiếng Việt tự nhiên trên dữ liệu doanh nghiệp
(`CSV`, `Excel`, `SQLite`). Thay vì để LLM tự đoán câu trả lời, hệ thống dùng LLM để
sinh truy vấn, chạy truy vấn trên dữ liệu thật, kiểm tra kết quả rồi mới xuất JSON.
Cách này giúp câu trả lời có thể kiểm tra và giảm lỗi tính toán.

## 2. Dữ liệu đầu vào và đầu ra

- Mỗi split có `questions.jsonl`, `rulebook.md`, `calendar.csv` và thư mục
  `data/<business_id>/`.
- Câu hỏi có `id`, `business_id`, nội dung câu hỏi và `as_of_date`.
- Kết quả được ghi một dòng JSON cho mỗi câu hỏi vào `answers.jsonl` (hoặc đường dẫn
  truyền qua `--out`).
- Các kiểu kết quả chính: `number`, `percent`, `string`, `date`, `boolean`, `list`,
  `table`, `unanswerable`, `abstain`.

## 3. Tiền xử lý dữ liệu làm được gì?

Module `dataprocess` quét dữ liệu theo doanh nghiệp và:

1. Đọc CSV, Excel nhiều sheet và SQLite.
2. Chuẩn hóa tên bảng/cột, kết nối các bảng vào DuckDB in-memory.
3. Phát hiện cột trạng thái đơn hàng và chuẩn hóa một số trạng thái.
4. Tạo schema profile: cột, kiểu dữ liệu, mẫu dữ liệu, khoảng ngày và khả năng dữ liệu
   (ví dụ có/không có chi tiết sản phẩm).
5. Tạo danh mục thực thể để hỗ trợ tìm sản phẩm, khách hàng, chi nhánh.
6. Giữ ngữ cảnh dữ liệu theo doanh nghiệp để tái sử dụng cho nhiều câu hỏi.

Các quy tắc làm sạch quan trọng gồm xử lý ngày tháng, tiền tệ, tiêu đề nhiều tầng,
forward-fill và tránh nhân dòng khi JOIN. Đây là phần cần tiếp tục kiểm chứng trên
từng loại sổ thực tế.

## 4. Xử lý câu hỏi làm được gì?

Với mỗi câu hỏi, hệ thống:

1. Giải mã thời gian tương đối dựa trên `as_of_date` và lịch âm–dương.
2. Tìm gợi ý thực thể gần đúng, hỗ trợ câu hỏi viết tắt/không dấu.
3. Chọn few-shot tương tự nếu có ngân hàng ví dụ.
4. Tạo prompt gồm luật chung đã lọc, toàn bộ mục `## 6. Theo ngành`, ghi chú doanh
   nghiệp, schema và capability profile.
5. Gọi backend LLM để sinh candidate truy vấn và kiểu câu trả lời.
6. Kiểm tra SQL tĩnh/ngữ nghĩa, chạy trong DuckDB và retry tối đa hai lần khi lỗi.
7. Kiểm tra khả năng trả lời, loại bỏ candidate không an toàn hoặc thiếu bộ lọc ngày.
8. Chọn kết quả hoặc `abstain` nếu không đủ tin cậy.
9. Chuẩn hóa kết quả về schema JSON thống nhất.

## 5. Ý tưởng xử lý rulebook

- Khi đối chiếu `data/train/rulebook.md` và `data/public_test/rulebook.md`, các luật
  chung có cùng ý nghĩa nghiệp vụ; phần thay đổi theo dữ liệu/ngành nằm ở mục
  `## 6. Theo ngành`.
- Vì code đã xử lý trạng thái đơn, dòng tổng, ngày tháng, quy đổi cơ bản và kiểm tra
  SQL, không đưa nguyên văn toàn bộ `rulebook.md` vào prompt. Làm vậy vừa tốn token,
  vừa lặp lại các quy tắc mà code đã thực thi.
- Prompt nhận ba lớp luật:
  1. `tool/rulebook_core.md`: luật chung còn cần LLM suy luận, sau khi bỏ các luật
     do code đảm nhiệm.
  2. Toàn bộ mục `## 6. Theo ngành` đọc từ `rulebook.md` của split đang chạy. Mục
     này bắt buộc giữ nguyên vì có thể chứa quy tắc riêng của ngành.
  3. README doanh nghiệp và schema/header để giải thích ý nghĩa cột, đơn vị và
     quan hệ bảng cụ thể.
- Không dùng một bản viết tay cố định thay cho mục 6. Nếu luật ngành khác luật
  chung, ưu tiên luật ngành trong phạm vi nghiệp vụ của ngành đó. Các ràng buộc an
  toàn do code áp đặt (chỉ SELECT, không truy cập bảng raw, kiểm tra type/kết quả)
  vẫn luôn có hiệu lực.
- Rulebook cần mô tả rõ các khái niệm dễ nhầm: doanh thu/đã thu, số đơn/số dòng hàng,
  bán chạy/số đơn chứa sản phẩm, quy đổi đơn vị và điều kiện `unanswerable`.
- Không nên hard-code theo `business_id` hoặc `question_id`.

## 6. Các tool đã viết

| File | Vai trò |
|---|---|
| `tool/date_resolver.py` | Giải mã ngày, kỳ tương đối và một số mốc âm lịch. |
| `tool/entity_matcher.py` | Fuzzy match thực thể và tạo entity hint cho prompt. |
| `tool/abbrev.py` | Từ điển viết tắt/chuẩn hóa cách gọi. |
| `tool/fewshot.py` | Nạp và truy hồi ví dụ câu hỏi tương tự. |
| `tool/prompts.py` | Xây prompt chính, prompt retry và giảm kích thước prompt. |
| `tool/token_budget.py` | Hỗ trợ kiểm soát ngân sách token. |
| `sqlrunner/validator.py` | Kiểm tra, lint, chạy SQL và tạo gợi ý retry. |
| `sqlrunner/executor.py` | Tiện ích thực thi mã/truy vấn. |
| `decision/checker.py` | Kiểm tra capability, candidate và quyết định kết quả. |
| `decision/voting.py` | Thành phần hỗ trợ voting/kết hợp kết quả. |
| `formatting/format_enforcer.py` | Ép kiểu và chuẩn hóa output. |

## 7. Flow thực hiện (`main.py`)

```text
Đọc tham số
  → chọn split và tối đa 100 câu hỏi
  → đọc rulebook, calendar, few-shot bank
  → load_business() và cache dữ liệu theo business_id
  → build_prompt() cho từng câu hỏi
  → generate_candidates()
  → validate_with_retry() (tối đa 2 retry)
  → decide() / abstain khi không đủ điều kiện
  → to_answer()
  → ghi outputs/answers.jsonl và outputs/debug/debug.jsonl
```

Lệnh mẫu:

```bash
cd ai_agent_datamate
python main.py --split_dir data/train --limit 100 \
  --out outputs/answers.jsonl --debug_dir outputs/debug
```

Backend mặc định lấy từ `LLM_BACKEND` (mặc định `hf`); có thể chọn `mock`, `hf` hoặc
`vllm`. `--sample_mode diverse` dùng để lấy mẫu theo nhiều doanh nghiệp.

## 8. Cấu trúc thư mục

```text
ai_agent_datamate/
├── main.py                  # Entrypoint, điều phối toàn bộ pipeline
├── requirements.txt         # Thư viện Python
├── README.md                # Tài liệu tóm tắt dự án
├── README_docsclaude.md     # Tài liệu thiết kế và định hướng chi tiết
├── data/
│   ├── train/               # Câu hỏi, đáp án và dữ liệu huấn luyện
│   └── public_test/         # Split public test, cấu trúc tương tự train
├── dataprocess/             # Đọc, làm sạch, profile và nạp dữ liệu
├── tool/                    # Resolver, matcher, prompt và few-shot
├── llm/                     # Backend và sinh candidate từ LLM
├── sqlrunner/               # Validate và thực thi truy vấn
├── decision/                # Kiểm tra và quyết định candidate
├── formatting/              # Chuẩn hóa và ghi output
├── eval/                    # Script kiểm tra entity, thời gian, prompt và score
├── tests/                   # Unit test hiện có
└── outputs/                 # Kết quả/debug sinh ra khi chạy, không phải mã nguồn
```

## 9. Ý nghĩa output và cách đọc bug

Ví dụ output:

```json
{"id":"q_001","type":"number","answer":1250000}
```

- `id`: liên kết output với câu hỏi gốc.
- `type`: kiểu dữ liệu và quy tắc chấm/chuẩn hóa được áp dụng.
- `answer`: đáp án cuối cùng sau khi truy vấn và format.
- `unanswerable`: dữ liệu không chứa đủ thông tin để trả lời.
- `abstain`: hệ thống không đủ tin cậy, lỗi, timeout hoặc vi phạm điều kiện kiểm tra.

Để đọc bug, xem `outputs/debug/debug.jsonl`: mỗi dòng chứa câu hỏi, period/entity hint,
candidate, SQL, lỗi validate, retry, quyết định, confidence, output và thời gian từng
module. Ưu tiên kiểm tra `error`, `flags`, `sql`, `result_head` và `abstain_reason`.

## 10. Nhận xét thực nghiệm (sẽ bổ sung)

Phần này sẽ được cập nhật sau khi chạy 100 dòng dữ liệu train:

- Số câu đúng/sai, `unanswerable` và `abstain`.
- PA tổng và PA theo nhóm lỗi.
- Các bug tiêu biểu và ví dụ output sai.
- Thời gian khởi động, thời gian/câu và tổng thời gian thực thi.
- Nhận xét về khả năng tổng quát hóa và ưu tiên cải tiến.

## 11. Những việc đã làm được

- Có pipeline end-to-end từ câu hỏi đến JSON output.
- Đọc được các nguồn CSV, Excel và SQLite.
- Có DuckDB làm lớp truy vấn thống nhất.
- Có xử lý thời gian, entity matching, few-shot và schema/capability profile.
- Có validate SQL, retry khi lỗi và chuẩn hóa nhiều kiểu output.
- Có log debug theo từng câu hỏi và đo thời gian theo module.
- Có unit test cho một phần xử lý ngày và trạng thái đơn.

## 12. Hạn chế hiện tại

- Chưa có báo cáo PA/accuracy được xác nhận cho lần chạy 100 câu.
- Chưa có benchmark chính thức giữa các backend/model.
- `main.py` hiện chạy `k=1`; self-consistency voting nhiều candidate chưa được khai thác
  đầy đủ trong flow mặc định.
- Chất lượng phụ thuộc mạnh vào model, schema profile và dữ liệu bẩn.
- Chưa chứng minh đầy đủ khả năng chạy offline trong Docker và giới hạn phần cứng.
- Các lỗi dòng tổng, ngày mơ hồ, quy đổi đơn vị và JOIN vẫn cần kiểm tra bằng dữ liệu
  thực nghiệm.

## 13. Hướng đi tiếp theo

1. Chạy và lưu kết quả trên 100 câu train, đối chiếu với `answers.jsonl`.
2. Tính PA theo nhóm lỗi: thời gian, entity, JOIN, `unanswerable`, format.
3. Đọc các file debug để phân loại bug lặp lại và bổ sung test hồi quy.
4. Benchmark model/backend và chọn cấu hình theo PA cùng thời gian chạy.
5. Bật voting nhiều candidate, tối ưu ngưỡng trả lời/abstain.
6. Cải thiện chuẩn hóa ngày, tiền tệ, dòng tổng và quy đổi đơn vị.
7. Kiểm thử Docker offline, giới hạn timeout và độ ổn định giữa hai lần chạy.
