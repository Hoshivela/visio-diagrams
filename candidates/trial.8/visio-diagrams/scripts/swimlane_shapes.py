"""Build native swimlane shapes and their member relationships in a Visio Core VSDX."""
from dataclasses import dataclass
from math import isclose, isfinite, pi
from pathlib import Path
import re
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from visio_core.contracts import check_text
from visio_core.resources import DEFAULT_FONT, stencil_candidates
from visio_core.text_layout import TEXT_MARGIN, WindowsFont, wrap_text


V = "{http://schemas.microsoft.com/office/visio/2012/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
P = "{http://schemas.openxmlformats.org/package/2006/relationships}"
T = "{http://schemas.openxmlformats.org/package/2006/content-types}"
TOLERANCE = 1e-6  # The generator rounds cached ShapeSheet coordinates to ten significant digits.
HEADER_POINTS = 15
DEFAULT_LANE_FILL = "#F4F7FA"
DEFAULT_HEADER_FILL = "#DDE8EF"


@dataclass(frozen=True)
class Rect:
    """Bounds in the input's top-down page coordinates."""
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top


def _stencil(explicit):
    if explicit is not None:
        path = Path(explicit)
        if path.is_file():
            return path
        raise FileNotFoundError(path)
    for basic in stencil_candidates():
        candidate = basic.with_name("XFUNC_M.VSSX")
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Could not locate XFUNC_M.VSSX in the installed Visio stencil folders.")


def _get_number(obj, key):
    value = obj.get(key)
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"Expected a finite number for {key}")
    return value


def _value(shape, name):
    cell = shape.find(f"{V}Cell[@N='{name}']")
    if cell is None:
        raise ValueError(f"Missing {name} on {shape.get('NameU', shape.tag)}")
    return float(cell.get("V"))


def _validate(spec, sheet, nodes):
    if not isinstance(spec, dict) or not isinstance(spec.get("page"), dict):
        raise ValueError("Provide a swimlane specification with page dimensions.")
    width, height = (_get_number(spec["page"], dim) for dim in ("width", "height"))
    if not (isclose(width, _value(sheet, "PageWidth"), abs_tol=TOLERANCE)
            and isclose(height, _value(sheet, "PageHeight"), abs_tol=TOLERANCE)):
        raise ValueError("Swimlane page dimensions differ from the generated page.")
    orientation = spec.get("orientation", "horizontal")
    if orientation not in ("horizontal", "vertical"):
        raise ValueError("orientation must be horizontal or vertical.")
    vertical = orientation == "vertical"
    horizontal_extent = ("left", "headerRight", "right")
    vertical_extent = ("top", "headerBottom", "bottom")
    extent, incompatible = (vertical_extent, horizontal_extent) if vertical else (horizontal_extent, vertical_extent)
    band_keys = ("left", "right") if vertical else ("top", "bottom")
    band_limit = width if vertical else height
    if any(field in spec for field in incompatible):
        raise ValueError(f"Unexpected cross-axis table bounds for {orientation} swimlanes.")
    start, split, end = (_get_number(spec, key) for key in extent)
    if not 0 <= start < split < end <= (height if vertical else width):
        raise ValueError(f"Expected {' < '.join(extent)} within the page.")
    lanes = spec.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise ValueError("Provide at least one swimlane.")

    ids, members = set(), set()
    frames = []
    previous = None
    for lane in lanes:
        if not isinstance(lane, dict):
            raise ValueError("Each swimlane must be an object.")
        key = lane.get("id")
        title = lane.get("title")
        check_text(key, "$.lanes[].id", identifier=True)
        check_text(title, f"lane {key}.title")
        if key.casefold() in ids:
            raise ValueError(f"Duplicate swimlane id: {key!r}")
        ids.add(key.casefold())
        if not title.strip():
            raise ValueError(f"Missing title for swimlane {key}")
        for field in ("fill", "headerFill"):
            if field in lane and not (isinstance(lane[field], str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", lane[field])):
                raise ValueError(f"Invalid {field} in swimlane {key}")
        if any(field in lane for field in (*extent, incompatible[1])):
            raise ValueError(f"Unexpected table bounds on individual swimlane {key}.")
        low, high = (_get_number(lane, field) for field in band_keys)
        if not 0 <= low < high <= band_limit or (previous is not None and abs(low - previous) > TOLERANCE):
            raise ValueError(f"Swimlane {key} must be on the page and adjacent to the preceding lane.")
        previous = high
        if vertical:
            frame = Rect(low, start, high, end)
            header = Rect(low, start, high, split)
            content = Rect(low, split, high, end)
        else:
            frame = Rect(start, low, end, high)
            header = Rect(start, low, split, high)
            content = Rect(split, low, end, high)
        frames.append((frame, header))
        if not isinstance(lane.get("members"), list):
            raise ValueError(f"Expected a list of members in swimlane {key}")
        for name in lane["members"]:
            if not isinstance(name, str) or name not in nodes or name in members:
                raise ValueError(f"Unknown or repeated member {name!r} in swimlane {key}")
            shape = nodes[name]
            if shape.find(f"{V}Cell[@N='Relationships']") is not None:
                raise ValueError(f"Member {name} already has native container relationships.")
            x = _value(shape, "PinX") - _value(shape, "LocPinX")
            y = height - (_value(shape, "PinY") - _value(shape, "LocPinY") + _value(shape, "Height"))
            if (x < content.left - TOLERANCE or y < content.top - TOLERANCE or
                    x + _value(shape, "Width") > content.right + TOLERANCE or
                    y + _value(shape, "Height") > content.bottom + TOLERANCE):
                raise ValueError(f"Member {name} extends outside the content area of swimlane {key}.")
            members.add(name)
    if nodes.keys() != members:
        raise ValueError(f"Missing swimlane for nodes: {', '.join(sorted(nodes.keys() - members))}")
    return vertical, frames


def _element(parent, tag, **attrs):
    return ET.SubElement(parent, V + tag, {k: str(v) for k, v in attrs.items()})


def _cell(parent, name, value, formula=None):
    item = parent.find(f"{V}Cell[@N='{name}']")
    if item is None:
        item = ET.Element(V + "Cell", N=name)
        pos = next((i for i, child in enumerate(parent) if child.tag != V + "Cell"), len(parent))
        parent.insert(pos, item)
    item.set("V", str(value))
    if formula:
        item.set("F", formula)
    else:
        item.attrib.pop("F", None)
    return item


def _cells(parent, values, formula=None):
    for name, value in values.items():
        _cell(parent, name, value, formula)


def _prop(shape, name, value, formula=None):
    section = shape.find(V + 'Section[@N="User"]')
    if section is None:
        section = _element(shape, "Section", N="User")
    return _cell(_element(section, "Row", N=name), "Value", value, formula)


def _related(shape, *groups):
    terms = []
    for category, ids in groups:
        if ids:
            references = ",".join(f"Sheet.{i}!SheetRef()" for i in ids)
            terms.append(f"DEPENDSON({category},{references})")
    _cell(shape, "Relationships", 0, "SUM(" + ",".join(terms) + ")")


def _rectangle(shape, width, height):
    # Update the cached vertices as well as their formulas: Visio draws the
    # inherited master's old rectangle on first open if only its size changes.
    section = _element(shape, "Section", N="Geometry", IX=0)
    for ix, (x, y) in enumerate(((0, 0), (1, 0), (1, 1), (0, 1), (0, 0)), 1):
        point = _element(section, "Row", IX=ix, T="MoveTo" if ix == 1 else "LineTo")
        _cell(point, "X", x * width, f"Width*{x}")
        _cell(point, "Y", y * height, f"Height*{y}")


def _masters(stencil, masters, rels, types, entries, lane_master):
    imported = {}
    next_id = max(int(m.get("ID")) for m in masters.findall(V + "Master")) + 1
    index = ET.fromstring(stencil.read("visio/masters/masters.xml"))
    connections = ET.fromstring(stencil.read("visio/masters/_rels/masters.xml.rels"))
    children = {}
    for name in (lane_master, "Swimlane List"):
        master = index.find(f"{V}Master[@NameU='{name}']")
        if master is None:
            raise ValueError(f"Missing {name} master in {stencil.filename}")
        link = master.find(V + "Rel")
        rel = connections.find(f"{P}Relationship[@Id='{link.get(R + 'id')}']")
        part = stencil.read("visio/masters/" + rel.get("Target"))
        if name == lane_master:
            instance = ET.fromstring(part).find(f"{V}Shapes/{V}Shape/{V}Shapes")
            for child in instance.findall(V + "Shape"):
                kind = child.find(f"{V}Section[@N='User']/{V}Row[@N='msvStructureType']/{V}Cell[@N='Value']")
                children["heading" if kind is not None and kind.get("V") == "Heading" else "body"] = child.get("ID")
            if children.keys() != {"body", "heading"} or len(instance.findall(V + "Shape")) != 2:
                raise ValueError(f"Unsupported {name} master geometry.")
        target = f"master{next_id}.xml"
        relation_id = f"rIdLane{next_id}"
        link.set(R + "id", relation_id)
        master.set("ID", str(next_id))
        masters.append(master)
        entries["visio/masters/" + target] = part
        ET.SubElement(rels, P + "Relationship", Id=relation_id, Type=rel.get("Type"), Target=target)
        ET.SubElement(types, T + "Override", PartName="/visio/masters/" + target,
                      ContentType="application/vnd.ms-visio.master+xml")
        imported[name] = next_id
        next_id += 1
    return imported, children


def _guid():
    return "{" + str(uuid4()).upper() + "}"


def _shape(shape_id, name, kind, master):
    return ET.Element(V + "Shape", ID=str(shape_id), NameU=name, Name=name, IsCustomNameU="1",
                      IsCustomName="1", Type=kind, Master=str(master), UniqueID=_guid())


def _appearance(lane, item, ids, list_id, frame, heading, vertical, title, family):
    sub = _element(lane, "Shapes")
    index = int(lane.get("ID"))
    body = _element(sub, "Shape", ID=index+1, Type="Shape", MasterShape=ids["body"], UniqueID=_guid())
    header = _element(sub, "Shape", ID=index+2, Type="Shape", MasterShape=ids["heading"], UniqueID=_guid())
    width, height = frame.width, frame.height
    _cells(body, {"PinX": width/2, "PinY": height/2, "Width": width, "Height": height,
                  "LocPinX": width/2, "LocPinY": height/2}, "Inh")
    # Both masters place the heading at the upper left. Only the side heading
    # uses a rotated shape and counter-rotated text.
    local_width, thickness = (heading.width, heading.height) if vertical else (heading.height, heading.width)
    angle = 0 if vertical else pi/2
    _cells(header, {"PinX": heading.width/2, "PinY": height-heading.height/2, "Width": local_width,
                    "LocPinX": local_width/2, "LocPinY": thickness/2, "Angle": angle,
                    "TxtPinX": local_width/2, "TxtPinY": thickness/2,
                    "TxtWidth": heading.width, "TxtHeight": heading.height, "TxtAngle": -angle,
                    "TxtLocPinX": heading.width/2, "TxtLocPinY": heading.height/2}, "Inh")
    _cell(header, "Height", thickness, f"Sheet.{list_id}!User.visHeadingHeight")
    for child, fill in ((body, item.get("fill", DEFAULT_LANE_FILL)), (header, item.get("headerFill", DEFAULT_HEADER_FILL))):
        _cells(child, {"FillPattern": 1, "FillForegnd": fill, "FillBkgnd": fill, "FillForegndTrans": 0,
                       "FillBkgndTrans": 0, "LinePattern": 1, "LineColor": "#A5B3BB", "LineWeight": .006})
    chars = _element(_element(header, "Section", N="Character"), "Row", IX=0)
    _cells(chars, {"Font": family, "AsianFont": family, "Size": HEADER_POINTS/72, "Style": 1, "Color": "#173B4D"})
    paragraph = _element(_element(header, "Section", N="Paragraph"), "Row", IX=0)
    _cell(paragraph, "HorzAlign", 1)
    _cell(header, "VerticalAlign", 1)
    _rectangle(body, width, height)
    _rectangle(header, local_width, thickness)
    _element(header, "Text").text = title


def write_swimlanes(source, spec, output, *, stencil=None, font_family=None):
    """Add ordered rows or columns to a new copy of a single Core page."""
    with ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    fields = ("visio/pages/page1.xml", "visio/masters/masters.xml",
              "visio/masters/_rels/masters.xml.rels", "[Content_Types].xml")
    page, masters, rels, types = trees = [ET.fromstring(entries[name]) for name in fields]
    pages = ET.fromstring(entries["visio/pages/pages.xml"]).findall(V + "Page")
    if len(pages) != 1:
        raise ValueError("Swimlanes require a single generated page.")
    shapes = page.find(V + "Shapes")
    nodes = {s.get("NameU")[5:]: s for s in shapes if s.get("NameU", "").startswith("node_")}
    vertical, frames = _validate(spec, pages[0].find(V + "PageSheet"), nodes)
    header_font = font_family
    if header_font is None:
        family = shapes.find(f".//{V}Section[@N='Character']/{V}Row/{V}Cell[@N='Font']")
        header_font = family.get("V") if family is not None else DEFAULT_FONT
    lane_master = "Swimlane (vertical)" if vertical else "Swimlane"
    with ZipFile(_stencil(stencil)) as template:
        master, children = _masters(template, masters, rels, types, entries, lane_master)

    list_id = max(int(s.get("ID")) for s in page.iter(V + "Shape")) + 1
    lanes = spec["lanes"]
    first, first_heading = frames[0]
    last, _ = frames[-1]
    table = Rect(first.left, first.top, last.right, last.bottom)
    page_height = spec["page"]["height"]
    lane_list = _shape(list_id, "native_swimlane_list", "Shape", master["Swimlane List"])
    _cells(lane_list, {"PinX": table.left, "PinY": page_height-table.top, "Width": table.width,
                       "Height": table.height, "LinePattern": 0, "FillPattern": 0})
    _cell(lane_list, "LocPinX", 0, "Width*0")
    _cell(lane_list, "LocPinY", table.height, "Height")
    rotate_label = 0 if vertical else 1
    _prop(lane_list, "visRotateLabel", rotate_label)
    _prop(lane_list, "visHeadingHeight", first_heading.height if vertical else first_heading.width)
    _prop(lane_list, "msvSDListDirection", 0 if vertical else 2)
    _prop(lane_list, "msvSDListItemMaster", master[lane_master], f'USE("{lane_master}")')
    shapes.insert(0, lane_list)
    lane_ids = []
    with WindowsFont(header_font) as font:
        for i, item in enumerate(lanes):
            frame, heading = frames[i]
            title = item["title"]
            where = "lane " + item["id"] + ".title"
            available = heading.width - .08
            measured = wrap_text(font, title, HEADER_POINTS, True, available, where)
            # Short centered headings read better when both lines have similar widths.
            # Keep the original number of lines and honor explicit line breaks.
            if measured["text"].count("\n") == 1 and "\n" not in title and "\r" not in title:
                target = font.measure(title, HEADER_POINTS, True)[0] / 2 + 2 * TEXT_MARGIN
                widest = max(font.measure(c, HEADER_POINTS, True)[0] for c in title) + 2 * TEXT_MARGIN
                balanced = wrap_text(font, title, HEADER_POINTS, True, min(available, max(target, widest) + .01), where)
                if balanced["text"].count("\n") == 1:
                    measured = balanced
            if measured["height"] > heading.height - .08:
                raise ValueError(f"Heading does not fit swimlane {item['id']}")
            lane_id = list_id + 1 + 3*i
            lane_ids.append(lane_id)
            lane = _shape(lane_id, "lane_"+item["id"], "Group", master[lane_master])
            shapes.insert(i+1, lane)
            _cells(lane, {"PinX": (frame.left+frame.right)/2, "PinY": page_height-(frame.top+frame.bottom)/2,
                          "Width": frame.width, "Height": frame.height})
            _cell(lane, "LocPinX", frame.width/2, "Width*0.5")
            _cell(lane, "LocPinY", frame.height/2, "Height*0.5")
            ids = [nodes[name].get("ID") for name in item["members"]]
            _related(lane, (5, [list_id]), (1, ids))
            for name in item["members"]:
                _related(nodes[name], (4, [lane_id, list_id]))
            _prop(lane, "visRotateLabel", rotate_label)
            _prop(lane, "SwimlaneListGUID", lane_list.get("UniqueID")).set("U", "GUID")
            _appearance(lane, item, children, list_id, frame, heading, vertical,
                        measured["text"], font.family)
    _related(lane_list, (2, lane_ids), (1, [s.get("ID") for s in nodes.values()]))
    for shape in shapes:
        if shape.get("NameU", "").startswith("edge_label_"):
            _cells(shape, {"FillPattern": 1, "FillForegnd": "#FFFFFF", "FillForegndTrans": 0,
                           "LinePattern": 0, "TextBkgnd": 2, "TextBkgndTrans": 0})
    for name, tree in zip(fields, trees):
        entries[name] = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
    output = Path(output)
    with output.open("xb") as stream:
        try:
            with ZipFile(stream, "w", ZIP_DEFLATED) as bundle:
                for name, data in entries.items():
                    bundle.writestr(name, data)
        except BaseException:
            stream.close()
            output.unlink(missing_ok=True)
            raise
