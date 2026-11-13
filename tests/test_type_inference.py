import unittest

from ai_agent_datamate.formatting.type_inference import infer_question_type


class TypeInferenceTests(unittest.TestCase):
    def test_language_variants(self):
        cases = {
            "tỷ lệ tăng giảm bao nhiêu %": "percent",
            "thang nao ban nhieu nhat": "date",
            "co phai don nay dung khong": "boolean",
            "top 5 mat hang": "list",
            "liệt kê những khách nào": "list",
            "khach nao mua nhieu nhat": "string",
            "so sanh doanh thu va so don": "table",
            "tổng doanh thu là bao nhiêu": "number",
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                answer_type, confidence = infer_question_type(question)
                self.assertEqual(answer_type, expected)
                self.assertGreaterEqual(confidence, 0.6)

    def test_ambiguous_question_is_not_guessed(self):
        answer_type, confidence = infer_question_type("cho tôi biết kết quả")
        self.assertIsNone(answer_type)
        self.assertLess(confidence, 0.6)


if __name__ == "__main__":
    unittest.main()
