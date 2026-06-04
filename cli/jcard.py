"""The canonical internal contact representation: **jCard (RFC 7095)**.

A jCard is the JSON binding of the vCard data model:

    ["vcard", [ [name, params, type, value...], [name, params, type, value...], ... ]]

Each *property* is an array: a lowercase ``name``, a ``params`` object, a value-``type`` string, and
one or more ``value`` elements. Structured properties (``n``, ``adr``, ``org``) carry a single value
that is itself an array; multi-valued properties (``categories``) carry several value elements.

We model this as our own small wrapper — there is no maintained Python jCard library and none is
needed (RFC 7095 is a mechanical mapping we own; see ``plans/v0.1-implementation-plan.md`` §5). The
``JCard`` here is exactly what gets stored as ``source_records.raw_jcard`` (TEXT) in shared.db, so
``to_json()`` is the on-disk form and ``from_json()`` round-trips it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class JCardProperty:
    """One jCard property: ``[name, params, type, *values]`` per RFC 7095."""

    name: str                      # lowercase property name, e.g. "fn", "email"
    params: dict[str, Any]         # parameter object ({} if none); may include "group"
    type: str                      # value type, e.g. "text", "uri", "date-and-or-time"
    values: list[Any]              # one element (scalar or structured array), or several (multi)

    def to_array(self) -> list[Any]:
        return [self.name, dict(self.params), self.type, *self.values]

    @property
    def value(self) -> Any:
        """The first value element — the common single-valued case."""
        return self.values[0] if self.values else None

    @property
    def group(self) -> str | None:
        return self.params.get("group")


@dataclass
class JCard:
    """An ordered list of jCard properties — the canonical form of one contact."""

    properties: list[JCardProperty] = field(default_factory=list)

    def add(self, name: str, params: dict[str, Any], type: str, *values: Any) -> JCardProperty:
        prop = JCardProperty(name=name.lower(), params=dict(params or {}), type=type, values=list(values))
        self.properties.append(prop)
        return prop

    def get(self, name: str) -> JCardProperty | None:
        name = name.lower()
        return next((p for p in self.properties if p.name == name), None)

    def get_all(self, name: str) -> list[JCardProperty]:
        name = name.lower()
        return [p for p in self.properties if p.name == name]

    def first_value(self, name: str) -> Any:
        prop = self.get(name)
        return prop.value if prop else None

    def to_jcard(self) -> list[Any]:
        """The RFC 7095 array form: ``["vcard", [propertyArrays]]``."""
        return ["vcard", [p.to_array() for p in self.properties]]

    def to_json(self) -> str:
        return json.dumps(self.to_jcard(), ensure_ascii=False)

    @classmethod
    def from_jcard(cls, data: list[Any]) -> "JCard":
        if not (isinstance(data, list) and len(data) == 2 and data[0] == "vcard"):
            raise ValueError("not a jCard array: expected ['vcard', [...]]")
        jc = cls()
        for arr in data[1]:
            name, params, type_, *values = arr
            jc.properties.append(JCardProperty(name=name, params=dict(params), type=type_, values=list(values)))
        return jc

    @classmethod
    def from_json(cls, text: str) -> "JCard":
        return cls.from_jcard(json.loads(text))
