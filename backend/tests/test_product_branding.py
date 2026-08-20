import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app


class ProductBrandingTests(unittest.TestCase):
    def test_openapi_uses_devpost_project_name(self) -> None:
        self.assertEqual(app.title, "Test Engineer Agent")
        self.assertEqual(app.openapi()["info"]["title"], "Test Engineer Agent")


if __name__ == "__main__":
    unittest.main()
