"""SVG geometry preview from the resolved scene in page coordinates."""
from html import escape
from pathlib import Path


def n(value):
    return f"{value:.8f}".rstrip("0").rstrip(".") or "0"


def text_svg(item, frame, font, default_points):
    text = item.get("text", "")
    if not text:
        return ""
    lines = text.split("\n")
    height = item["_lineHeight"]
    size = item.get("fontSize", default_points) / 72
    weight = 700 if item.get("bold") else 400
    cx = frame["x"] + frame["w"] / 2
    first_y = frame["y"] + (frame["h"] - height * (len(lines) - 1)) / 2
    rows = "".join(f'<tspan x="{n(cx)}" y="{n(first_y + i * height)}">{escape(line)}</tspan>'
                   for i, line in enumerate(lines))
    return (f'<text text-anchor="middle" dominant-baseline="central" xml:space="preserve" '
            f'font-family="{escape(font, quote=True)}" font-weight="{weight}" font-size="{n(size)}">{rows}</text>')


def node_svg(node):
    x, y, w, h = (node[key] for key in ("x", "y", "w", "h"))
    style = f'fill="{node.get("fill") or "#FFFFFF"}" stroke="#404040" stroke-width="{n(.75/72)}"'
    if node.get("dashed"):
        style += ' stroke-dasharray=".07 .045"'
    kind = node["kind"]
    if kind == "text":
        return ""
    if kind == "ellipse":
        return f'<ellipse cx="{n(x+w/2)}" cy="{n(y+h/2)}" rx="{n(w/2)}" ry="{n(h/2)}" {style}/>'
    if kind == "diamond":
        points = ((x+w/2, y), (x+w, y+h/2), (x+w/2, y+h), (x, y+h/2))
        return f'<polygon points="{polyline(points)}" {style}/>'
    rounding = min(w, h) / 2 if kind == "terminator" else 0
    return f'<rect x="{n(x)}" y="{n(y)}" width="{n(w)}" height="{n(h)}" rx="{n(rounding)}" {style}/>'


def polyline(points):
    return " ".join(f"{n(x)},{n(y)}" for x, y in points)


def write_preview(scene, path, scene_hash):
    page, font = scene["page"], scene["fontFamily"]
    width, height = n(page["width"]), n(page["height"])
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}in" height="{height}in" viewBox="0 0 {width} {height}">',
        f'<title>{escape(page.get("title") or "Diagram")}</title>',
        f'<desc>Geometry preview. Scene SHA-256 {scene_hash}</desc>',
        '<defs><marker id="arrow" markerUnits="userSpaceOnUse" viewBox="0 0 12 10" '
        'markerWidth=".12" markerHeight=".10" refX="12" refY="5" orient="auto">'
        '<path d="M 0 0 L 12 5 L 0 10 Z" fill="#404040"/></marker></defs>',
        f'<rect width="{width}" height="{height}" fill="white"/>',
    ]
    for group in scene.get("groups", []):
        elements.append(f'<g data-group="{escape(group["id"], quote=True)}"><rect '
                        f'x="{n(group["x"])}" y="{n(group["y"])}" width="{n(group["w"])}" height="{n(group["h"])}" '
                        f'fill="{group.get("fill", "#F3F6F8")}" stroke="#93A3B3" stroke-width="{n(.7/72)}"/>'
                        f'{text_svg(group, group["_textFrame"], font, 11)}</g>')
    for node in scene["nodes"]:
        detail = ""
        if node.get("detail"):
            item = {"text": node["detail"], "fontSize": node.get("detailFontSize", 10),
                    "_lineHeight": node["_detailLineHeight"]}
            detail = '<g fill="#303C48">' + text_svg(item, node["_detailFrame"], font, 10) + '</g>'
        elements.append(f'<g data-node="{escape(node["id"], quote=True)}">{node_svg(node)}'
                        f'{text_svg(node, node.get("_titleFrame") or node.get("_textFrame"), font, 11)}{detail}</g>')
    if page.get("_title"):
        elements.append(text_svg(page["_title"], page["_title"], font, 16))
    for edge in scene["edges"]:
        dashed = ' stroke-dasharray=".075 .04"' if edge.get("dashed") else ""
        elements.append(f'<g data-edge="{escape(edge["id"], quote=True)}"><polyline fill="none" stroke="#404040" '
                        f'stroke-width="{n(.75/72)}" points="{polyline(edge["routePoints"])}" '
                        f'marker-end="url(#arrow)"{dashed}/>'
                        f'{text_svg(edge, edge.get("label") or edge.get("_inlineLabel"), font, 10)}</g>')
    Path(path).write_text("\n".join([*elements, "</svg>", ""]), encoding="utf-8")
