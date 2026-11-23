"""Compare stable authoring IDs across inputs or a resolved drawing scene."""


def logical_text(value):
    return (value or "").replace("\r", "").replace("\n", "")


def entries(diagram):
    layout = diagram.get("layout") or {}
    groups = diagram.get("groups", layout.get("groups", []))
    page = diagram.get("page", {})
    yield ("page", ""), page, {"title": page.get("title", "")}
    for node in diagram["nodes"]:
        yield ("node", node["id"]), node, {
            "kind": node.get("kind", "box"), "parent": node.get("parent"),
            "text": node.get("text", ""), "detail": node.get("detail", ""),
        }
    for i, edge in enumerate(diagram["edges"], 1):
        yield ("edge", edge.get("id", f"edge-{i}")), edge, {
            "from": edge["from"], "to": edge["to"], "text": edge.get("text", ""),
            "role": edge.get("role"), "dashed": edge.get("dashed", False),
        }
    for group in groups:
        yield ("group", group["id"]), group, {
            "title": group.get("title", group.get("text", "")),
            "members": sorted(group["members"]),
        }


def snapshot(diagram):
    business, geometry = {}, {}
    for key, raw, fields in entries(diagram):
        if key in business:
            raise ValueError(f"Duplicate identity {key!r}")
        business[key] = dict(fields)
        geometry[key] = {k: raw[k] for k in (
            "position", "size", "draft", "x", "y", "w", "h", "width", "height", "label", "route",
            "routePoints", "fromPort", "toPort", "fontSize", "detailFontSize", "layer") if k in raw}
        if key[0] == "page":
            layout = diagram.get("layout") or {}
            geometry[key].update({k: layout[k] for k in ("mode", "strategy", "direction", "order", "parallel")
                                  if k in layout})
        for name in ("text", "detail", "title"):
            if name in fields:
                business[key][name] = logical_text(fields[name])
                if "\n" in fields[name] or "\r" in fields[name]:
                    geometry[key][name + "Lines"] = fields[name].splitlines()
    return business, geometry


def changes(before, after):
    changed = []
    for kind, identity in sorted(set(before) | set(after)):
        key = kind, identity
        if before.get(key) != after.get(key):
            changed.append({"type": kind, "id": identity,
                            "before": before.get(key), "after": after.get(key)})
    return changed


def requirements(mapping):
    rows = {}
    for row in (mapping or {}).get("requirements", []):
        rid = row["id"]
        if not rid or rid in rows or not isinstance(row["objects"], list) or not row["objects"]:
            raise ValueError(f"Invalid requirement mapping {rid!r}")
        rows[rid] = sorted(set(row["objects"]))
    return rows


def compare(before, after, *, mapping=None, previous_mapping=None):
    initial, initial_geometry = snapshot(before)
    final, final_geometry = snapshot(after)
    business = changes(initial, final)
    new_map = requirements(mapping)
    old_map = requirements(previous_mapping) if previous_mapping is not None else new_map
    map_changes = changes({("requirement", k): v for k, v in old_map.items()},
                          {("requirement", k): v for k, v in new_map.items()})
    present = {k for kind, k in final if kind != "page"}
    missing = {rid: sorted(set(ids) - present) for rid, ids in new_map.items() if set(ids) - present}
    return {"businessChanges": business, "layoutChanges": changes(initial_geometry, final_geometry),
            "mappingChanges": map_changes, "missingMappedObjects": missing,
            "comparison": "CHANGED" if business or map_changes or missing else "UNCHANGED",
            "semanticReview": "NOT_RUN"}
