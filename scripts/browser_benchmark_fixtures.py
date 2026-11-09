"""Versioned, authored inputs for the Chromium fault-detection benchmark."""

from dataclasses import dataclass
import json


@dataclass(frozen=True)
class LiteralSubmission:
    field: str
    button: str
    value: str


@dataclass(frozen=True)
class Defect:
    name: str
    requirement: str
    before: str
    after: str
    failing_check: str
    submission: LiteralSubmission | None = None


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

ORDERS = """<!doctype html><html lang="en"><title>Orders</title><h1>Orders</h1>
<form>
<label for="status">Order status</label>
<select id="status"><option value="open_internal">Open</option><option value="closed_internal">Closed</option></select>
<button>Apply filters</button></form><p role="status"></p>
<script>
document.querySelector("form").addEventListener("submit", event => {
  event.preventDefault();
  document.querySelector("[role=status]").textContent =
    document.querySelector("#status").value === "closed_internal" ? "Closed order 0142" : "Open order 0007";
});
</script></html>"""

PREFERENCES = """<!doctype html><html lang="en"><title>Receipt preferences</title><h1>Receipt preferences</h1>
<form>
<label for="email">Email receipt</label><input id="email" type="checkbox">
<button>Save preferences</button></form><p role="status"></p>
<script>
const email = document.querySelector("#email");
email.checked = localStorage.getItem("email-receipt") === "enabled";
document.querySelector("form").addEventListener("submit", event => {
  event.preventDefault();
  localStorage.setItem("email-receipt", email.checked ? "enabled" : "disabled");
  document.querySelector("[role=status]").textContent = email.checked ? "Email receipt enabled" : "Email receipt disabled";
});
</script></html>"""

PAYLOAD_EDITOR = """<!doctype html><html lang="en"><title>Payload editor</title><h1>Payload editor</h1>
<label for="payload">Payload</label><textarea id="payload"></textarea>
<button>Apply payload</button><p role="status">Waiting</p><pre id="saved" data-testid="saved-payload"></pre>
<script>
document.querySelector("button").addEventListener("click", () => {
  const input = document.querySelector("#payload").value;
  const saved = input;
  document.querySelector("#saved").textContent = saved;
  try {
    JSON.parse(saved);
    document.querySelector("[role=status]").textContent = "Valid JSON accepted";
  } catch {
    document.querySelector("[role=status]").textContent = "Invalid JSON rejected";
  }
});
</script></html>"""

VALID_PAYLOAD = '{\n  "enabled": false,\n  "label": "  Launch into β  ",\n  "template": "{env.example}"\n}'
INVALID_PAYLOAD = '{\n  "enabled": false,\n  "label": "broken"\n'

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
    "orders": Fixture(
        route="/orders",
        pages={"/orders": ORDERS},
        requirements={"ORD-CLOSED": 'Selecting the "Closed" order status and applying filters displays exact text "Closed order 0142".'},
        notes='The orders filter is public. Choose "Closed" in the native field "Order status" and use the "Apply filters" button.',
        controls=(
            (
                "ORD-CLOSED",
                (
                    ('Select exact option "Closed" in field "Order status"', ""),
                    ('Click exact button "Apply filters"', 'exact text "Closed order 0142" is visible'),
                ),
            ),
        ),
        defects=(
            Defect(
                "wrong-order-result",
                "ORD-CLOSED",
                '? "Closed order 0142" :',
                '? "Open order 0007" :',
                'exact text "Closed order 0142" should be visible',
            ),
            Defect(
                "wrong-option-label",
                "ORD-CLOSED",
                ">Closed</option>",
                ">Closed archive</option>",
                'I select exact option "Closed" in field "Order status"',
            ),
        ),
    ),
    "preferences": Fixture(
        route="/preferences",
        pages={"/preferences": PREFERENCES},
        requirements={
            "PREF-ENABLE": (
                'Checking "Email receipt" and pressing "Save preferences" displays exact text "Email receipt enabled". '
                "Reopening the same page shows the checkbox is still checked."
            ),
            "PREF-DISABLE": (
                'With the preference enabled, unchecking "Email receipt" and pressing "Save preferences" displays exact text '
                '"Email receipt disabled". Reopening the same page shows the checkbox is still unchecked.'
            ),
        },
        notes=(
            'The public form starts with "Email receipt" unchecked in a fresh browser session. For the disable case, first check '
            'it and save using the "Save preferences" button in the same case. Then uncheck it, save, and reopen the same supplied page.'
        ),
        controls=(
            (
                "PREF-ENABLE",
                (
                    ('Check exact checkbox "Email receipt"', 'exact checkbox "Email receipt" is checked'),
                    ('Click exact button "Save preferences"', 'exact text "Email receipt enabled" is visible'),
                    ('Open "{target}"', 'exact checkbox "Email receipt" is checked'),
                ),
            ),
            (
                "PREF-DISABLE",
                (
                    ('Check exact checkbox "Email receipt"', 'exact checkbox "Email receipt" is checked'),
                    ('Click exact button "Save preferences"', 'exact text "Email receipt enabled" is visible'),
                    ('Uncheck exact checkbox "Email receipt"', 'exact checkbox "Email receipt" is not checked'),
                    ('Click exact button "Save preferences"', 'exact text "Email receipt disabled" is visible'),
                    ('Open "{target}"', 'exact checkbox "Email receipt" is not checked'),
                ),
            ),
        ),
        defects=(
            Defect(
                "lost-enabled-preference",
                "PREF-ENABLE",
                '? "enabled" : "disabled"',
                '? "disabled" : "disabled"',
                'exact checkbox "Email receipt" should be checked',
            ),
            Defect(
                "lost-disabled-preference",
                "PREF-DISABLE",
                '? "enabled" : "disabled"',
                '? "enabled" : "enabled"',
                'exact checkbox "Email receipt" should not be checked',
            ),
            Defect(
                "mixed-disabled-preference",
                "PREF-DISABLE",
                'email.checked = localStorage.getItem("email-receipt")',
                'email.indeterminate = localStorage.getItem("email-receipt") === "disabled";\nemail.checked = localStorage.getItem("email-receipt")',
                'exact checkbox "Email receipt" should not be checked',
            ),
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
    "payload-editor": Fixture(
        route="/payload-editor",
        pages={"/payload-editor": PAYLOAD_EDITOR},
        requirements={
            "JSON-VALID": (
                'Submit the exact named browser sample "valid-json" in "Payload" using "Apply payload". '
                'Show "Valid JSON accepted" and the submitted text with its original line breaks, indentation and spaces. '
            ),
            "JSON-INVALID": (
                'Submit the exact named browser sample "malformed-json" in "Payload" using "Apply payload". '
                'Show "Invalid JSON rejected" and the attempted payload as entered. The input has no closing brace. '
                "The final line feed is part of the sample."
            ),
        },
        notes=(
            'The public editor starts with an empty native multiline "Payload" field and requires no account or setup.\n'
            'Browser result test id "saved-payload" on "{target}" shows field "Payload" after button "Apply payload"\n'
            f'Browser sample "valid-json" is {json.dumps(VALID_PAYLOAD, ensure_ascii=False)}\n'
            f'Browser sample "malformed-json" is {json.dumps(INVALID_PAYLOAD)}'
        ),
        controls=tuple(
            (
                requirement,
                (
                    (f'Enter browser sample "{sample}" into field "Payload"', f'field "Payload" value should equal browser sample "{sample}"'),
                    ('Click exact button "Apply payload"', f'exact text "{status}" is visible'),
                    ("Inspect the saved payload", f'test id "saved-payload" text should equal browser sample "{sample}"'),
                ),
            )
            for requirement, sample, status in (
                ("JSON-VALID", "valid-json", "Valid JSON accepted"),
                ("JSON-INVALID", "malformed-json", "Invalid JSON rejected"),
            )
        ),
        defects=(
            Defect(
                "compact-valid-payload",
                "JSON-VALID",
                "const saved = input;",
                "const saved = JSON.stringify(JSON.parse(input));",
                f'test id "saved-payload" text should equal literal {json.dumps(VALID_PAYLOAD)}',
                LiteralSubmission("Payload", "Apply payload", VALID_PAYLOAD),
            ),
            Defect(
                "repair-invalid-payload",
                "JSON-INVALID",
                "const saved = input;",
                'const saved = input + "}";',
                'exact text "Invalid JSON rejected" should be visible',
                LiteralSubmission("Payload", "Apply payload", INVALID_PAYLOAD),
            ),
            Defect(
                "change-rejected-payload",
                "JSON-INVALID",
                "const saved = input;",
                'const saved = input + " ";',
                f'test id "saved-payload" text should equal literal {json.dumps(INVALID_PAYLOAD)}',
                LiteralSubmission("Payload", "Apply payload", INVALID_PAYLOAD),
            ),
        ),
    ),
}
