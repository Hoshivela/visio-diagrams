"""Check movement receipts against native shape positions and connector glue."""
from collections import defaultdict
import math
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .package_check import NS, cells, value


def require(condition, message):
    if not condition:
        raise ValueError(message)


def point(raw, label):
    require(isinstance(raw, (list, tuple)) and len(raw) == 2 and
            all(type(v) in (int, float) and math.isfinite(v) for v in raw),
            f"{label} requires two finite coordinates")
    return tuple(raw)


def translated(origin, delta):
    return origin[0] + delta[0], origin[1] + delta[1]


def near(raw, expected, label):
    actual = point(raw, label)
    require(all(abs(a - b) <= .001 for a, b in zip(actual, expected)),
            f"{label}: measured {actual} differs from expected {expected}")


def follows(before, after, delta, label):
    after = point(after, label)
    measured = tuple(b - a for a, b in zip(before, after))
    near(measured, delta, label)
    if any(delta):
        require(any(measured) and sum(a * b for a, b in zip(measured, delta)) > 0,
                f"{label}: no displacement in the recorded direction")


class Drawing:
    def __init__(self, path):
        try:
            with ZipFile(path) as package:
                page = ET.fromstring(package.read("visio/pages/page1.xml"))
        except (BadZipFile, KeyError, ET.ParseError) as error:
            raise ValueError(f"Cannot read drawing geometry from {path}: {error}") from error
        shapes = page.findall("v:Shapes/v:Shape", NS)
        names = {s.get("ID"): s.get("NameU") for s in shapes}
        self.shapes = {s.get("NameU"): cells(s) for s in shapes}
        require(len(names) == len(self.shapes) == len(shapes), f"Duplicate shape in {path}")
        self.members = {}
        for shape in shapes:
            marker = shape.find("v:Section[@N='User']/v:Row[@N='msvStructureType']/v:Cell[@N='Value']", NS)
            if marker is not None and marker.get("V") == "Container":
                self.members[shape.get("NameU")] = set()
        for name, data in self.shapes.items():
            if name and name.startswith("node_") and "Relationships" in data:
                formula = data["Relationships"].get("F", "")
                for parent_id in re.findall(r"DEPENDSON\(4,Sheet\.(\d+)!SheetRef\(\)\)", formula):
                    parent = names.get(parent_id)
                    require(parent in self.members, f"{path}: missing native container for {name}")
                    self.members[parent].add(name)
        self.links = {}
        for row in page.findall("v:Connects/v:Connect", NS):
            key = names[row.attrib["FromSheet"]], row.attrib["FromCell"]
            require(key not in self.links, f"Duplicate native glue for {key} in {path}")
            self.links[key] = names[row.attrib["ToSheet"]], row.attrib["ToCell"]

    def position(self, shape, cell):
        require(shape in self.shapes, f"Missing moved shape or connector {shape!r}")
        data = self.shapes[shape]
        require(all(cell + axis in data for axis in "XY"), f"{shape}: missing {cell} coordinates")
        return point([value(data, cell + axis) for axis in "XY"], f"{shape}.{cell}")

    def incident(self, ids):
        targets = {"node_" + node for node in ids}
        return {key for key, (node, _) in self.links.items() if node in targets}


def affected_nodes(scene, object_id):
    require(isinstance(scene, dict) and isinstance(scene.get("nodes"), list)
            and all(isinstance(n, dict) and isinstance(n.get("id"), str) for n in scene["nodes"]),
            "Move evidence requires a planned scene with identified nodes")
    nodes = {n["id"]: n for n in scene["nodes"]}
    require(isinstance(object_id, str) and object_id in nodes, f"Unknown moved object {object_id!r}")
    return {object_id} | {n["id"] for n in nodes.values() if n.get("parent") == object_id}


def checked_samples(before_keys, after_keys, incident, label):
    require(before_keys == after_keys, f"{label}: before and after endpoint samples differ")
    require(before_keys <= incident, f"{label}: sampled endpoint is not connected to the moved object")
    require(not incident or before_keys, f"{label}: connected moved object has zero endpoint samples")
    return "PASS" if incident else "NOT_APPLICABLE"


def sample_count(row, field, actual, label):
    count = row.get(field)
    require(type(count) is int and count == actual, f"{label}: {field} does not match {actual} endpoint results")


def native_move(move, source, scene):
    require(isinstance(move, dict), "Invalid native move record")
    node = move.get("node")
    ids = affected_nodes(scene, node)
    drawing = Drawing(source)
    require(drawing.members.get("node_" + node, set()) == {"node_" + n for n in ids - {node}},
            f"{node}: native membership differs from the planned scene")
    near(move.get("before"), drawing.position("node_" + node, "Pin"), f"{node} before native move")
    delta = point(move.get("actualNodeDeltaIn"), f"{node} actualNodeDeltaIn")
    require(any(delta), f"{node}: actual native move is zero")
    ends = move.get("endpoints")
    require(isinstance(ends, list), f"{node}: missing native endpoint samples")
    seen = set()
    incident = drawing.incident(ids)
    for row in ends:
        require(isinstance(row, dict), f"{node}: invalid native endpoint row")
        key = edge, cell = row.get("edge"), row.get("endpoint")
        require(cell in ("BeginX", "EndX") and key in incident and key not in seen,
                f"{node}: unrelated or duplicate native endpoint {key}")
        seen.add(key)
        require(row.get("glue") == drawing.links[key][1], f"{node} {key}: native glue differs")
        follows((0, 0), row.get("deltaIn"), delta, f"{node} {key} native endpoint displacement")
        follows(drawing.position(edge, cell[:-1]), row.get("geometryPoint"), delta,
                f"{node} {key} moved geometry")
    status = checked_samples(seen, seen, incident, node)
    if "checkedEndpoints" in move:
        sample_count(move, "checkedEndpoints", len(seen), node)
    require(move.get("endpointStatus", status) == status, f"{node}: conflicting native endpoint status")
    return {"status": status, "node": node, "actualNodeDeltaIn": delta, "endpoints": ends,
            "checkedEndpoints": len(seen)}


def edit_samples(rows, logical_edges, drawing, offsets, ids, label):
    require(isinstance(rows, list), f"{label}: missing endpoint sample array")
    seen = set()
    for row in rows:
        require(isinstance(row, dict), f"{label}: invalid endpoint row")
        edge, side, node = row.get("edge"), row.get("endpoint"), row.get("node")
        require(edge in logical_edges and side in ("from", "to") and node in ids,
                f"{label}: unrelated endpoint {(edge, side, node)}")
        index, spec = logical_edges[edge]
        require(spec[side] == node, f"{label}: endpoint {edge}.{side} does not target {node}")
        line = f"edge_{index}"
        cell = "Begin" if side == "from" else "End"
        key = line, cell + "X"
        require(key not in seen and drawing.links.get(key) == ("node_" + node, row.get("glue")),
                f"{label}: duplicate endpoint or incorrect glue for {edge}.{side}")
        seen.add(key)
        near([row.get("x"), row.get("y")], translated(drawing.position(line, cell), offsets.get(node, (0, 0))),
             f"{label} endpoint {edge}.{side}")
    return seen


def edited_move(actions, samples, source_path, saved_path, scene):
    require(isinstance(samples, list), "Edited movement requires endpointChecks")
    require(all(isinstance(s, dict) and isinstance(s.get("object"), str) for s in samples),
            "Invalid edited movement endpoint check")
    by_object = {s["object"]: s for s in samples}
    require(len(by_object) == len(samples), "Duplicate edited movement endpoint check")
    source, saved = Drawing(source_path), Drawing(saved_path)
    require(isinstance(scene, dict) and isinstance(scene.get("edges"), list)
            and all(isinstance(e, dict) and all(isinstance(e.get(k), str) for k in ("id", "from", "to"))
                    for e in scene["edges"]), "Move evidence requires a planned scene with identified connections")
    logical_edges = {e["id"]: (i, e) for i, e in enumerate(scene["edges"], 1)}
    # 'before' belongs to the individual action. All 'after' readings come from
    # the final reopened document, including subsequent moves of a container.
    offsets = defaultdict(lambda: (0, 0))
    results = []
    for action in actions:
        is_container = action["kind"] == "move-native-container"
        obj = action.get("container" if is_container else "node")
        ids = affected_nodes(scene, obj)
        shape = "node_" + obj
        members = {"node_" + n for n in ids - {obj}}
        require(source.members.get(shape, set()) == saved.members.get(shape, set()) == members,
                f"{obj}: native membership differs from the planned scene")
        if is_container:
            declared = action.get("members")
            require(isinstance(declared, list) and len(declared) == len(members) and set(declared) == members,
                    f"{obj}: container member list differs from planned members")
            require(shape in source.members and shape in saved.members, f"{obj}: missing native container")
        require(obj in by_object, f"{obj}: missing dedicated endpoint check for moved object")
        check = by_object.pop(obj)
        delta = point(action.get("deltaIn"), f"{obj} deltaIn")
        require(any(delta), f"{obj}: recorded edit displacement is zero")
        if "pinBefore" in action or not is_container:
            near(action.get("pinBefore"), translated(source.position("node_" + obj, "Pin"), offsets[obj]),
                 f"{obj} pin before move")
        if "pinAfter" in check:
            near(check["pinAfter"], saved.position("node_" + obj, "Pin"), f"{obj} pin after reopen")
        before = edit_samples(action.get("before"), logical_edges, source, offsets, ids, f"{obj} before")
        after = edit_samples(check.get("after"), logical_edges, saved, {}, ids, f"{obj} after")
        incident = source.incident(ids)
        require(incident == saved.incident(ids), f"{obj}: incident connections changed after edit")
        sample_count(check, "count", len(after), obj)
        status = checked_samples(before, after, incident, obj)
        require(check.get("status", status) == status, f"{obj}: conflicting endpoint check status")
        # Compare both glue and final geometry across the action sequence below.
        for key in after:
            require(source.links[key] == saved.links[key], f"{obj}: glue changed on {key}")
        results.append({**check, "status": status})
        for node in ids:
            offsets[node] = translated(offsets[node], delta)
    require(not by_object, f"Endpoint checks without a corresponding move: {sorted(by_object)}")
    for node, delta in offsets.items():
        name = "node_" + node
        follows(source.position(name, "Pin"), saved.position(name, "Pin"), delta,
                f"{node} saved node displacement")
    for result in results:
        for row in result["after"]:
            index, _ = logical_edges[row["edge"]]
            cell = "Begin" if row["endpoint"] == "from" else "End"
            line = f"edge_{index}"
            follows(source.position(line, cell), saved.position(line, cell), offsets[row["node"]],
                    f"{row['edge']}.{row['endpoint']} endpoint follow")
    total = sum(r["count"] for r in results)
    return {"status": "PASS" if total else "NOT_APPLICABLE", "actions": actions,
            "endpointChecks": results, "checkedEndpoints": total}
