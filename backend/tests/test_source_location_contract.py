from __future__ import annotations

import sys
import unittest
from io import BytesIO
from pathlib import Path

from docx import Document


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.api.evaluate import _attach_finding_locations, _normalize_document_rule_result  # noqa: E402


class SourceLocationContractTests(unittest.TestCase):
    def _docx(self, *paragraphs: str) -> bytes:
        document = Document()
        for paragraph in paragraphs:
            document.add_paragraph(paragraph)
        output = BytesIO()
        document.save(output)
        return output.getvalue()

    def test_rule_evidence_is_preserved_for_exact_matching(self) -> None:
        raw = {
            "results": [
                {
                    "rule_id": "DATA_REASON_001",
                    "rule_name": "budget rule",
                    "passed": False,
                    "severity": "risk",
                    "summary": "needs review",
                    "issues": [
                        {
                            "message": "amount mismatch",
                            "evidence": "2.7 Investment total: 100 yuan",
                            "section": "Investment total",
                        }
                    ],
                    "suggestions": [],
                }
            ]
        }
        content = self._docx("2.7 Investment total: 100 yuan")

        result = _normalize_document_rule_result(raw, "data_reasonableness")
        finding = _attach_finding_locations(result, content, "report.docx")["findings"][0]

        self.assertEqual(finding["evidence"], "2.7 Investment total: 100 yuan")
        self.assertEqual(finding["source_location"]["precision"], "line")
        self.assertEqual(finding["source_location"]["quote"], "2.7 Investment total: 100 yuan")

    def test_comma_joined_sections_resolve_to_a_real_paragraph(self) -> None:
        raw = {
            "results": [
                {
                    "rule_id": "BASIS_007",
                    "rule_name": "investment consistency",
                    "passed": False,
                    "severity": "warning",
                    "summary": "needs review",
                    "issues": [
                        {"message": "mismatch", "evidence": "2.7, 8.2", "section": "2.7, 8.2"}
                    ],
                    "suggestions": [],
                }
            ]
        }
        content = self._docx("2.7 Investment overview", "8.2 Budget explanation")

        result = _normalize_document_rule_result(raw, "basis")
        finding = _attach_finding_locations(result, content, "report.docx")["findings"][0]

        self.assertEqual(finding["source_location"]["precision"], "line")
        self.assertTrue(finding["source_location"]["quote"].startswith("2.7"))


if __name__ == "__main__":
    unittest.main()
