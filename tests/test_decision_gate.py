import unittest

from ai_agent_datamate.decision.checker import DecisionConfig, confidence_for_candidates, decide
from ai_agent_datamate.llm.generator import Candidate


def candidate(*, sql="SELECT 1", answer_type="number", rows=None, flags=None, error=None):
    return Candidate(
        sql,
        answer_type,
        rows=[[1]] if rows is None else rows,
        columns=["value"],
        flags=list(flags or ()),
        error=error,
    )


class DecisionGateTests(unittest.TestCase):
    def test_invalid_contracts_always_abstain(self):
        cases = [
            candidate(sql=None, answer_type="invalid"),
            candidate(sql=None),
            candidate(sql="SELECT 1 FROM"),
            candidate(sql="SELECT (1"),
        ]
        for item in cases:
            with self.subTest(item=item):
                decision, winner = decide([item])
                self.assertEqual(decision, "abstain")
                self.assertIsNone(winner)
                self.assertTrue(item.abstain_reason)

    def test_hard_flags_always_abstain(self):
        for flag in ("UNSAFE_SQL", "CARTESIAN", "PARSE_ERROR", "FANOUT_RISK"):
            item = candidate(flags=[flag])
            decision, winner = decide([item])
            self.assertEqual((decision, winner), ("abstain", None))
            self.assertIn(flag, item.abstain_reason)

    def test_period_without_date_filter_is_hard_block(self):
        item = candidate(flags=["NO_DATE_FILTER"])
        decision, winner = decide([item], periods={"month": {}})
        self.assertEqual((decision, winner), ("abstain", None))

    def test_clean_result_has_real_confidence(self):
        item = candidate()
        decision, winner = decide([item])
        self.assertEqual(decision, "answer")
        self.assertIs(winner, item)
        self.assertGreater(confidence_for_candidates([item], winner), 0.0)

    def test_semantic_flags_are_hard_blocks(self):
        for flag in ("NO_STATUS_FILTER", "LINE_GRAIN_ORDER_METRIC", "NO_DATE_FILTER"):
            with self.subTest(flag=flag):
                decision, winner = decide([candidate(flags=[flag])])
                self.assertEqual((decision, winner), ("abstain", None))

    def test_resolver_is_exempt_from_semantic_flag_blocks(self):
        item = candidate(flags=["RESOLVER", "NO_STATUS_FILTER", "NO_DATE_FILTER"])
        decision, winner = decide([item])
        self.assertEqual((decision, winner), ("answer", item))

    def test_voting_uses_consensus(self):
        first = candidate()
        second = candidate()
        different = candidate(rows=[[2]])
        decision, winner = decide([first, second, different], tau=0.6)
        self.assertEqual(decision, "answer")
        self.assertEqual(winner.rows, [[1]])

    def test_threshold_is_configurable(self):
        item = candidate()
        item._retry_count = 1
        decision, winner = decide([item], tau=0.99)
        self.assertEqual((decision, winner), ("abstain", None))
        self.assertEqual(DecisionConfig().min_confidence, 0.6)


if __name__ == "__main__":
    unittest.main()
