import argparse
import json
import os
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ingest_service import preview_pdf_extraction


DEFAULT_PDF_PATH = PROJECT_ROOT / "test" / "pdf测试.pdf"


class TestPdfExtractionPreview(unittest.TestCase):
    """Preview PDF extraction in the console without writing to Neo4j."""

    def test_preview_pdf_extraction(self) -> None:
        pdf_path = Path(os.getenv("TEST_PDF_PATH", str(DEFAULT_PDF_PATH))).resolve()
        mode = os.getenv("TEST_PDF_MODE", "template").strip().lower() or "template"
        preview_chars = int(os.getenv("TEST_PDF_PREVIEW_CHARS", "1200"))

        self.assertTrue(pdf_path.exists(), f"PDF file not found: {pdf_path}")

        result = preview_pdf_extraction(
            path=str(pdf_path),
            mode=mode,
            text_preview_chars=preview_chars,
        )

        print("\n=== PDF Extraction Preview ===")
        print(f"PDF: {pdf_path}")
        print(f"Mode: {mode}")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        self.assertEqual(result["pdf_path"], str(pdf_path))
        self.assertEqual(result["mode"], mode)
        self.assertIn("text_preview", result)

        if mode == "llm_rel":
            self.assertIn("raw_llm_output", result)
        else:
            self.assertIn("records", result)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview PDF extraction result without importing data into Neo4j.",
    )
    parser.add_argument(
        "--pdf",
        default=str(DEFAULT_PDF_PATH),
        help="Absolute or relative path to the PDF file to preview.",
    )
    parser.add_argument(
        "--mode",
        default="template",
        choices=["template", "llm", "hybrid", "llm_rel"],
        help="Extraction mode. Default is template to avoid calling the LLM.",
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=1200,
        help="Number of extracted text characters to keep in the console preview.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    os.environ["TEST_PDF_PATH"] = args.pdf
    os.environ["TEST_PDF_MODE"] = args.mode
    os.environ["TEST_PDF_PREVIEW_CHARS"] = str(args.preview_chars)
    unittest.main(argv=[sys.argv[0]], verbosity=2)
