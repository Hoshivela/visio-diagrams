"""Inspect OPC, cached geometry and native glue independently of the writer."""
from collections import defaultdict
import math
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

from .errors import GenerationError

NS = {"v": "http://schemas.microsoft.com/office/visio/2012/main"}
TOL = 2e-6
MASTERS = {"box": "Process", "diamond": "Decision", "terminator": "Start/End"}


def expect(condition, message, path):
    if not condition:
        raise GenerationError("VSDX_STRUCTURE_MISMATCH", message, path)


def checked_xml(path):
    with zipfile.ZipFile(path) as package:
        names = package.namelist()
        expect(package.testzip() is None and len(names) == len(set(names)), "Corrupt or duplicate package part", path)
        parts = set(names)
        xml = {}
        for name in names:
            if name.endswith((".xml", ".rels")):
                xml[name] = ET.fromstring(package.read(name))
            if name.endswith(".rels"):
                folder = "" if name == "_rels/.rels" else posixpath.dirname(posixpath.dirname(name))
                seen = set()
                for rel in xml[name]:
                    target = rel.get("Target", "")
                    resolved = (target.lstrip("/") if target.startswith("/")
                                else posixpath.normpath(posixpath.join(folder, target)))
                    expect(rel.get("TargetMode") != "External" and resolved in parts
                           and rel.get("Id") not in seen, f"Unresolved or duplicate relationship to {target!r}", name)
                    seen.add(rel.get("Id"))
        return xml


def cells(shape):
    return {cell.get("N"): cell for cell in shape.findall("v:Cell", NS)}


def value(data, name):
    return float(data[name].get("V"))


def close(actual, expected, path):
    expect(abs(actual - expected) <= TOL, f"Expected {expected}, got {actual}", path)


def text_value(shape):
    text = shape.find("v:Text", NS)
    return "".join(text.itertext()).removesuffix("\n") if text is not None else ""


def check_text(shape, expected, size, font, path, *, bold=False):
    expect(text_value(shape) == expected, "VSDX text differs from resolved text", path)
    section = shape.find("v:Section[@N='Character']/v:Row", NS)
    check_run(section, size, font, path, bold)


def check_run(section, size, font, path, bold):
    expect(section is not None, "Character row missing", path)
    char = cells(section)
    close(value(char, "Size") * 72, size, f"{path}.fontSize")
    expect(all(char.get(name) is not None and char[name].get("V") == font for name in ("Font", "AsianFont")),
           "Latin or Asian font value differs from the scene", path)
    want_bold = "1" if bold else "0"
    actual_bold = char["Style"].get("V") if "Style" in char else "0"
    expect(actual_bold == want_bold, "Text weight differs from the scene", path)


def check_frame(data, frame, height, path):
    for key, wanted in (("Width", frame["w"]), ("Height", frame["h"]),
                        ("PinX", frame["x"] + frame["w"] / 2), ("PinY", height - frame["y"] - frame["h"] / 2)):
        close(value(data, key), wanted, f"{path}.{key}")


def check_text_frame(data, frame, page_height, path):
    for name, key in (("TxtWidth", "w"), ("TxtHeight", "h")):
        close(value(data, name), frame[key], f"{path}.{name}")
    angle = value(data, "Angle") if "Angle" in data else 0
    x, y = to_page(data, value(data, "TxtPinX"), value(data, "TxtPinY"), angle)
    close(x, frame["x"] + frame["w"] / 2, f"{path}.textCenterX")
    close(page_height - y, frame["y"] + frame["h"] / 2, f"{path}.textCenterY")


def to_page(frame, x, y, angle):
    lx, ly = x - value(frame, "LocPinX"), y - value(frame, "LocPinY")
    return (value(frame, "PinX") + lx * math.cos(angle) - ly * math.sin(angle),
            value(frame, "PinY") + lx * math.sin(angle) + ly * math.cos(angle))


def check_package(path, scene):
    xml = checked_xml(path)
    metadata = xml["visio/pages/pages.xml"].find("v:Page/v:PageSheet", NS)
    page = cells(metadata)
    height, font = scene["page"]["height"], scene.get("fontFamily", "Microsoft YaHei")
    close(value(page, "PageWidth"), scene["page"]["width"], "PageWidth")
    close(value(page, "PageHeight"), height, "PageHeight")
    names = xml["visio/document.xml"].findall("v:FaceNames/v:FaceName", NS)
    expect(sum(face.get("NameU") == font for face in names) == 1,
           "Expected a unique configured family in the FaceNames table", "FaceNames")

    xml_page = xml["visio/pages/page1.xml"]
    ordered = xml_page.findall("v:Shapes/v:Shape", NS)
    shapes = {shape.get("NameU"): shape for shape in ordered}
    expect(len(shapes) == len(ordered) == len({s.get("ID") for s in ordered}), "Repeated shape ID or name", "PageContents")
    masters = {master.get("ID"): master.get("NameU") for master in xml["visio/masters/masters.xml"]}
    links = defaultdict(list)
    connects = xml_page.findall("v:Connects/v:Connect", NS)
    for link in connects:
        links[link.get("FromSheet"), link.get("FromCell")].append(link)

    checked = set()
    parents = defaultdict(list)
    for node in scene["nodes"]:
        if node.get("parent"):
            parents[node["parent"]].append(node["id"])
    for node in scene["nodes"]:
        name = "node_" + node["id"]
        shape = shapes.get(name)
        expect(shape is not None, "Missing node shape", name)
        checked.add(name)
        data = cells(shape)
        expected_master = node.get("master") or MASTERS.get(node["kind"])
        expect(masters.get(shape.get("Master")) == expected_master, "Unexpected node master", name)
        check_frame(data, node, height, name)
        expected = node.get("text", "")
        if node.get("detail"):
            expected += "\n" + node["detail"]
            row = shape.find("v:Section[@N='Character']/v:Row[@IX='1']", NS)
            check_run(row, node.get("detailFontSize", 10), font, name + ".detail", False)
            close(value(cells(row), "FontScale"), 1, name + ".detail.fontScale")
            parts = shape.findall("v:Text/v:cp", NS)
            expect([part.get("IX") for part in parts] == ["0", "1"]
                   and parts[1].tail == node["detail"] + "\n", "Detail run differs from the scene", name)
        check_text(shape, expected, node.get("fontSize", 11), font, name, bold=node.get("bold", False))
        if node.get("_textFrame"):
            check_text_frame(data, node["_textFrame"], height, name)
        expect(data["FillForegnd"].get("V") == (node.get("fill") or "#FFFFFF"), "Node fill differs from scene", name)
        expect(data["LinePattern"].get("V") == ("0" if node["kind"] == "text" else "2" if node.get("dashed") else "1"),
               "Node line pattern differs from scene", name)
        geometry = shape.find("v:Section[@N='Geometry']", NS)
        rows = geometry.findall("v:Row", NS)
        kind = node["kind"]
        expected_row_count = 1 if kind == "ellipse" else 5
        expect(len(rows) == expected_row_count, "Native shape geometry differs from requested kind", name)
        if kind == "ellipse":
            expect(rows[0].get("T") == "Ellipse", "Native ellipse row is missing", name)
        if kind == "diamond":
            close(value(cells(rows[0]), "Y"), node["h"]/2, f"{name}.diamond")
            close(value(cells(rows[1]), "X"), node["w"]/2, f"{name}.diamond")
        if kind == "terminator":
            close(value(data, "Rounding"), min(node["w"], node["h"])/2, f"{name}.rounding")
        if kind == "container":
            marker = shape.find("v:Section[@N='User']/v:Row[@N='msvStructureType']/v:Cell[@N='Value']", NS)
            expect(marker is not None and marker.get("V") == "Container", "Native container marker is missing", name)
        dependencies = []
        if node.get("parent"):
            parent_id = shapes["node_" + node["parent"]].get("ID")
            dependencies.append(f"DEPENDSON(4,Sheet.{parent_id}!SheetRef())")
        if parents[node["id"]]:
            children = ",".join(f"Sheet.{shapes['node_' + child].get('ID')}!SheetRef()" for child in parents[node["id"]])
            dependencies.append(f"DEPENDSON(1,{children})")
        formula = data["Relationships"].get("F") if "Relationships" in data else None
        want = f"SUM({','.join(dependencies)})" if dependencies else None
        expect(formula == want, "Native membership differs from explicit containment", name)
    for group in scene.get("groups", []):
        name = "group_" + group["id"]
        shape = shapes.get(name)
        expect(shape is not None, "Missing visual group", name)
        group_data = cells(shape)
        check_frame(group_data, group, height, name)
        check_text_frame(group_data, group["_textFrame"], height, name)
        check_text(shape, group["text"], group["fontSize"], font, name, bold=True)
        expect("Relationships" not in group_data, "Unexpected container membership on visual group", name)
        checked.add(name)
    title = scene["page"].get("_title")
    if title:
        name = "diagram_title"
        check_frame(cells(shapes[name]), title, height, name)
        check_text(shapes[name], title["text"], title["fontSize"], font, name, bold=True)
        check_text_frame(cells(shapes[name]), title, height, name)
        checked.add(name)

    for index, edge in enumerate(scene["edges"], 1):
        name = f"edge_{index}"
        connector = shapes.get(name)
        expect(connector is not None and masters.get(connector.get("Master")) == "Dynamic connector",
               "Missing native Dynamic connector master", name)
        data = cells(connector)
        expect(data["EndArrow"].get("V") == "4" and data["BeginArrow"].get("V") == "0"
               and data["LinePattern"].get("V") == ("2" if edge.get("dashed") else "1"),
               "Connector style or direction differs from scene", name)
        checked.add(name)
        route = edge["routePoints"]
        angle = value(data, "Angle")
        for end, cell, y_cell, index_at_end in (("from", "BeginX", "BeginY", 0), ("to", "EndX", "EndY", -1)):
            verify_glue(links[connector.get("ID"), cell], data, shapes["node_" + edge[end]], cell, y_cell,
                        route[index_at_end], height, name)
        rows = [row for row in connector.findall("v:Section[@N='Geometry']/v:Row", NS) if row.get("Del") != "1"]
        expect(len(rows) == len(route), "Connector vertex count differs from scene", name)
        for row, (x, y) in zip(rows, route):
            xy = cells(row)
            page_x, page_y = to_page(data, value(xy, "X"), value(xy, "Y"), angle)
            close(page_x, x, f"{name}.vertex.x")
            close(height - page_y, y, f"{name}.vertex.y")
        if edge.get("label") and edge.get("text"):
            label_name = f"edge_label_{index}"
            label = shapes[label_name]
            check_frame(cells(label), edge["label"], height, label_name)
            check_text(label, edge["text"], edge.get("fontSize", 10), font, label_name, bold=edge.get("bold", False))
            check_text_frame(cells(label), edge["label"], height, label_name)
            check_text(connector, "", edge.get("fontSize", 10), font, name, bold=edge.get("bold", False))
            checked.add(label_name)
        else:
            check_text(connector, edge.get("text", ""), edge.get("fontSize", 10), font, name, bold=edge.get("bold", False))
            if edge.get("_inlineLabel"):
                check_text_frame(data, edge["_inlineLabel"], height, name)
    expect(checked == shapes.keys() and len(connects) == 2 * len(scene["edges"]), "Unexpected shape or glue count", "PageContents")
    return {"status": "PASS", "shapeCount": len(checked), "gluedEndpoints": len(connects),
            "routeVertices": sum(len(edge["routePoints"]) for edge in scene["edges"]),
            "scope": "ZIP, OPC relationships, shape/text/style, container membership, routes and glue"}


def verify_glue(links, data, node, x_cell, y_cell, point, height, path):
    expect(len(links) == 1 and links[0].get("ToSheet") == node.get("ID"),
           "Connector is not glued to its required node", path)
    link = links[0]
    row = re.fullmatch(r"Connections\.X([0-9]+)", link.get("ToCell", ""))
    expect(row is not None and int(link.get("ToPart", "0")) == 99 + int(row[1]),
           "Glue does not identify a native connection point", path)
    number, node_id = int(row[1]), node.get("ID")
    formula = f"PAR(PNT(Sheet.{node_id}!Connections.X{number},Sheet.{node_id}!Connections.Y{number}))"
    expect(all(data[name].get("F") == formula for name in (x_cell, y_cell)), "Endpoint formula does not match glue", path)
    local = node.find(f"v:Section[@N='Connection']/v:Row[@IX='{number - 1}']", NS)
    expect(local is not None, "Native connection row is missing", path)
    x, y = point
    geometry = cells(local)
    frame = cells(node)
    page_x, page_y = to_page(frame, value(geometry, "X"), value(geometry, "Y"), 0)
    for actual, wanted in ((page_x, x), (height - page_y, y),
                           (value(data, x_cell), x), (height - value(data, y_cell), y)):
        close(actual, wanted, path)
