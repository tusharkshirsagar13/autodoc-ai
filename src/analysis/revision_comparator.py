"""Deterministic revision comparison.

Literal additions, removals and modifications are computed with difflib at
section level (sections are matched by heading text with the numbering
removed, so renumbered sections still match). Entity and relationship
changes are computed from the extracted inventories. An LLM is only used,
optionally, to *summarise* this deterministic diff; its output is labelled as
AI inference.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass, field

from src.architecture.entity_extractor import normalize_name
from src.ingestion.pdf_parser import ParsedDocument

_NUMBER_PREFIX = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+")
_IGNORED_ATTRS = {"occurrences"}


def section_key(title: str) -> str:
    return normalize_name(_NUMBER_PREFIX.sub("", title or "")) or "(front matter)"


def sections_with_text(parsed: ParsedDocument) -> "OrderedDict[str, dict]":
    out: OrderedDict[str, dict] = OrderedDict()
    for el in parsed.elements():
        key = section_key(el.section)
        entry = out.setdefault(key, {"title": el.section or "(front matter)", "lines": [], "pages": set()})
        if el.kind == "heading" and el.text == el.section:
            entry["pages"].add(el.page_number)
            continue
        for line in re.split(r"\n|(?<=[.!?])\s+(?=[A-Z])", el.text):
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                entry["lines"].append(line)
        entry["pages"].add(el.page_number)
    return out


@dataclass
class ComparisonResult:
    old_label: str
    new_label: str
    added_sections: list[dict] = field(default_factory=list)
    removed_sections: list[dict] = field(default_factory=list)
    modified_sections: list[dict] = field(default_factory=list)
    unchanged_sections: int = 0
    entity_changes: dict = field(default_factory=dict)
    relationship_changes: dict = field(default_factory=dict)
    affected_dependencies: list[dict] = field(default_factory=list)
    structural_similarity: float = 1.0
    warnings: list[str] = field(default_factory=list)

    def counts(self) -> dict:
        ec = self.entity_changes
        return {
            "sections_added": len(self.added_sections),
            "sections_removed": len(self.removed_sections),
            "sections_modified": len(self.modified_sections),
            "sections_unchanged": self.unchanged_sections,
            "entities_added": sum(len(v["added"]) for v in ec.values()),
            "entities_removed": sum(len(v["removed"]) for v in ec.values()),
            "entities_changed": sum(len(v["changed"]) for v in ec.values()),
            "relationships_added": len(self.relationship_changes.get("added", [])),
            "relationships_removed": len(self.relationship_changes.get("removed", [])),
            "affected_dependencies": len(self.affected_dependencies),
        }

    def summary_text(self) -> str:
        c = self.counts()
        parts = [f"Compared revision {self.old_label} → {self.new_label}.",
                 f"Sections: {c['sections_added']} added, {c['sections_removed']} removed, {c['sections_modified']} modified, {c['sections_unchanged']} unchanged.",
                 f"Entities: {c['entities_added']} added, {c['entities_removed']} removed, {c['entities_changed']} changed.",
                 f"Relationships: {c['relationships_added']} added, {c['relationships_removed']} removed.",
                 f"{c['affected_dependencies']} documented relationship(s) involve changed items."]
        return " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "old_label": self.old_label, "new_label": self.new_label, "counts": self.counts(),
            "added_sections": self.added_sections, "removed_sections": self.removed_sections,
            "modified_sections": self.modified_sections, "unchanged_sections": self.unchanged_sections,
            "entity_changes": self.entity_changes, "relationship_changes": self.relationship_changes,
            "affected_dependencies": self.affected_dependencies,
            "structural_similarity": self.structural_similarity, "warnings": self.warnings,
        }


def compare_sections(old: ParsedDocument, new: ParsedDocument, result: ComparisonResult) -> None:
    a, b = sections_with_text(old), sections_with_text(new)
    union = set(a) | set(b)
    matched = set(a) & set(b)
    result.structural_similarity = round(len(matched) / len(union), 2) if union else 1.0
    for key, entry in b.items():
        if key not in a:
            result.added_sections.append({"title": entry["title"], "pages_new": sorted(entry["pages"]),
                                          "text": "\n".join(entry["lines"])[:3000]})
    for key, entry in a.items():
        if key not in b:
            result.removed_sections.append({"title": entry["title"], "pages_old": sorted(entry["pages"]),
                                            "text": "\n".join(entry["lines"])[:3000]})
    for key in [k for k in b if k in a]:
        old_lines, new_lines = a[key]["lines"], b[key]["lines"]
        if old_lines == new_lines:
            result.unchanged_sections += 1
            continue
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
        added, removed, changed = [], [], []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "insert":
                added.extend(new_lines[j1:j2])
            elif tag == "delete":
                removed.extend(old_lines[i1:i2])
            elif tag == "replace":
                for old_line, new_line in zip(old_lines[i1:i2], new_lines[j1:j2]):
                    changed.append({"old": old_line, "new": new_line})
                if (i2 - i1) > (j2 - j1):
                    removed.extend(old_lines[i1 + (j2 - j1):i2])
                elif (j2 - j1) > (i2 - i1):
                    added.extend(new_lines[j1 + (i2 - i1):j2])
        result.modified_sections.append({
            "title": b[key]["title"], "old_title": a[key]["title"],
            "pages_old": sorted(a[key]["pages"]), "pages_new": sorted(b[key]["pages"]),
            "similarity": round(matcher.ratio(), 2), "added_lines": added, "removed_lines": removed,
            "changed_lines": changed,
        })


def _entity_key(e: dict) -> tuple:
    scoped = normalize_name(e.get("component", "")) if e["entity_type"] in {"port", "responsibility"} else ""
    return (e["entity_type"], normalize_name(e["name"]), scoped)


def _comparable(e: dict) -> dict:
    attrs = {k: v for k, v in (e.get("attributes") or {}).items() if k not in _IGNORED_ATTRS and k != "raw_data_elements"}
    return {"description": (e.get("description") or "").strip(), "component": (e.get("component") or "").strip(),
            "external_id": (e.get("external_id") or "").strip(), **attrs}


def compare_entities(old_entities: list[dict], new_entities: list[dict], result: ComparisonResult) -> None:
    old = {_entity_key(e): e for e in old_entities if e.get("review_status") != "rejected"}
    new = {_entity_key(e): e for e in new_entities if e.get("review_status") != "rejected"}
    changes: dict[str, dict] = {}
    for key in set(old) | set(new):
        etype = key[0]
        bucket = changes.setdefault(etype, {"added": [], "removed": [], "changed": []})
        if key not in old:
            e = new[key]
            bucket["added"].append({"name": e["name"], "component": e.get("component", ""), "page_new": e.get("page_number"),
                                    "description": e.get("description", "")})
        elif key not in new:
            e = old[key]
            bucket["removed"].append({"name": e["name"], "component": e.get("component", ""), "page_old": e.get("page_number"),
                                      "description": e.get("description", "")})
        else:
            a, b = _comparable(old[key]), _comparable(new[key])
            diffs = [{"field": f, "old": a.get(f, ""), "new": b.get(f, "")}
                     for f in sorted(set(a) | set(b)) if a.get(f, "") != b.get(f, "")]
            if diffs:
                bucket["changed"].append({"name": new[key]["name"], "component": new[key].get("component", ""),
                                          "page_old": old[key].get("page_number"), "page_new": new[key].get("page_number"),
                                          "differences": diffs})
    for bucket in changes.values():
        for lst in bucket.values():
            lst.sort(key=lambda x: x["name"].lower())
    result.entity_changes = dict(sorted(changes.items()))


def _rel_key(r: dict) -> tuple:
    return (normalize_name(r["source_name"]), normalize_name(r["target_name"]), r["relation_type"], normalize_name(r.get("via", "")))


def compare_relationships(old_rels: list[dict], new_rels: list[dict], result: ComparisonResult) -> None:
    old = {_rel_key(r): r for r in old_rels if r.get("review_status") != "rejected"}
    new = {_rel_key(r): r for r in new_rels if r.get("review_status") != "rejected"}

    def view(r, page_field):
        return {"source": r["source_name"], "target": r["target_name"], "relation": r["relation_type"],
                "via": r.get("via", ""), page_field: r.get("page_number")}

    result.relationship_changes = {
        "added": [view(new[k], "page_new") for k in new if k not in old],
        "removed": [view(old[k], "page_old") for k in old if k not in new],
        "unchanged": sum(1 for k in new if k in old),
    }


def find_affected_dependencies(new_rels: list[dict], old_rels: list[dict], result: ComparisonResult) -> None:
    touched: dict[str, str] = {}
    for etype, bucket in result.entity_changes.items():
        if etype not in {"component", "signal", "interface", "port"}:
            continue
        for kind in ("added", "removed", "changed"):
            for item in bucket[kind]:
                touched.setdefault(normalize_name(item["name"]), f"{etype} {kind}")
    seen = set()
    for label, rels, page_field in (("new", new_rels, "page_new"), ("old", old_rels, "page_old")):
        for r in rels:
            if r["relation_type"] not in {"depends_on", "sends_to", "calls", "uses", "requires", "provides"}:
                continue
            reasons = [f"{name} ({touched[normalize_name(name)]})"
                       for name in (r["source_name"], r["target_name"], r.get("via", "")) if name and normalize_name(name) in touched]
            key = _rel_key(r)
            if reasons and key not in seen:
                seen.add(key)
                result.affected_dependencies.append({
                    "source": r["source_name"], "target": r["target_name"], "relation": r["relation_type"],
                    "via": r.get("via", ""), "present_in": "both" if key in {_rel_key(x) for x in (old_rels if label == "new" else new_rels)} else label,
                    page_field: r.get("page_number"), "reasons": "; ".join(reasons),
                })


def compare_revisions(old_parsed: ParsedDocument, new_parsed: ParsedDocument, old_entities: list[dict],
                      new_entities: list[dict], old_rels: list[dict], new_rels: list[dict],
                      old_label: str = "old", new_label: str = "new", same_document: bool = True) -> ComparisonResult:
    result = ComparisonResult(old_label=old_label, new_label=new_label)
    compare_sections(old_parsed, new_parsed, result)
    compare_entities(old_entities, new_entities, result)
    compare_relationships(old_rels, new_rels, result)
    find_affected_dependencies(new_rels, old_rels, result)
    if not same_document:
        result.warnings.append("The two revisions belong to different documents. Differences reflect document "
                               "content, not necessarily a change history.")
    if result.structural_similarity < 0.4:
        result.warnings.append(f"Only {int(result.structural_similarity * 100)}% of section headings match between the "
                               "revisions. The documents may be unrelated or restructured, so section-level "
                               "comparison may be incomplete.")
    return result


def impact_findings(result: ComparisonResult) -> list[dict]:
    """Change-impact findings to attach to the newer revision (reviewable, not defects)."""
    out = []
    for dep in result.affected_dependencies:
        key = "|".join([dep["source"], dep["target"], dep["relation"], dep.get("via", ""), result.old_label, result.new_label])
        out.append({
            "fingerprint": hashlib.sha1(("revision_change_impact|" + key.lower()).encode()).hexdigest(),
            "rule_id": "revision_change_impact",
            "category": "Change impact",
            "severity": "Low",
            "summary": f"Documented relationship {dep['source']} → {dep['target']} ({dep['relation'].replace('_', ' ')}) involves items changed between {result.old_label} and {result.new_label}.",
            "explanation": "Changed: " + dep["reasons"] + ". A change does not mean the revision is defective; "
                           "confirm that both sides of the relationship were updated consistently.",
            "evidence": f"Relationship present in: {dep['present_in']} revision(s). Via: {dep.get('via') or '—'}",
            "pages": [p for p in (dep.get("page_new"), dep.get("page_old")) if p],
            "detection_method": "deterministic_rule:revision_change_impact",
            "is_ai_generated": False,
            "evidence_kind": "observed",
        })
    return out


def diff_digest(result: ComparisonResult, max_chars: int = 9000) -> str:
    """Compact text description of the deterministic diff, for optional LLM summarisation."""
    lines = [result.summary_text()]
    for etype, bucket in result.entity_changes.items():
        for kind in ("added", "removed"):
            if bucket[kind]:
                lines.append(f"{etype} {kind}: " + ", ".join(i["name"] for i in bucket[kind][:30]))
        for item in bucket["changed"][:30]:
            diffs = "; ".join(f"{d['field']}: '{d['old']}' -> '{d['new']}'" for d in item["differences"][:4])
            lines.append(f"{etype} changed: {item['name']} ({diffs})")
    for s in result.added_sections[:15]:
        lines.append(f"Section added: {s['title']}")
    for s in result.removed_sections[:15]:
        lines.append(f"Section removed: {s['title']}")
    for s in result.modified_sections[:20]:
        lines.append(f"Section modified: {s['title']} (+{len(s['added_lines'])} / -{len(s['removed_lines'])} / ~{len(s['changed_lines'])} lines)")
    for d in result.affected_dependencies[:20]:
        lines.append(f"Affected relationship: {d['source']} -> {d['target']} ({d['relation']}) because {d['reasons']}")
    return "\n".join(lines)[:max_chars]
