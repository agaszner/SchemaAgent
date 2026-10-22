"""Design tokens from app/design: CSS variables for st.html and hex values for Python (graph colours)."""

import json
import re
from pathlib import Path

import streamlit as st

DESIGN = Path(__file__).parent / "design"
TOKENS: dict = json.loads((DESIGN / "tokens.json").read_text())
THEMES = [t["id"] for t in TOKENS["color"]["themes"]]
ALIAS = re.compile(r"^\{(.+)\}$")


def _value(token: dict, theme: str) -> str:
    value = token["value"]
    if isinstance(value, str):
        return value
    return value.get(theme, value[THEMES[0]])  # a missing theme inherits the first theme


def color(name: str, theme: str = "light") -> str:
    """Hex value of a colour token, following {alias} values."""
    token = next(t for t in TOKENS["color"]["tokens"] if t["name"] == name)
    value = _value(token, theme)
    alias = ALIAS.match(value)
    return color(alias[1], theme) if alias else value


def css() -> str:
    """bundle.css (its @import must stay the first rule), then the token variables."""
    blocks = [(DESIGN / "bundle.css").read_text()]
    for i, theme in enumerate(THEMES):
        selector = f':root, [data-theme="{theme}"]' if i == 0 else f'[data-theme="{theme}"]'
        lines = [
            f"  --{t['name']}: {ALIAS.sub(r'var(--\1)', _value(t, theme))};" for t in TOKENS["color"]["tokens"]
        ]
        blocks.append(selector + " {\n" + "\n".join(lines) + "\n}")
    rest = [*TOKENS["spacing"]["tokens"], *TOKENS["radius"]["tokens"]]
    lines = [f"  --{t['name']}: {t['value']};" for t in rest]
    lines += [f"  --font-{family}: {value};" for family, value in TOKENS["type"]["families"].items()]
    blocks.append(":root {\n" + "\n".join(lines) + "\n}")
    return "\n".join(blocks) + "\n"


def theme_type() -> str:
    """Active Streamlit theme; None under AppTest, so fall back to light."""
    return st.context.theme.type or "light"


def inject() -> None:
    st.html("<style>" + css() + "</style>")
