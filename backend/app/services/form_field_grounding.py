"""Extract field names with explicit associations in fetched HTML."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
import re


_FIELDS = {"input", "select", "textarea"}
_LABELABLE = _FIELDS | {"button", "meter", "output", "progress"}
_BUTTON_INPUTS = {"button", "image", "reset", "submit"}
_INACTIVE_CONTENT = {"script", "style", "template", "noscript", "title", "xmp", "iframe", "noembed", "noframes", "plaintext"}
_UNAVAILABLE = {"disabled", "hidden", "inert"}
MAX_SELECT_OPTIONS = 40


def _unavailable(attrs: dict[str, str]) -> bool:
    return bool(_UNAVAILABLE.intersection(attrs)) or attrs.get("aria-disabled", "").lower() == "true"


@dataclass(frozen=True)
class FormField:
    label: str
    hint: str
    select_options: tuple[str, ...] | None


@dataclass
class _SelectOption:
    label: str
    available: bool
    text: list[str] = field(default_factory=list)


@dataclass
class _NativeSelect:
    index: int
    available: bool
    group_available: bool = True
    option: _SelectOption | None = None
    choices: list[tuple[str, bool]] = field(default_factory=list)

    def finish_option(self) -> None:
        if self.option:
            # Native option.label uses the explicit label or ASCII-whitespace-normalized text.
            text = re.sub(r"[\t\n\f\r ]+", " ", "".join(self.option.text)).strip(" ")
            self.choices.append((self.option.label or text, self.option.available))
            self.option = None

    def finish(self) -> tuple[str, ...]:
        self.finish_option()
        if not self.available:
            return ()
        counts = Counter(label for label, _ in self.choices)
        return tuple(label for label, available in self.choices if available and label.strip() and len(label) <= 160 and counts[label] == 1)[
            :MAX_SELECT_OPTIONS
        ]


@dataclass
class _NativeLabel:
    target_id: str | None
    text: list[str] = field(default_factory=list)
    wrapped_field: int | None = None
    ambiguous: bool = False


class _FormMarkup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: Counter[str] = Counter()
        self.fields: list[dict[str, str]] = []
        self.labels: list[_NativeLabel] = []
        self.selects: dict[int, tuple[str, ...]] = {}
        self._open_label: _NativeLabel | None = None
        self._select: _NativeSelect | None = None
        self._fieldsets: list[bool] = []
        self._inactive_tags: list[str] = []

    def handle_starttag(self, tag: str, raw_attrs: list[tuple[str, str | None]]) -> None:
        if self._inactive_tags:
            if self._inactive_tags[-1] == "template" and tag in _INACTIVE_CONTENT | {"textarea"}:
                self._inactive_tags.append(tag)
            return
        if tag in _INACTIVE_CONTENT:
            self._inactive_tags.append(tag)
            return
        attrs = {name: value or "" for name, value in reversed(raw_attrs)}
        if tag == "fieldset":
            self._fieldsets.append(_unavailable(attrs))
        if self._select:
            if tag == "option":
                self._select.finish_option()
                available = self._select.group_available and not _unavailable(attrs)
                self._select.option = _SelectOption(attrs.get("label", ""), available)
                return
            if tag == "optgroup":
                self._select.finish_option()
                self._select.group_available = not _unavailable(attrs)
                return
            if tag == "select":
                self._select.available = False
                return
            if tag in {"input", "textarea"}:
                self._select.available = False
        if identifier := attrs.get("id"):
            self.ids[identifier] += 1
        if tag == "label":
            if self._open_label is None:
                self._open_label = _NativeLabel(target_id=attrs.get("for"))
            else:
                self._open_label.ambiguous = True
            return

        input_type = attrs.get("type", "").lower()
        if tag == "input" and input_type == "hidden":
            return
        field_index = None
        if tag in _FIELDS and not (tag == "input" and input_type in _BUTTON_INPUTS):
            field_index = len(self.fields)
            self.fields.append(attrs)
            if tag == "select":
                available = not any(self._fieldsets) and not _unavailable(attrs) and "multiple" not in attrs
                self.selects[field_index] = ()
                self._select = _NativeSelect(field_index, available)
        if self._open_label and tag in _LABELABLE:
            # A wrapped input has no text of its own. Other wrapped controls
            # can contribute values or option text to the label.
            if tag != "input" or input_type in _BUTTON_INPUTS or self._open_label.wrapped_field is not None:
                self._open_label.ambiguous = True
            else:
                self._open_label.wrapped_field = field_index
        if tag == "textarea":
            self._inactive_tags.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._inactive_tags:
            if tag == self._inactive_tags[-1]:
                self._inactive_tags.pop()
            return
        if self._select:
            if tag == "select":
                self.selects[self._select.index] = self._select.finish()
                self._select = None
            elif tag in {"option", "optgroup"}:
                self._select.finish_option()
                if tag == "optgroup":
                    self._select.group_available = True
        if tag == "fieldset" and self._fieldsets:
            self._fieldsets.pop()
        if tag == "label" and self._open_label:
            self.labels.append(self._open_label)
            self._open_label = None

    def handle_data(self, data: str) -> None:
        if self._select and self._select.option and not self._inactive_tags:
            self._select.option.text.append(data)
        if self._open_label and not (self._open_label.ambiguous or self._inactive_tags):
            self._open_label.text.append(data)


def _label_text(value: str) -> str:
    return " ".join(re.sub(r"[\u200b\u00ad]", "", value).split())


def extract_form_fields(html: str) -> list[FormField]:
    """Return verified labels, identifier hints and native select evidence in markup order."""
    markup = _FormMarkup()
    try:
        markup.feed(html)
        markup.close()
    except AssertionError, ValueError:
        return []
    field_by_id = {identifier: index for index, attrs in enumerate(markup.fields) if (identifier := attrs.get("id")) and markup.ids[identifier] == 1}
    native_names: dict[int, set[str]] = defaultdict(set)
    for label in markup.labels:
        if label.ambiguous:
            continue
        target = label.wrapped_field if label.target_id is None else field_by_id.get(label.target_id)
        if target is None or label.wrapped_field not in (None, target):
            continue
        if name := _label_text("".join(label.text)):
            native_names[target].add(name)

    names_by_field: list[set[str]] = []
    for index, attrs in enumerate(markup.fields):
        aria_name = _label_text(attrs.get("aria-label", ""))
        # ID-reference names require a separate association and text check.
        if attrs.get("aria-labelledby", "").strip():
            names_by_field.append(set())
        else:
            names_by_field.append({aria_name} if aria_name else native_names[index])
    name_counts = Counter(name for names in names_by_field for name in names)

    fields = []
    for index, (attrs, names) in enumerate(zip(markup.fields, names_by_field, strict=True)):
        name = next(iter(names)) if len(names) == 1 else ""
        verified = name if name_counts[name] == 1 and "hidden" not in attrs else ""
        hint = _label_text(attrs.get("id") or attrs.get("name") or attrs.get("placeholder") or "")
        if verified or hint:
            fields.append(FormField(verified, hint, markup.selects.get(index) if verified else None))
    return fields
