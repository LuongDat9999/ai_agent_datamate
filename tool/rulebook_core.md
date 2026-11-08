[QT3] Ngày tháng
- Dùng khoảng thời gian đã giải mã trong prompt; lọc ngày theo cột ngày phù hợp.

[QT4] Đơn vị và quy đổi
- Quy đổi về đơn vị cơ bản trước khi cộng, đếm hoặc xếp hạng.
- Dùng đúng cột tiền/giá và nêu rõ đơn vị trong câu trả lời.

[QT5] Quan hệ bảng
- Nối theo khóa được mô tả trong README/schema; aggregate trước khi JOIN để tránh nhân dòng.

[QT6] Không đủ dữ liệu
- Nếu schema không có bằng chứng cần thiết hoặc kết quả không xác định, trả type unanswerable.

[QT7] Kết quả
- Chỉ trả lời từ dữ liệu; ranking phải có tie-breaker ổn định.

[QT9] Trạng thái đơn
- Dùng hướng dẫn chuẩn hoá trạng thái và các view được cung cấp trong schema; không tự đoán giá trị trạng thái.
