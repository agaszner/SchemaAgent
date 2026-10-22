"""Relationship graph: declared FKs are certain edges, inferred ones carry a confidence."""

import networkx as nx


def build_graph(model: dict, candidates: list[dict] = ()) -> nx.MultiDiGraph:
    # Multi: Customer->Employee and Employee->Employee (ReportsTo) must not collapse
    graph = nx.MultiDiGraph()
    graph.add_nodes_from(t["name"] for t in model["tables"])
    for table in model["tables"]:
        for fk in table["foreign_keys"]:
            add_relation(graph, {"table": table["name"], **fk}, source="declared", status="accepted")
    for candidate in candidates:
        add_relation(graph, candidate, source="inferred", status="proposed")
    return graph


def add_relation(graph: nx.MultiDiGraph, rel: dict, source: str, status: str) -> None:
    graph.add_edge(
        rel["table"], rel["ref_table"],
        from_table=rel["table"], from_columns=rel["columns"],
        to_table=rel["ref_table"], to_columns=rel["ref_columns"],
        source=source, status=status, confidence=rel.get("confidence", 1.0),
    )


def find_path(graph: nx.MultiDiGraph, source: str, target: str) -> list[dict] | None:
    """JOIN steps from source to target table, or None if they are not connected.

    Rejected edges are never used. Proposed edges cost more than accepted ones
    so a guessed shortcut does not beat a certain path of similar length.
    """
    usable = nx.Graph()
    usable.add_nodes_from(graph)
    for u, v, edge in graph.edges(data=True):
        if edge["status"] == "rejected":
            continue
        # ponytail: fixed penalty for guessed edges; tune if benchmark shows bad paths
        weight = 1.0 if edge["status"] == "accepted" else 2.0 / edge["confidence"]
        if not usable.has_edge(u, v) or weight < usable[u][v]["weight"]:
            usable.add_edge(u, v, weight=weight, **edge)
    try:
        tables = nx.shortest_path(usable, source, target, weight="weight")
    except nx.NetworkXNoPath:
        return None
    steps = []
    for u, v in zip(tables, tables[1:]):
        edge = dict(usable[u][v])
        del edge["weight"]
        steps.append(edge)
    return steps
