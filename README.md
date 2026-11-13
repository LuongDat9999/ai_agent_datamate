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
7. Suy luận cột doanh thu cấp dòng, loại đơn huỷ/trả hàng theo mã đơn và kiểm tra
   phạm vi ngày trước khi kết luận dữ liệu không tồn tại.
8. Từ chối cấu hình metric nếu bảng đã chuẩn hóa còn trạng thái `khong_ro`; mỗi đơn
   phải thuộc một trong bốn trạng thái hợp lệ.

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
8. Chọn kết quả, `unanswerable` khi dữ liệu nằm ngoài phạm vi được bảo đảm, hoặc
   `abstain` nếu không đủ tin cậy.
9. Chuẩn hóa kết quả về schema JSON thống nhất.

Metric resolver có thêm các guard nghiệp vụ:

- Doanh thu chỉ dùng cột cấp dòng; cấu hình suy ra từ tổng hóa đơn được đánh dấu
  `revenue_basis=invoice_total` và không được resolver trả lời.
- Câu so sánh kỳ chỉ được resolver khi câu hỏi yêu cầu phần trăm/tăng trưởng.
- Các câu chứa thực thể ca sáng hoặc ca chiều không được coi là metric đơn giản.

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
| `tool/metrics.py` | Suy luận cấu hình metric, tạo SQL doanh thu/số đơn và coverage guard. |
| `dataprocess/order_status.py` | Chuẩn hóa trạng thái đơn và tạo quy ước trạng thái cho prompt. |

## 7. Flow thực hiện (`main.py`)

```text
Đọc tham số
  → chọn split và tối đa 100 câu hỏi
  → đọc rulebook, calendar, few-shot bank
  → load_business() và cache dữ liệu theo business_id
  → thử metric resolver cho các câu doanh thu/số đơn đơn giản
  → build_prompt() cho từng câu hỏi
  → generate_candidates(k candidates; mặc định k=1)
  → validate_with_retry() (tối đa 2 retry)
  → decide() / abstain khi candidate invalid, có cờ chặn hoặc confidence thấp
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

Cổng quyết định đọc ngưỡng và mức phạt từ biến môi trường:
`DECISION_MIN_CONFIDENCE`, `DECISION_SOFT_FLAG_PENALTY`,
`DECISION_RETRY_PENALTY`, `DECISION_RESULT_SIZE_PENALTY`.
Các cờ chặn cứng luôn dẫn đến `abstain`; cảnh báo mềm chỉ làm giảm confidence.

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
- Với `unanswerable`, output có thể kèm `reason` dạng chuỗi; không so sánh nội dung
  reason khi đối chiếu đáp án gold.

Để đọc bug, xem `outputs/debug/debug.jsonl`: mỗi dòng chứa câu hỏi, period/entity hint,
candidate, SQL, lỗi validate, retry, quyết định, confidence, output và thời gian từng
module. Ưu tiên kiểm tra `error`, `flags`, `sql`, `result_head` và `abstain_reason`.

## 10. Nhận xét thực nghiệm

Chưa chạy lại kết quả benchmark sau các thay đổi về trạng thái đơn, chọn cột doanh thu,
dedupe và coverage guard, nên phần này **chưa cập nhật số liệu**. Không dùng các số liệu
benchmark cũ để kết luận chất lượng hiện tại của pipeline.

## 11. Những việc đã làm được

- Có pipeline end-to-end từ câu hỏi đến JSON output.
- Đọc được các nguồn CSV, Excel và SQLite.
- Có DuckDB làm lớp truy vấn thống nhất.
- Có xử lý thời gian, entity matching, few-shot và schema/capability profile.
- Có validate SQL, retry khi lỗi và chuẩn hóa nhiều kiểu output.
- Có log debug theo từng câu hỏi và đo thời gian theo module.
- Có unit test cho xử lý ngày, trạng thái đơn, metric resolver và các guard câu hỏi.
- Có metric resolver cho doanh thu, số đơn, đơn huỷ và tỉ lệ huỷ.
- Có phân biệt `unanswerable` với `abstain`, kèm reason và trace coverage.
- Có script `eval/check_dedupe.py` để so sánh kết quả có/không loại dòng trùng.

## 12. Hạn chế hiện tại

- Chưa có benchmark chính thức mới giữa các backend/model sau các thay đổi gần đây.
- `main.py` hiện chạy `k=1`; self-consistency voting nhiều candidate chưa được khai thác
  đầy đủ trong flow mặc định.
- Chất lượng phụ thuộc mạnh vào model, schema profile và dữ liệu bẩn.
- Chưa chứng minh đầy đủ khả năng chạy offline trong Docker và giới hạn phần cứng.
- Các lỗi dòng tổng, ngày mơ hồ, quy đổi đơn vị và JOIN vẫn còn ảnh hưởng trực tiếp đến
  kết quả.

## 13. Hướng đi tiếp theo

1. Chạy lại benchmark theo từng business và cập nhật mục 10 khi có kết quả ổn định.
2. Kiểm tra JOIN, cấp độ tổng hợp và quy đổi đơn vị để tránh tổng tiền bị phóng đại.
3. Dùng `eval/check_dedupe.py` để quyết định dedupe riêng theo business dựa trên gold.
4. Đọc debug, phân loại bug lặp lại, bổ sung test hồi quy và chạy lại benchmark.
5. Benchmark model/backend, bật voting nhiều candidate và tối ưu ngưỡng trả lời.
6. Kiểm thử Docker offline, timeout và độ ổn định giữa hai lần chạy.

### Tín hiệu bug lịch sử từ `outputs/debug_100/debug.jsonl`

Các số liệu dưới đây là log của một lần chạy cũ, chưa được cập nhật theo code hiện tại.

- `NO_STATUS_FILTER`: 69 lần.
- `NO_DATE_FILTER`: 41 lần.
- `PARSE_ERROR`: 22 lần.
- `UNSAFE_SQL`: 4 lần; `FANOUT_RISK`: 2 lần; `CARTESIAN`: 1 lần.

Thứ tự ưu tiên sửa: bắt buộc xác định bộ lọc ngày/trạng thái, sửa parse ngày và kiểu số,
kiểm tra JOIN chống nhân dòng, sau đó cải thiện entity matching và câu hỏi dạng list/percent.

### Thời gian thực thi

Thời gian thực tế của toàn bộ lần chạy là **61,68 phút/100 câu**, bao gồm cả thời gian
load model. Đây là kết quả trên Kaggle với 2 GPU T4 và `Qwen/Qwen2.5-7B-Instruct`;
không nên so sánh trực tiếp với thời gian chạy trên phần cứng khác.

### Hướng nâng cấp

Thử nghiệm tiếp theo dự kiến chạy trên GPU **RTX 5090**, sử dụng model
`Qwen3-14B` và tăng ngân sách token của prompt. Mục tiêu là cung cấp schema, rulebook,
ví dụ few-shot và ngữ cảnh đầy đủ hơn cho model, đồng thời đánh giá lại PA, tỷ lệ
`abstain` và thời gian thực thi.
