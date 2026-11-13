import unittest

from ai_agent_datamate.llm.generator import _candidate_from_response, resolve_candidate_type


class GeneratorParserTests(unittest.TestCase):
    def test_supported_response_shapes(self):
        responses = [
            "```sql\nSELECT SUM(total) FROM t;\n```",
            "```sql\nSELECT COUNT(*) FROM t;\n```\n\nThis query counts bills.",
            "```sql\nWITH a AS (SELECT 1) SELECT * FROM a;\n```\n\n**Explanation:**",
            "<type>number</type>\n<sql>\nSELECT COUNT(*) FROM t\n</sql>",
        ]
        for response in responses:
            with self.subTest(response=response):
                candidate = _candidate_from_response(response)
                self.assertTrue(candidate.sql.startswith(("SELECT", "WITH")))
                self.assertIsNone(candidate.abstain_reason)

    def test_first_sql_fence_and_unclosed_fence(self):
        candidate = _candidate_from_response("```text\nhello\n```\n```python\nSELECT 1;\n```")
        self.assertEqual(candidate.sql, "SELECT 1")
        candidate = _candidate_from_response("```sql\nSELECT 1")
        self.assertEqual(candidate.sql, "SELECT 1")

    def test_no_sql_has_distinct_reason(self):
        candidate = _candidate_from_response("I cannot answer this.")
        self.assertEqual(candidate.abstain_reason, "no_sql_found")

    def test_missing_type_is_resolved_from_question(self):
        candidate = _candidate_from_response("```sql\nSELECT COUNT(*) FROM t\n```")
        resolve_candidate_type(candidate, "Có bao nhiêu đơn hàng?")
        self.assertEqual((candidate.answer_type, candidate.type_source), ("number", "question"))


if __name__ == "__main__":
    unittest.main()
