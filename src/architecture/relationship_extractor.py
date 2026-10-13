"""Relationship post-processing.

The raw relationship candidates are produced alongside entities by
`entity_extractor` (dependency tables, port tables, signal tables, prose
patterns and functional-flow chains). This module:

* resolves relationship endpoints against the extracted component inventory,
* derives component-to-component *interface links* from documented port
  tables (A provides IF_X, B requires IF_X  =>  B uses A via IF_X). These are
  labelled `derived_interface_link` so they are never confused with a
  dependency the document states in words or in a dependency table.
"""

from __future__ import annotations

from collections import defaultdict

from src.architecture.entity_extractor import normalize_name


def component_names(entities: list[dict]) -> dict[str, str]:
    return {normalize_name(e["name"]): e["name"] for e in entities
            if e["entity_type"] == "component" and e.get("review_status") != "rejected"
            and not e.get("attributes", {}).get("missing_name")}


def annotate_resolution(relationships: list[dict], entities: list[dict]) -> list[dict]:
    """Add `source_resolved` / `target_resolved` flags to relationship dicts (copy)."""
    comps = component_names(entities)
    interfaces = {normalize_name(e["name"]) for e in entities if e["entity_type"] == "interface"}
    out = []
    for rel in relationships:
        r = dict(rel)
        r["source_resolved"] = normalize_name(r["source_name"]) in comps
        target = normalize_name(r["target_name"])
        r["target_resolved"] = target in comps or (r["relation_type"] in {"provides", "requires"} and target in interfaces)
        out.append(r)
    return out


def derive_interface_links(entities: list[dict]) -> list[dict]:
    """Derive requirer -> provider links from port/interface typing in port tables."""
    providers: dict[str, list[dict]] = defaultdict(list)
    requirers: dict[str, list[dict]] = defaultdict(list)
    for e in entities:
        if e["entity_type"] != "port" or e.get("review_status") == "rejected":
            continue
        attrs = e.get("attributes", {})
        iface = attrs.get("interface", "")
        direction = (attrs.get("direction", "") or "").lower()
        if not iface or not e.get("component"):
            continue
        if direction.startswith(("p", "out", "sender", "server")):
            providers[normalize_name(iface)].append(e)
        elif direction.startswith(("r", "in", "receiver", "client")):
            requirers[normalize_name(iface)].append(e)
    links = []
    for iface, req_ports in requirers.items():
        for req in req_ports:
            for prov in providers.get(iface, []):
                if normalize_name(req["component"]) == normalize_name(prov["component"]):
                    continue
                links.append({
                    "id": f"derived_{req['id']}_{prov['id']}",
                    "source_name": req["component"], "target_name": prov["component"],
                    "relation_type": "uses", "via": req["attributes"].get("interface", ""),
                    "description": f"{req['name']} (required) is typed by the same interface as {prov['name']} (provided)",
                    "page_number": req.get("page_number"), "section": req.get("section", ""),
                    "evidence": f"{req.get('evidence', '')} || {prov.get('evidence', '')}",
                    "extraction_method": "derived_interface_link", "confidence": "medium",
                    "review_status": "unreviewed",
                })
    return links
