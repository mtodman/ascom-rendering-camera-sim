"""Generic helpers for rendering a pydantic BaseModel as an HTML form and
parsing a submitted form back into a validated instance of that model.
Used by the web setup UI so every config field is editable without hand
writing a template field per settings option.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError


def _humanize(name: str) -> str:
    words = name.replace("_", " ").split(" ")
    small = {"id", "url", "ra", "dec", "ccd", "adu", "fwhm", "px", "um", "mm", "s", "e"}
    out = []
    for w in words:
        if w.lower() in small:
            out.append(w.upper())
        else:
            out.append(w.capitalize())
    return " ".join(out)


@dataclass
class FormField:
    name: str
    label: str
    input_type: str  # "checkbox" | "number" | "text"
    step: str | None
    value: Any


def describe_fields(model: BaseModel) -> list[FormField]:
    fields = []
    for name, info in type(model).model_fields.items():
        value = getattr(model, name)
        annotation = info.annotation
        if annotation is bool:
            input_type = "checkbox"
            step = None
        elif annotation is int:
            input_type = "number"
            step = "1"
        elif annotation is float:
            input_type = "number"
            step = "any"
        else:
            input_type = "text"
            step = None
        fields.append(FormField(name=name, label=_humanize(name), input_type=input_type, step=step, value=value))
    return fields


def parse_form_to_model(model_cls: type[BaseModel], form: dict[str, str]) -> tuple[BaseModel | None, list[str]]:
    """Builds a validated instance of model_cls from submitted form data.
    Bool fields are treated as HTML checkboxes: an unchecked checkbox simply
    isn't submitted by the browser, so its absence means False. Every other
    field is parsed from its submitted string value.
    """
    values: dict[str, Any] = {}
    errors: list[str] = []
    for name, info in model_cls.model_fields.items():
        annotation = info.annotation
        if annotation is bool:
            values[name] = form.get(name) in ("on", "true", "1", "True")
            continue
        if name not in form:
            continue
        raw = form[name]
        try:
            if annotation is int:
                values[name] = int(raw)
            elif annotation is float:
                values[name] = float(raw)
            else:
                values[name] = raw
        except ValueError:
            errors.append(f"{_humanize(name)}: invalid value '{raw}'")

    if errors:
        return None, errors

    try:
        return model_cls(**values), []
    except ValidationError as exc:
        for e in exc.errors():
            loc = ".".join(str(p) for p in e["loc"])
            errors.append(f"{_humanize(loc)}: {e['msg']}")
        return None, errors
