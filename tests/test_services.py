import tempfile
import unittest
from pathlib import Path

import pandas as pd

import contingent
from services import reconciliation
from services.imports import (
    clean_upload_text,
    find_dataframe_column_casefold,
    find_row_value_casefold,
    read_tabular_upload,
)


class ImportServiceTests(unittest.TestCase):
    def test_csv_fallback_and_casefold_helpers_preserve_import_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "students.csv"
            path.write_bytes("Фамилия;Почта\nИванов;ivanov@example.test\n".encode("cp1251"))

            frame = read_tabular_upload(path)

        self.assertEqual(list(frame.columns), ["Фамилия", "Почта"])
        self.assertEqual(find_dataframe_column_casefold(frame.columns, {"почта"}), "Почта")
        self.assertEqual(find_row_value_casefold(frame.iloc[0], {"фамилия"}), "Иванов")
        self.assertEqual(clean_upload_text(pd.NA), "")


class ReconciliationServiceTests(unittest.TestCase):
    def test_legacy_contingent_module_reexports_service_api(self):
        self.assertIs(contingent.reconcile, reconciliation.reconcile)
        self.assertIs(contingent.parse_docx, reconciliation.parse_docx)


if __name__ == "__main__":
    unittest.main()
