from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import EnrichInput, Requirement
from app.services.context_grounding import build_grounded_context, extract_api_surfaces_from_json, extract_ui_elements_from_html


class ContextGroundingTests(unittest.TestCase):
    def test_extract_ui_elements_from_html_discovers_headings_links_buttons_and_fields(self) -> None:
        html = """
        <html>
          <head><title>Login</title></head>
          <body>
            <h1>Sign In</h1>
            <a href="/docs/getting-started">Get started</a>
            <form>
              <input name=\"email\" />
              <button>Submit</button>
            </form>
          </body>
        </html>
        """

        elements = extract_ui_elements_from_html("ART-APP-01", html, base_url="https://example.com/app")

        element_names = [element.name for element in elements]
        self.assertIn("Login", element_names)
        self.assertIn("Sign In", element_names)
        self.assertIn("Get started", element_names)
        self.assertIn("Submit", element_names)
        self.assertIn("email", element_names)
        navigation = next(element for element in elements if element.name == "Get started")
        self.assertEqual(navigation.element_type, "Navigation")
        self.assertEqual(navigation.href, "https://example.com/docs/getting-started")

    def test_extract_api_surfaces_from_json_reads_openapi_paths(self) -> None:
        raw_json = '{"paths": {"/health": {"get": {"summary": "Health check"}}}}'

        api_surfaces = extract_api_surfaces_from_json("ART-API-01", raw_json)

        self.assertEqual(len(api_surfaces), 1)
        self.assertEqual(api_surfaces[0].method, "GET")
        self.assertEqual(api_surfaces[0].path, "/health")

    def test_build_grounded_context_uses_fetcher_results(self) -> None:
        payload = EnrichInput(
            requirements=[Requirement(id="REQ-001", text="The system shall keep an expense report in Draft status until the employee submits it.")],
            app_link="https://example.com/app",
            notes="Grounding test",
        )

        def fake_fetcher(url: str) -> dict:
            return {
                "url": url,
                "status": "Analyzed",
                "content_type": "text/html",
                "text": "<html><head><title>Expense App</title></head><body><h1>Expense Report</h1><button>Submit</button></body></html>",
                "error": None,
            }

        grounded_context = build_grounded_context(payload, fetcher=fake_fetcher)

        self.assertEqual(len(grounded_context.artifact_sources), 2)
        self.assertEqual(grounded_context.artifact_sources[0].status, "Analyzed")
        self.assertTrue(any(element.name == "Expense App" for element in grounded_context.ui_elements))
        self.assertTrue(grounded_context.workflows)
        self.assertIn("Draft → Submitted", grounded_context.workflows[0].transitions)

    def test_build_grounded_context_preserves_partial_warnings_when_fetching_fails(self) -> None:
        payload = EnrichInput(
            requirements=[Requirement(id="REQ-001", text="The system shall show the app dashboard.")],
            app_link="https://example.com/app",
            prototype_link="https://example.com/prototype",
        )

        def fake_fetcher(url: str) -> dict:
            if url.endswith("/prototype"):
                raise TimeoutError("raw timeout detail")
            return {
                "url": url,
                "status": "Skipped",
                "content_type": "application/pdf",
                "text": None,
                "error": "Unsupported artifact content type: application/pdf.",
            }

        grounded_context = build_grounded_context(payload, fetcher=fake_fetcher)

        self.assertEqual(len(grounded_context.artifact_sources), 2)
        self.assertEqual(grounded_context.artifact_sources[0].status, "Skipped")
        self.assertIn("Unsupported artifact content type", grounded_context.artifact_sources[0].notes)
        self.assertEqual(grounded_context.artifact_sources[1].status, "Unavailable")
        self.assertEqual(grounded_context.artifact_sources[1].notes, "Artifact fetch failed: TimeoutError")
        self.assertIn("skipped 1", grounded_context.summary)
        self.assertIn("unavailable 1", grounded_context.summary)


if __name__ == "__main__":
    unittest.main()
