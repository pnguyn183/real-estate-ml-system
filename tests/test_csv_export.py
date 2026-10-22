from io import BytesIO
import unittest

import pandas as pd

from csv_export import spreadsheet_safe_csv


class SpreadsheetSafeCSVTests(unittest.TestCase):
    def test_formula_like_text_is_neutralized_without_changing_numeric_values(self):
        frame = pd.DataFrame({
            "text": [
                "=HYPERLINK(\"https://example.invalid\",\"open\")",
                " \t+SUM(1,2)",
                "\r\n-CMD",
                "\u200b@SUM(1,2)",
                "ordinary text",
            ],
            "number": [-42, 1, 2, 3, 4],
        })

        exported = pd.read_csv(
            BytesIO(spreadsheet_safe_csv(frame)),
            encoding="utf-8-sig",
            dtype={"text": str},
        )

        self.assertEqual(exported["text"].tolist(), [
            "'=HYPERLINK(\"https://example.invalid\",\"open\")",
            "' \t+SUM(1,2)",
            "'\r\n-CMD",
            "'\u200b@SUM(1,2)",
            "ordinary text",
        ])
        self.assertEqual(exported["number"].tolist(), [-42, 1, 2, 3, 4])

    def test_does_not_modify_the_input_dataframe(self):
        frame = pd.DataFrame({"title": ["=1+1"]})
        original = frame.copy(deep=True)

        spreadsheet_safe_csv(frame)

        pd.testing.assert_frame_equal(frame, original)


if __name__ == "__main__":
    unittest.main()
