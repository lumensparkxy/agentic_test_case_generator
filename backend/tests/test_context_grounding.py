from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import EnrichInput, Requirement, TestCase, TestStep
from app.services.browser_handoff import validate_browser_handoff
from app.services.context_grounding import build_grounded_context, extract_api_surfaces_from_json, extract_ui_elements_from_html
from app.services.execution_service import preview_execution


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

    def test_explicit_native_and_aria_labels_keep_exact_names_and_source(self) -> None:
        html = """
        <label for="ref">Order <strong>&amp; receipt</strong> &nbsp; reference</label>
        <input id="ref" value="private-default">
        <label><span>Receipt</span> owner <input name="owner"></label>
        <label for="date">Alternate name</label>
        <input id="date" aria-label="  Receipt date  ">
        <label for="detail">Receipt details</label>
        <textarea id="detail"><input aria-label="private-markup"></textarea>
        """
        fields = [element for element in extract_ui_elements_from_html("APP", html) if element.element_type == "Field"]
        self.assertEqual([element.name for element in fields], ["Order & receipt reference", "Receipt owner", "Receipt date", "Receipt details"])
        self.assertTrue(all(element.source_id == "APP" for element in fields))
        self.assertIn("HTML field hint: ref", fields[0].description)
        self.assertNotIn("private", str(fields))

    def test_unresolved_duplicate_or_inactive_labels_remain_hints(self) -> None:
        html = """
        <label for="dup">Duplicate ID</label>
        <div id="dup"></div><input id="dup">
        <input id="one" aria-label="Repeated"><input id="two" aria-label="Repeated">
        <label for="referenced">Native name</label>
        <input id="referenced" aria-label="Fallback name" aria-labelledby="missing">
        <label for="gone">Unmatched name</label>
        <input placeholder="Identifier only">
        <input id="hide" type="hidden" aria-label="Hidden field">
        <template><input aria-label="Template field"></template>
        """
        fields = [element for element in extract_ui_elements_from_html("APP", html) if element.element_type == "Field"]
        self.assertEqual([element.name for element in fields], ["dup", "one", "two", "referenced", "Identifier only"])
        self.assertTrue(all(element.description.startswith("Field identifier extracted from artifact:") for element in fields))

    def test_linked_html_label_reaches_browser_preview_without_context_notes(self) -> None:
        target = "https://example.test/receipt"
        requirement = Requirement(id="R1", text='Saving a valid reference displays "Request saved".')
        context = EnrichInput(app_link=target, requirements=[requirement])
        document = """
        <label for="ref">Reference</label><input id="ref">
        <input id="reference-note" placeholder="Reference note">
        <button>Save request</button>
        """
        context.grounded_context = build_grounded_context(
            context, fetcher=lambda url: {"url": url, "status": "Analyzed", "content_type": "text/html", "text": document}
        )
        cases = [
            TestCase(
                id=f"TC-{index}",
                title=f"Save from {label}",
                automation_status="To Be Automated",
                linked_requirement_ids=["R1"],
                steps=[
                    TestStep(step=1, action=f'Open "{target}"', expected=""),
                    TestStep(step=2, action=f'Enter "Q-17" into the "{label}" field', expected=f'field "{label}" value should equal "Q-17"'),
                    TestStep(step=3, action='Click the "Save request" button', expected='exact text "Request saved" is visible'),
                ],
            )
            for index, label in enumerate(("Reference", "reference-note"), start=1)
        ]
        warnings = validate_browser_handoff(cases, context, [requirement])
        self.assertIn("TC-2", warnings[1])
        self.assertEqual([case.automation_status for case in cases], ["To Be Automated", "Manual"])
        preview = preview_execution(cases, target_base_url=target)
        self.assertEqual([case.source_test_case_id for case in preview.executable], ["TC-1"])
        self.assertEqual([case.source_test_case_id for case in preview.manual], ["TC-2"])

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
