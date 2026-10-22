"""The pyvis relationship graph, styled like the design system's EdgeLegend."""

from html import escape

from pyvis.network import Network

from ui.theme import TOKENS, color
from ui.markup import STATUS_LABEL


def _style(rel: dict) -> tuple[str, int, list[int] | bool]:
    """Colour token, width and dashes for a shown (not rejected) relationship."""
    if rel["status"] == "proposed":
        return "edge-inferred", 2, [6, 4]
    if rel["source"] == "declared":
        return "edge-declared", 2, False
    return "edge-accepted", 3, False


def build_network(model: dict, relationships: list[dict], theme: str) -> Network:
    paper = color("paper-raised", theme)
    net = Network(height="440px", width="100%", directed=True, bgcolor=paper, cdn_resources="remote")
    shown = [r for r in relationships if r["status"] != "rejected"]  # spec: rejected edges are hidden
    names = [t["name"] for t in model["tables"]]
    for rel in shown:  # a declared FK can name a table the model lacks
        names += [n for n in (rel["from_table"], rel["to_table"]) if n not in names]
    for name in names:
        net.add_node(name, shape="box", color={"background": paper, "border": color("line-control", theme)},
                     font={"face": TOKENS["type"]["families"]["mono"], "color": color("ink", theme)})
    for rel in shown:
        token, width, dashes = _style(rel)
        status = STATUS_LABEL[rel["status"]][0]  # proposed shows as suggested
        title = (f"{rel['from_table']}.{'/'.join(rel['from_columns'])} → "
                 f"{rel['to_table']}.{'/'.join(rel['to_columns'])} · {rel['source']} · {status}")
        net.add_edge(rel["from_table"], rel["to_table"], color=color(token, theme), width=width, dashes=dashes,
                     title=escape(title))  # names come from uploaded databases
    return net
