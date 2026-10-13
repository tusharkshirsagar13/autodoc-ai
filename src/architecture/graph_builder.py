"""Build an architecture graph (networkx) and an interactive Plotly figure.

Only relationships extracted from the document (or explicitly derived from
documented port tables, when the user opts in) become edges. Nothing is
added to make the graph look complete.
"""

from __future__ import annotations

import math

import networkx as nx

from src.architecture.entity_extractor import normalize_name

RELATION_COLORS = {
    "depends_on": "#1f4e8c",
    "sends_to": "#3b82c4",
    "calls": "#6b4fa3",
    "uses": "#7a8699",
    "flow_step": "#2f8f6b",
    "provides": "#9aa5b1",
    "requires": "#9aa5b1",
}

COMPONENT_RELATIONS = {"depends_on", "sends_to", "calls", "uses", "flow_step"}


def build_graph(entities: list[dict], relationships: list[dict], relation_types: set[str] | None = None) -> nx.MultiDiGraph:
    relation_types = relation_types or COMPONENT_RELATIONS
    graph = nx.MultiDiGraph()
    components = {normalize_name(e["name"]): e for e in entities
                  if e["entity_type"] == "component" and e.get("review_status") != "rejected"
                  and not e.get("attributes", {}).get("missing_name")}
    for key, comp in components.items():
        graph.add_node(comp["name"], kind="component", documented=True, description=comp.get("description", ""),
                       page=comp.get("page_number"))
    for rel in relationships:
        if rel.get("review_status") == "rejected" or rel["relation_type"] not in relation_types:
            continue
        for end in (rel["source_name"], rel["target_name"]):
            key = normalize_name(end)
            if key in components:
                continue
            if end not in graph:
                graph.add_node(end, kind="unresolved", documented=False, description="Referenced but not defined "
                               "in the extracted component inventory", page=rel.get("page_number"))
        src = components.get(normalize_name(rel["source_name"]), {"name": rel["source_name"]})["name"]
        tgt = components.get(normalize_name(rel["target_name"]), {"name": rel["target_name"]})["name"]
        graph.add_edge(src, tgt, relation=rel["relation_type"], via=rel.get("via", ""), page=rel.get("page_number"),
                       method=rel.get("extraction_method", ""), evidence=rel.get("evidence", "")[:200])
    return graph


def _layout(graph: nx.MultiDiGraph) -> dict:
    """Left-to-right layering in data-flow direction when possible, otherwise Kamada-Kawai.

    'depends_on' edges point from consumer to provider, so they are reversed for
    layering; the drawn arrows keep their documented direction.
    """
    if graph.number_of_nodes() == 0:
        return {}
    flow = nx.DiGraph()
    flow.add_nodes_from(graph.nodes)
    for src, tgt, data in graph.edges(data=True):
        if src == tgt:
            continue
        if data.get("relation") in {"depends_on", "uses", "calls"}:
            flow.add_edge(tgt, src)
        else:
            flow.add_edge(src, tgt)
    try:
        if flow.number_of_edges() and nx.is_directed_acyclic_graph(flow):
            for layer, nodes in enumerate(nx.topological_generations(flow)):
                for node in nodes:
                    flow.nodes[node]["layer"] = layer
            pos = nx.multipartite_layout(flow, subset_key="layer", align="vertical", scale=1.0)
            return {n: (float(x), float(y)) for n, (x, y) in pos.items()}
    except Exception:
        pass
    try:
        return nx.kamada_kawai_layout(nx.Graph(flow))
    except Exception:
        return nx.spring_layout(nx.Graph(flow), seed=42, k=1.6 / math.sqrt(max(flow.number_of_nodes(), 1)))


def plotly_figure(graph: nx.MultiDiGraph):
    import plotly.graph_objects as go

    pos = _layout(graph)
    fig = go.Figure()
    annotations = []
    edge_hover_x, edge_hover_y, edge_hover_text = [], [], []
    # Merge edges with the same source, target and relation into one arrow (e.g. four wheel-speed
    # signals become one 'sends_to' arrow whose hover lists every signal and page).
    grouped: dict[tuple, list[dict]] = {}
    for src, tgt, data in graph.edges(data=True):
        grouped.setdefault((src, tgt, data["relation"]), []).append(data)
    pair_totals: dict[tuple, int] = {}
    for src, tgt, _ in grouped:
        pair = tuple(sorted((src, tgt)))
        pair_totals[pair] = pair_totals.get(pair, 0) + 1
    xs = [p[0] for p in pos.values()] or [0]
    ys = [p[1] for p in pos.values()] or [0]
    span_x, span_y = (max(xs) - min(xs)) or 1.0, (max(ys) - min(ys)) or 1.0
    seen_pairs: dict[tuple, int] = {}
    for (src, tgt, relation), items in grouped.items():
        x0, y0 = pos[src]
        x1, y1 = pos[tgt]
        pair = tuple(sorted((src, tgt)))
        index = seen_pairs.get(pair, 0)
        seen_pairs[pair] = index + 1
        total = pair_totals[pair]
        if total > 1:  # draw opposite/parallel relations side by side
            dx, dy = (x1 - x0) / span_x, (y1 - y0) / span_y
            if (src, tgt) != pair:
                dx, dy = -dx, -dy
            length = math.hypot(dx, dy) or 1.0
            shift = (index - (total - 1) / 2) * 0.028
            ox, oy = -dy / length * shift * span_x, dx / length * shift * span_y
            x0, y0, x1, y1 = x0 + ox, y0 + oy, x1 + ox, y1 + oy
        color = RELATION_COLORS.get(relation, "#7a8699")
        annotations.append(dict(ax=x0, ay=y0, x=x1, y=y1, xref="x", yref="y", axref="x", ayref="y",
                                showarrow=True, arrowhead=3, arrowsize=1.2, arrowwidth=1.8, arrowcolor=color,
                                standoff=16, startstandoff=16, opacity=0.9))
        edge_hover_x.append((x0 + x1) / 2)
        edge_hover_y.append((y0 + y1) / 2)
        vias = sorted({d["via"] for d in items if d.get("via")})
        pages = sorted({d["page"] for d in items if d.get("page")})
        methods = sorted({d.get("method", "") for d in items if d.get("method")})
        via = f"<br>via {', '.join(vias)}" if vias else ""
        page = f"<br>page(s) {', '.join(map(str, pages))}" if pages else ""
        edge_hover_text.append(f"<b>{src} → {tgt}</b><br>{relation.replace('_', ' ')}{via}{page}<br><i>{', '.join(methods)}</i>")
    fig.add_trace(go.Scatter(x=edge_hover_x, y=edge_hover_y, mode="markers", hoverinfo="text",
                             hovertext=edge_hover_text, marker=dict(size=9, color="#c7d2e0", symbol="diamond"),
                             showlegend=False))
    for kind, color, label in (("component", "#1f4e8c", "Documented component"),
                               ("unresolved", "#c2410c", "Referenced, not defined")):
        nodes = [n for n, d in graph.nodes(data=True) if d.get("kind") == kind]
        if not nodes:
            continue
        hover = []
        for n in nodes:
            d = graph.nodes[n]
            page = f"<br>Page {d['page']}" if d.get("page") else ""
            desc = (d.get("description") or "")[:140]
            hover.append(f"<b>{n}</b><br>{desc}{page}")
        fig.add_trace(go.Scatter(
            x=[pos[n][0] for n in nodes], y=[pos[n][1] for n in nodes], mode="markers+text", text=nodes,
            textposition="bottom center", hoverinfo="text", hovertext=hover, name=label,
            marker=dict(size=26, color=color, line=dict(width=2, color="#ffffff"),
                        symbol="circle" if kind == "component" else "diamond-open"),
            textfont=dict(size=12, color="#1b2a41"),
        ))
    legend_names = {"depends_on": "depends on (consumer → provider)", "sends_to": "sends to (data flow)",
                    "calls": "calls", "uses": "uses (derived interface link)", "flow_step": "functional-flow step"}
    for relation in sorted({r for (_, _, r) in grouped}):
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", name=legend_names.get(relation, relation),
                                 line=dict(color=RELATION_COLORS.get(relation, "#7a8699"), width=2)))
    fig.update_layout(annotations=annotations, height=560, margin=dict(l=10, r=10, t=10, b=10),
                      plot_bgcolor="#ffffff", paper_bgcolor="#ffffff", hovermode="closest",
                      legend=dict(orientation="h", y=-0.04),
                      xaxis=dict(visible=False), yaxis=dict(visible=False))
    return fig
