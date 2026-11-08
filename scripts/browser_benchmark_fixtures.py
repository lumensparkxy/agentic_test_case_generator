"""Versioned, authored inputs for the Chromium fault-detection benchmark."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Defect:
    name: str
    requirement: str
    before: str
    after: str
    failing_check: str


@dataclass(frozen=True)
class Fixture:
    route: str
    pages: dict[str, str]
    requirements: dict[str, str]
    notes: str
    controls: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    defects: tuple[Defect, ...]
    manual_requirement: str | None = None

    def documents(self, defect: Defect | None = None) -> dict[str, str]:
        if defect is None:
            return self.pages.copy()
        original = self.pages[self.route]
        if original.count(defect.before) != 1 or defect.before == defect.after:
            raise ValueError(f"Defect {defect.name} must change exactly one part of its fixture")
        return {**self.pages, self.route: original.replace(defect.before, defect.after, 1)}


DIRECTORY = """<!doctype html><html lang="en"><title>Employee directory</title>
<main><h1>Employee directory</h1><form>
<label for="name">Employee name</label><input id="name">
<button>Search</button></form><p role="status"></p></main>
<script>
document.querySelector("form").addEventListener("submit", event => {
  event.preventDefault();
  document.querySelector("[role=status]").textContent =
    document.querySelector("#name").value === "Alex Rivera" ? "Alex Rivera" : "No employees found";
});
</script></html>"""

RECEIPT = """<!doctype html><html lang="en"><title>Request receipt</title>
<main><h1>Request receipt</h1><form>
<label for="ref">Reference</label><input id="ref">
<button>Save request</button></form><p role="status"></p>
<a href="/receipt/details" hidden>Open receipt</a></main>
<script>
document.querySelector("form").addEventListener("submit", event => {
  event.preventDefault();
  if (document.querySelector("#ref").value === "Q-17") {
    document.querySelector("[role=status]").textContent = "Request saved";
    document.querySelector("a").hidden = false;
  }
});
</script></html>"""

FIXTURES = {
    "directory": Fixture(
        route="/directory",
        pages={"/directory": DIRECTORY},
        requirements={
            "DIR-MATCH": 'Searching for seeded employee "Alex Rivera" displays exact text "Alex Rivera".',
            "DIR-EMPTY": 'Searching for absent employee "NoMatch Person" displays exact text "No employees found".',
        },
        notes='The directory is public. "Alex Rivera" is the only seeded employee and "NoMatch Person" is absent.',
        controls=(
            (
                "DIR-MATCH",
                (
                    ('Enter "Alex Rivera" into the "Employee name" field', 'field "Employee name" value should equal "Alex Rivera"'),
                    ('Click exact button "Search"', 'exact text "Alex Rivera" is visible'),
                ),
            ),
            (
                "DIR-EMPTY",
                (
                    ('Enter "NoMatch Person" into the "Employee name" field', 'field "Employee name" value should equal "NoMatch Person"'),
                    ('Click exact button "Search"', 'exact text "No employees found" is visible'),
                ),
            ),
        ),
        defects=(
            Defect("wrong-match", "DIR-MATCH", '? "Alex Rivera" :', '? "Alexandra Rivera" :', 'exact text "Alex Rivera" should be visible'),
            Defect("wrong-empty", "DIR-EMPTY", ': "No employees found";', ': "Directory unavailable";', 'exact text "No employees found" should be visible'),
        ),
    ),
    "receipt": Fixture(
        route="/receipt",
        pages={
            "/receipt": RECEIPT,
            "/receipt/details": '<!doctype html><html lang="en"><title>Receipt details</title><h1>Receipt details</h1></html>',
        },
        requirements={
            "REC-SAVE": (
                'Before saving, the link "Open receipt" is not visible. Submitting valid reference "Q-17" with "Save request" shows '
                'exact text "Request saved" and makes "Open receipt" visible. Clicking that link opens the page with exact heading "Receipt details".'
            ),
            "REC-AUDIT": (
                "An authorized auditor must use the separate auditor checklist to verify an immutable record for the successful save "
                "in the external audit store. The browser does not expose the audit store."
            ),
        },
        notes=(
            'The public receipt form starts blank. The details page has the heading "Receipt details". '
            "The audit store, auditor access and checklist are unavailable in this fixture."
        ),
        controls=(
            (
                "REC-SAVE",
                (
                    ('Inspect the "Open receipt" link', 'exact link "Open receipt" is not visible'),
                    ('Enter "Q-17" into the "Reference" field', 'field "Reference" value should equal "Q-17"'),
                    ('Click exact button "Save request"', 'exact text "Request saved" is visible'),
                    ('Inspect the "Open receipt" link', 'exact link "Open receipt" is visible'),
                    ('Click exact link "Open receipt"', 'exact heading "Receipt details" is visible'),
                ),
            ),
            (
                "REC-AUDIT",
                (
                    (
                        "Use the authorized auditor checklist to locate the saved request in the external audit store.",
                        "The auditor verifies an immutable record of the successful save.",
                    ),
                ),
            ),
        ),
        defects=(
            Defect("early-link", "REC-SAVE", '/details" hidden>', '/details">', 'exact link "Open receipt" should not be visible'),
            Defect("wrong-confirmation", "REC-SAVE", '= "Request saved";', '= "Save queued";', 'exact text "Request saved" should be visible'),
        ),
        manual_requirement="REC-AUDIT",
    ),
}
