import unittest

import pandas as pd

from ai_agent_datamate.dataprocess.order_status import render_status_prompt, standardize_statuses
from ai_agent_datamate.tool.prompts import _estimate_tokens


class PromptBudgetTests(unittest.TestCase):
    def test_status_block_is_compact(self):
        status_columns, standardized = standardize_statuses(
            {"orders": pd.DataFrame({"status": ["Xong", "Hủy", "Chờ xác nhận"]})}
        )
        prompt = render_status_prompt(status_columns, standardized)
        self.assertLessEqual(_estimate_tokens(prompt), 200)
        self.assertIn("trang_thai_chuan", prompt)
        self.assertNotIn("Chờ xác nhận", prompt)


if __name__ == "__main__":
    unittest.main()
