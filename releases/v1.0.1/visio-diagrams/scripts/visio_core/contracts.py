"""Read logical requests and adapt the supported legacy Geometry JSON."""
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

from .errors import GenerationError

SCHEMA = "visio.diagram/1.0"
KINDS = {"box", "diamond", "ellipse", "terminator", "text", "container"}
SIDES = ("right", "bottom", "left", "top")
STRATEGIES = ("auto", "g4-grid", "layered", "positioned")
INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def reject(code, message, path, **details):
    raise GenerationError(code, message, path, **details)


def require_keys(value, allowed, path):
    if not isinstance(value, dict):
        reject("INVALID_OBJECT", "Expected an object", path)
    for key in value:
        if key not in allowed:
            reject("UNKNOWN_FIELD", f"Unrecognized field {key!r}", f"{path}.{key}")


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def check_number(value, path, *, minimum=0, positive=False):
    if not number(value) or value < minimum or (positive and value == minimum):
        reject("INVALID_NUMBER", f"Expected a finite number {'greater than' if positive else 'at least'} {minimum}", path)


def check_text(value, path, *, identifier=False):
    if not isinstance(value, str) or INVALID_XML.search(value):
        reject("INVALID_TEXT", "Expected text valid in VSDX XML", path)
    if identifier and (not value.strip() or any(c.isspace() for c in value)):
        reject("INVALID_ID", "Expected a nonempty identifier without whitespace", path)


def check_bool(value, path):
    if type(value) is not bool:
        reject("INVALID_BOOLEAN", "Expected true or false", path)


def check_box(box, path):
    if not isinstance(box, dict) or set(box) != {"x", "y", "w", "h"}:
        reject("INVALID_RECTANGLE", "Expected x, y, w, h in inches", path)
    for key, value in box.items():
        check_number(value, f"{path}.{key}", positive=key in ("w", "h"))


def check_route(points, path):
    if not isinstance(points, list) or len(points) < 2:
        reject("INVALID_ROUTE", "Expected at least two route vertices", path)
    for index, pair in enumerate(points):
        if not isinstance(pair, list) or len(pair) != 2:
            reject("INVALID_ROUTE_VERTEX", "Expected [x, y] in inches", f"{path}[{index}]")
        for value in pair:
            check_number(value, f"{path}[{index}]")


def load(description, *, identity=None):
    if isinstance(description, Mapping):
        data = deepcopy(dict(description))
        if identity is not None:
            encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            identity.update(inputSha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                            inputEncoding="canonical-json")
        return data
    path = Path(description)

    def object_from_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                reject("DUPLICATE_FIELD", f"Duplicate JSON field {key!r}", path)
            result[key] = value
        return result

    try:
        raw = path.read_bytes()
        if identity is not None:
            identity.update(inputSha256=hashlib.sha256(raw).hexdigest(), inputEncoding="file-bytes")
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=object_from_pairs)
    except (json.JSONDecodeError, UnicodeError) as error:
        reject("INVALID_JSON", str(error), path)
    except OSError as error:
        reject("INPUT_UNREADABLE", str(error), path)


def normalize(description, *, legacy_com=False):
    if not isinstance(description, dict):
        reject("INVALID_OBJECT", "Diagram must be an object", "$")
    uses_legacy = "schema" not in description
    if not uses_legacy:
        if description["schema"] != SCHEMA:
            reject("UNKNOWN_SCHEMA", f"Expected {SCHEMA}", "$.schema")
        require_keys(description, {"schema", "page", "layout", "nodes", "edges"}, "$")
    if legacy_com and not uses_legacy:
        reject("LEGACY_BACKEND_SCHEMA", "The explicit COM compatibility backend requires legacy Geometry JSON", "$.schema")

    layout = description.get("layout", {}) if not uses_legacy else {"mode": "positioned"}
    require_keys(layout, {"mode", "direction", "strategy", "groups", "parallel", "order"}
                 if isinstance(layout, dict) and layout.get("mode") == "intent"
                 else {"mode", "direction", "strategy"}, "$.layout")
    mode = layout.get("mode", "auto")
    direction = layout.get("direction", "down")
    strategy = layout.get("strategy", "auto")
    for key, value, choices in (
        ("mode", mode, ("auto", "positioned", "intent")),
        ("direction", direction, ("down", "right")),
        ("strategy", strategy, STRATEGIES),
    ):
        if value not in choices:
            reject("INVALID_LAYOUT", f"Expected one of {', '.join(choices)}", f"$.layout.{key}")
    layout = {**layout, "mode": mode, "direction": direction, "strategy": strategy}
    if mode == "intent" and strategy != "auto":
        reject("INVALID_LAYOUT", "Intent layout chooses its own placement candidates", "$.layout.strategy")

    page = description.get("page", {})
    if not uses_legacy:
        require_keys(page, {"width", "height", "title", "titleFontSize", "colorFill"}, "$.page")
    if not isinstance(page, dict):
        reject("INVALID_OBJECT", "Expected an object", "$.page")
    for key in ("width", "height"):
        if key in page or uses_legacy:
            check_number(page.get(key), f"$.page.{key}", positive=True)
    check_text(page.get("title", ""), "$.page.title")
    check_number(page.get("titleFontSize", 16), "$.page.titleFontSize", positive=True)
    check_bool(page.get("colorFill", True), "$.page.colorFill")
    if "stencilPath" in page:
        check_text(page["stencilPath"], "$.page.stencilPath")

    nodes, edges = description.get("nodes"), description.get("edges")
    if not isinstance(nodes, list) or not nodes:
        reject("INVALID_NODES", "Expected a nonempty array of nodes", "$.nodes")
    if not isinstance(edges, list):
        reject("INVALID_EDGES", "Expected an array of edges", "$.edges")
    node_ids = {}
    for index, node in enumerate(nodes):
        path = f"$.nodes[{index}]"
        if not uses_legacy:
            require_keys(node, {"id", "kind", "text", "parent", "fill", "fontSize", "bold", "dashed",
                                "master", "position", "size", "layer", "padding", "headerHeight"}
                         | ({"detail", "detailFontSize", "draft"} if mode == "intent" else set()), path)
        if not isinstance(node, dict):
            reject("INVALID_OBJECT", "Expected a node object", path)
        node_id = node.get("id")
        check_text(node_id, f"{path}.id", identifier=not uses_legacy)
        if not node_id or node_id in node_ids:
            reject("DUPLICATE_NODE", f"Empty or duplicate node ID {node_id!r}", f"{path}.id")
        node_ids[node_id] = node
        kind = node.setdefault("kind", "box")
        if not isinstance(kind, str) or kind not in KINDS:
            reject("UNKNOWN_SHAPE", f"Unknown shape kind {kind!r}", f"{path}.kind")
        check_style(node, path)
        fill = node.get("fill", "#FFFFFF")
        if fill is not None and (not isinstance(fill, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", fill)):
            reject("INVALID_FILL", "Expected #RRGGBB", f"{path}.fill")
        for key in ("parent", "master"):
            if node.get(key) is not None:
                check_text(node[key], f"{path}.{key}", identifier=key == "parent" and not uses_legacy)
        if "padding" in node or "headerHeight" in node:
            if kind != "container":
                reject("INVALID_CONTAINER_OPTION", "Padding and header height apply to containers", path)
            for key in ("padding", "headerHeight"):
                if key in node:
                    check_number(node[key], f"{path}.{key}", positive=True)
        if uses_legacy:
            if kind != "container" or legacy_com and not description.get("localLayout"):
                for key in ("x", "y", "w", "h"):
                    check_number(node.get(key), f"{path}.{key}", positive=key in ("w", "h"))
            if "fixed" in node:
                check_bool(node["fixed"], f"{path}.fixed")
        else:
            check_node_constraints(node, path, mode)
            if mode == "intent":
                check_intent_node(node, path)

    if mode == "intent":
        check_layout_intent(layout, node_ids)

    for index, node in enumerate(nodes):
        parent = node.get("parent")
        if parent is not None and (parent not in node_ids or node_ids[parent]["kind"] != "container"):
            reject("INVALID_PARENT", f"Parent {parent!r} must reference a container", f"$.nodes[{index}].parent")
    if legacy_com and any(node.get("parent") is not None for node in nodes):
        reject("LEGACY_COM_CONTAINMENT", "Use the Python backend to preserve explicit native container membership", "$.nodes")

    edge_ids = set()
    for index, edge in enumerate(edges):
        path = f"$.edges[{index}]"
        if not uses_legacy:
            require_keys(edge, {"id", "from", "to", "text", "fontSize", "dashed", "fromPort", "toPort",
                                "route", "label"} | ({"role"} if mode == "intent" else set()), path)
        if not isinstance(edge, dict):
            reject("INVALID_OBJECT", "Expected an edge object", path)
        edge_id = edge.setdefault("id", f"edge-{index + 1}")
        check_text(edge_id, f"{path}.id", identifier=True)
        if edge_id in edge_ids:
            reject("DUPLICATE_EDGE", f"Duplicate edge ID {edge_id!r}", f"{path}.id")
        edge_ids.add(edge_id)
        if mode == "intent" and edge.setdefault("role", "main") not in ("main", "auxiliary", "feedback"):
            reject("INVALID_EDGE_ROLE", "Expected main, auxiliary or feedback", f"{path}.role", edgeId=edge_id)
        check_style(edge, path, default_font=10)
        for end in ("from", "to"):
            node_id = edge.get(end)
            if not isinstance(node_id, str) or node_id not in node_ids:
                reject("UNKNOWN_ENDPOINT", f"Edge {edge_id!r} references unknown node {node_id!r}", f"{path}.{end}", edgeId=edge_id)
            if not uses_legacy and node_ids[node_id]["kind"] in ("container", "text"):
                reject("INVALID_ENDPOINT_SHAPE", f"Edge {edge_id!r} needs a connectable shape", f"{path}.{end}")
            if edge.get(end + "Port") not in (None, *SIDES):
                reject("INVALID_PORT", f"Expected one of {', '.join(SIDES)}", f"{path}.{end}Port")
            if edge.get(end + "Port") is None:
                edge.pop(end + "Port", None)
            fraction = edge.get(end + "Fraction", .5)
            if not number(fraction) or not 0 <= fraction <= 1:
                reject("INVALID_PORT_FRACTION", "Expected a fraction from 0 to 1", f"{path}.{end}Fraction")
        for key in ("route",) if not uses_legacy else ("fixedRoute", "routePoints"):
            if edge.get(key) is not None:
                if not uses_legacy and (mode != "positioned" or not edge.get("fromPort") or not edge.get("toPort")):
                    reject("INVALID_FIXED_ROUTE", "Explicit routes need positioned mode and both port sides", f"{path}.{key}")
                check_route(edge[key], f"{path}.{key}")
        if edge.get("label") is not None:
            if not uses_legacy and mode != "positioned":
                reject("INVALID_LABEL_CONSTRAINT", "Explicit label rectangles need positioned mode", f"{path}.label")
            if not uses_legacy and not edge.get("text"):
                reject("INVALID_LABEL_CONSTRAINT", "Explicit label rectangles need label text", f"{path}.label")
            check_box(edge["label"], f"{path}.label")

    description["page"] = page
    return description, layout, uses_legacy


def check_style(item, path, *, default_font=11):
    check_text(item.get("text", ""), f"{path}.text")
    check_number(item.get("fontSize", default_font), f"{path}.fontSize", positive=True)
    for key in ("bold", "dashed"):
        if key in item:
            check_bool(item[key], f"{path}.{key}")


def check_geometry(node, path):
    for field, axes, positive in (("position", {"x", "y"}, False), ("size", {"width", "height"}, True)):
        if field in node:
            value = node[field]
            require_keys(value, axes, f"{path}.{field}")
            for axis in sorted(axes):
                check_number(value.get(axis), f"{path}.{field}.{axis}", positive=positive)


def check_node_constraints(node, path, mode):
    check_geometry(node, path)
    if node["kind"] == "container":
        if {"position", "size", "layer"} & node.keys():
            reject("DERIVED_CONTAINER", "Container geometry is derived from explicit members", path)
    elif mode == "auto":
        if "position" in node:
            reject("POSITION_REQUIRES_POSITIONED_MODE", "Fixed coordinates require positioned mode", f"{path}.position")
    elif mode == "positioned" and "position" not in node:
        reject("POSITION_REQUIRED", "Positioned mode needs each leaf position", f"{path}.position")
    if "layer" in node and (mode != "auto" or type(node["layer"]) is not int or node["layer"] < 0 or node["layer"] >= 40):
        reject("INVALID_LAYER", "Auto layout accepts integer layer hints from 0 through 39", f"{path}.layer")


def check_intent_node(node, path):
    if "draft" in node:
        draft = node["draft"]
        require_keys(draft, {"position", "size"}, path + ".draft")
        if not draft or node["kind"] == "container":
            reject("INVALID_DRAFT", "A leaf draft needs a position or size", path + ".draft")
        check_geometry(draft, path + ".draft")
    if "detail" in node or "detailFontSize" in node:
        check_text(node.get("detail"), path + ".detail")
        if not node["detail"].strip() or not node.get("text", "").strip() or node["kind"] != "box":
            reject("INVALID_DETAIL", "Detail requires a titled box and nonempty body", path + ".detail")
        check_number(node.get("detailFontSize", 10), path + ".detailFontSize", positive=True)
        node.setdefault("bold", True)


def check_layout_intent(layout, nodes):
    def array(items, path):
        if not isinstance(items, list):
            reject("INVALID_INTENT", "Expected an array", path)

    def members(ids, path, minimum):
        array(ids, path)
        if len(ids) < minimum:
            reject("INVALID_INTENT", f"Expected at least {minimum} members", path)
        seen = set()
        for i, node_id in enumerate(ids):
            if not isinstance(node_id, str) or node_id not in nodes or nodes[node_id]["kind"] == "container":
                reject("INVALID_INTENT_MEMBER", f"Unknown or non-leaf member {node_id!r}", f"{path}[{i}]")
            if node_id in seen:
                reject("INVALID_INTENT_MEMBER", f"Repeated member {node_id!r}", f"{path}[{i}]")
            seen.add(node_id)
        return seen

    groups = layout.get("groups", [])
    array(groups, "$.layout.groups")
    group_ids, assigned = set(), set()
    for i, group in enumerate(groups):
        path = f"$.layout.groups[{i}]"
        require_keys(group, {"id", "title", "members", "fontSize"}, path)
        check_text(group.get("id"), path + ".id", identifier=True)
        check_text(group.get("title"), path + ".title")
        check_number(group.get("fontSize", 11), path + ".fontSize", positive=True)
        if not group["title"].strip() or group["id"] in group_ids or group["id"] in nodes:
            reject("INVALID_GROUP", "Expected a nonempty title and unique group ID", path)
        group_ids.add(group["id"])
        ids = members(group.get("members"), path + ".members", 1)
        if assigned & ids:
            reject("OVERLAPPING_GROUPS", "Visual groups must have disjoint members", path, members=sorted(assigned & ids))
        assigned.update(ids)
    for key in ("parallel", "order"):
        lists = layout.get(key, [])
        array(lists, "$.layout." + key)
        seen = set()
        for i, ids in enumerate(lists):
            ids = members(ids, f"$.layout.{key}[{i}]", 2)
            if key == "parallel" and ids & seen:
                reject("OVERLAPPING_PARALLEL", "Parallel lists must be disjoint", f"$.layout.{key}[{i}]",
                       members=sorted(ids & seen))
            seen.update(ids)
