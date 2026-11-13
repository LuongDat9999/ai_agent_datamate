import unittest

from ai_agent_datamate.formatting.format_enforcer import fmt_date, to_answer
from ai_agent_datamate.eval.eval_metrics import _classify, _period_has_rows
from ai_agent_datamate.tool.qtype import (
    classify_question,
    is_simple_metric_question,
    metric_guard_reason,
)


class QuestionTypeTests(unittest.TestCase):
    def test_questions_from_outputs(self):
        cases = [
            ("Hôm qua cửa hàng bán được mấy đơn?", "number", "order_count_period"),
            ("Doanh thu tháng 5/2026 tăng hay giảm bao nhiêu phần trăm so với tháng trước?", "percent", "period_compare"),
            ("Tính riêng ngày hôm nay, cửa hàng bán đc bao nhiêu đơn hàng", "number", "order_count_period"),
            ("Cho tôi biết doanh thu tháng 8/2025.", "number", "revenue_period"),
            ("Cho hỏi trong tháng 12/2025, xét theo doanh thu, ngày nào bán đắt nhất?", "date", "other"),
            ("tong doanh thu thang 9 nam 2025 la bao nhiu", "number", "revenue_period"),
            ("xem giup hom nay cua hang co bao nhieu don", "number", "order_count_period"),
            ("Tổng doanh thu hôm qua là bao nhiêu?", "number", "revenue_period"),
            ("tháng 4/2026 ngày nào cửa hàng bán được nhiều tiền nhất", "date", "other"),
            ("dthu thang 4/2026 bao nhieu", "number", "revenue_period"),
            ("coi giùm trong quý 1 năm 2025, dthu của cửa hàng được bao nhiêu tiền", "number", "revenue_period"),
            ("Quý 3 năm 2025 cửa hàng bán được tổng cộng bao nhiêu tiền?", "number", "revenue_period"),
            ("số đơn quys 3/2025 là nhiêu", "number", "order_count_period"),
            ("Giúp mình đếm số đơn đã huỷ trong tháng 11/2025.", "number", "order_count_cancelled"),
            ("tháng 1/2025 tir lệ đơn bij huỷ là bn %", "percent", "cancel_rate"),
            ("tỉ lệ đơn huỷ", "percent", "cancel_rate"),
            ("so voi thang lien truoc, doanh thu thang 3 nam 2026 thay doi bao nhieu % ta", "percent", "period_compare"),
            ("Tổng doanh thu trong ngày hôm nay là bao nhiêu nhỉ?", "number", "revenue_period"),
            ("Số đơn hàng hôm nay là bao nhiêu?", "number", "order_count_period"),
            ("Có mấy ngày trong tháng 2/2025 cửa hàng bán đc từ 10.000.000", "number", "other"),
            ("Doanh thu từng tháng của 3 tháng 11/2025, 12/2025, 1/2026 lần lượt", "list", "other"),
            ("Tồn kho sản phẩm A là bao nhiêu?", "number", "other"),
            ("Đơn giá sản phẩm B bao nhiêu?", "number", "other"),
            ("Ghi hai số doanh thu và số đơn", "list", "other"),
            ("Hôm nay có phải ngày nghỉ không?", "boolean", "other"),
        ]
        for question, answer_type, archetype in cases:
            with self.subTest(question=question):
                result = classify_question(question)
                self.assertEqual(result["type"], answer_type)
                self.assertEqual(result["archetype"], archetype)

    def test_metric_and_date_formatting(self):
        self.assertEqual(classify_question("dthu tháng này")["metric"], "revenue")
        self.assertEqual(classify_question("bnhiu đơn hnay")["metric"], "orders")
        self.assertIsNone(classify_question("tồn kho bao nhiêu")["metric"])
        self.assertEqual(fmt_date("31/12/2025"), "2025-12-31")
        self.assertEqual(fmt_date("31-12-2025"), "2025-12-31")

    def test_order_phrases_override_revenue_words(self):
        for question in ("Hôm qua cửa hàng bán được mấy đơn?", "bao nhieu don hom nay", "số đơn hôm qua"):
            with self.subTest(question=question):
                self.assertEqual(classify_question(question)["metric"], "orders")
                self.assertEqual(_classify(question), "order_count_period")

    def test_metric_guard_is_shared(self):
        for question in ("doanh thu bị hủy", "ngày nào nhiều nhất", "bình quân mỗi đơn", "tỉ lệ chiếm"):
            with self.subTest(question=question):
                self.assertEqual(metric_guard_reason(question), "unsupported_shape")

    def test_metric_entity_and_time_residuals(self):
        vocab = {"bot", "ngot", "ajinomoto"}
        blocked, reason = is_simple_metric_question(
            "tháng 4/2026 bans Bột ngọt Ajinomoto 400g được bao nhiêu tieenf",
            entity_vocab=vocab,
        )
        self.assertFalse(blocked)
        self.assertEqual(reason, "has_entity")
        self.assertTrue(is_simple_metric_question(
            "Tổng doanh thu trong ngày hôm nay là bao nhiêu nhỉ?",
            entity_vocab=vocab,
        )[0])
        self.assertTrue(is_simple_metric_question(
            "Tính riêng ngày hôm nay, cửa hàng bán đc bao nhiêu đơn hàng",
            entity_vocab=vocab,
        )[0])

    def test_shift_residual_is_an_entity(self):
        allowed, reason = is_simple_metric_question("doanh thu ca sang")
        self.assertFalse(allowed)
        self.assertEqual(reason, "has_entity")

    def test_eval_period_guard_rejects_empty_period(self):
        class Connection:
            def execute(self, query):
                return self

            def fetchone(self):
                return None

        class Context:
            con = Connection()

        self.assertFalse(_period_has_rows(
            Context(),
            {"table": "sales", "date_column": "ngay"},
            {"start": "2025-01-01", "end": "2025-01-31"},
        ))

    def test_multi_cell_scalar_selection(self):
        self.assertEqual(
            to_answer("q", "answer", "number", ["label", "value"], [["x", 12], ["y", 13]], question="bao nhiêu"),
            {"id": "q", "type": "number", "answer": 12},
        )
        self.assertEqual(
            to_answer("q", "answer", "date", ["label", "value"], [["x", "31/12/2025"]], question="ngày nào"),
            {"id": "q", "type": "date", "answer": "2025-12-31"},
        )
        self.assertEqual(
            to_answer("q", "answer", "list", ["name", "value"], [["A", 2], ["B", 1]], question="top 2"),
            {"id": "q", "type": "list", "answer": ["A", "B"]},
        )


if __name__ == "__main__":
    unittest.main()
