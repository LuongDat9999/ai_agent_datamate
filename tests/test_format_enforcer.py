import unittest

from ai_agent_datamate.formatting.format_enforcer import expected_type, infer_type, to_answer


class FormatEnforcerTests(unittest.TestCase):
    def test_generic_question_type_inference(self):
        cases = {
            "doanh thu bao nhieu": "number",
            "ty le tang truong bao nhieu %": "percent",
            "ai mua nhieu nhat": "string",
            "ngay nao ban hang nhieu nhat": "date",
            "co phai don nay da thanh toan khong": "boolean",
            "top 3 mat hang": "list",
            "so sanh doanh thu va so don": "table",
        }
        for question, answer_type in cases.items():
            with self.subTest(question=question):
                self.assertEqual(expected_type(question), answer_type)

    def test_type_precedes_shape_for_all_answer_types(self):
        cases = [
            ("number", "bao nhieu", [[12]], ["value"], {"id": "q", "type": "number", "answer": 12}),
            ("percent", "ty le bao nhieu %", [[12.5]], ["value"], {"id": "q", "type": "percent", "answer": 12.5}),
            ("string", "ai mua nhieu nhat", [["An"]], ["name"], {"id": "q", "type": "string", "answer": "An"}),
            ("date", "ngay nao", [["2026-01-02"]], ["event_date"], {"id": "q", "type": "date", "answer": "2026-01-02"}),
            ("boolean", "co phai dung khong", [[True]], ["ok"], {"id": "q", "type": "boolean", "answer": True}),
            ("list", "top 2 mat hang", [["A", 10], ["B", 8]], ["name", "value"], {"id": "q", "type": "list", "answer": ["A", "B"]}),
            ("table", "so sanh doanh thu va so don", [[10], [20]], ["metric"], {"id": "q", "type": "table", "answer": {"columns": ["metric"], "rows": [[10], [20]]}}),
            ("unanswerable", "khong biet", [], [], {"id": "q", "type": "unanswerable"}),
        ]
        for model_type, question, rows, columns, expected in cases:
            with self.subTest(model_type=model_type):
                decision = "unanswerable" if model_type == "unanswerable" else "answer"
                actual = to_answer("q", decision, model_type, columns, rows, question=question)
                self.assertEqual(actual, expected)

    def test_conflicting_model_type_uses_question_type_only_when_safe(self):
        self.assertEqual(infer_type("table", ["value"], [[12]], "bao nhieu"), "number")
        self.assertEqual(infer_type("date", ["value"], [["2026-01-02"]], "ngay nao"), "date")
        self.assertIsNone(infer_type("date", ["value"], [["not-a-date"]], "ngay nao"))
        self.assertIsNone(infer_type("number", ["a", "b"], [[1, 2]], "bao nhieu"))

    def test_list_uses_identifier_column_and_does_not_change_to_table(self):
        result = to_answer(
            "q", "answer", "table", ["name", "count"], [["A", 2], ["B", 1]],
            question="top 2 mat hang",
        )
        self.assertEqual(result["type"], "list")
        self.assertEqual(result["answer"], ["A", "B"])

    def test_unsafe_shape_or_date_abstains(self):
        self.assertEqual(
            to_answer("q", "answer", "date", ["date"], [[None]], question="ngay nao"),
            {"id": "q", "type": "abstain"},
        )
        self.assertEqual(
            to_answer("q", "answer", "number", ["a", "b"], [[1, 2]], question="bao nhieu"),
            {"id": "q", "type": "abstain"},
        )


if __name__ == "__main__":
    unittest.main()
