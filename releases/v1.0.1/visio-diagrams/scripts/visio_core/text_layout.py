"""Measure and wrap text using the configured installed Windows font."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from functools import lru_cache
import os
from struct import unpack_from
import unicodedata

from .errors import GenerationError

TEXT_MARGIN = .045
VERTICAL_MARGIN = .025
DPI = 720
FONT_PATH = "$.resources.fontFamily"


class WindowsFont:
    def __init__(self, family):
        # Configuration names the installed family so Visio and SVG can select it too.
        if os.name != "nt":
            raise GenerationError("WINDOWS_REQUIRED", "Text measurement requires Windows GDI", FONT_PATH)
        if not isinstance(family, str) or not family.strip() or any(c in family for c in '"\r\n\t'):
            raise GenerationError("INVALID_FONT", f"Invalid Windows font family name: {family!r}", FONT_PATH)
        self.requested = family.strip()
        self.family = self.requested
        self.aliases = ()
        self.gdi = ctypes.WinDLL("gdi32")
        self.bind("CreateCompatibleDC", wintypes.HDC, wintypes.HDC)
        self.bind("DeleteDC", wintypes.BOOL, wintypes.HDC)
        self.bind("CreateFontW", wintypes.HFONT, *([ctypes.c_int] * 5), *([wintypes.DWORD] * 8), wintypes.LPCWSTR)
        self.bind("SelectObject", wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
        self.bind("DeleteObject", wintypes.BOOL, wintypes.HGDIOBJ)
        self.bind("GetTextFaceW", ctypes.c_int, wintypes.HDC, ctypes.c_int, wintypes.LPWSTR)
        self.bind("GetFontData", wintypes.DWORD, wintypes.HDC, wintypes.DWORD, wintypes.DWORD,
                  ctypes.c_void_p, wintypes.DWORD)
        self.bind("GetTextExtentPoint32W", wintypes.BOOL, wintypes.HDC, wintypes.LPCWSTR,
                  ctypes.c_int, ctypes.POINTER(wintypes.SIZE))
        self.bind("GetGlyphIndicesW", wintypes.DWORD, wintypes.HDC, wintypes.LPCWSTR,
                  ctypes.c_int, ctypes.POINTER(wintypes.WORD), wintypes.DWORD)
        self.dc = None
        self.faces = {}
        self.measure = lru_cache(maxsize=8192)(self._measure)
        self.check_glyphs = lru_cache(maxsize=2048)(self._check_glyphs)

    def bind(self, name, result, *arguments):
        method = getattr(self.gdi, name)
        method.restype = result
        method.argtypes = arguments

    def __enter__(self):
        self.dc = self.gdi.CreateCompatibleDC(None)
        if not self.dc:
            raise GenerationError("FONT_MEASUREMENT_FAILED", "Unable to open a Windows GDI device context", FONT_PATH)
        try:
            with self.select(11, False):
                actual = self.selected_name()
                english, aliases = self.family_names()
                aliases.add(actual)
                if not actual or self.requested.casefold() not in {name.casefold() for name in aliases}:
                    raise GenerationError("FONT_MISSING",
                                          f"Font {self.requested!r} is unavailable. Install it in Windows Fonts or use --font-family.",
                                          FONT_PATH, substituted=actual)
                # The Windows family name in English is a portable Visio NameU.
                self.family = english or actual
                self.aliases = tuple(sorted(aliases | {self.family}))
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def selected_name(self):
        face = ctypes.create_unicode_buffer(128)
        if not self.gdi.GetTextFaceW(self.dc, len(face), face):
            raise GenerationError("FONT_MEASUREMENT_FAILED",
                                  f"Unable to identify the selected font for {self.requested!r}", FONT_PATH)
        return face.value

    def family_names(self):
        # OpenType name ID 1 is the Win32 family. Localized names from this face
        # are valid aliases, while typographic group names may include other faces.
        table = int.from_bytes(b"name", "little")
        length = self.gdi.GetFontData(self.dc, table, 0, None, 0)
        if not 6 <= length < 1024 * 1024:
            return None, set()
        data = ctypes.create_string_buffer(length)
        if self.gdi.GetFontData(self.dc, table, 0, data, length) != length:
            return None, set()
        raw = data.raw
        _, count, string_start = unpack_from(">HHH", raw)
        names = set()
        english = None
        for offset in range(6, min(6 + 12 * count, length - 11), 12):
            platform, _, language, name_id, size, relative = unpack_from(">6H", raw, offset)
            start = string_start + relative
            if platform not in (0, 3) or name_id != 1 or not size or start + size > length:
                continue
            name = raw[start:start + size].decode("utf-16-be", errors="replace")
            names.add(name)
            if platform == 3 and language == 0x0409:
                english = name
        return english, names

    def __exit__(self, *_):
        self.gdi.DeleteDC(self.dc)
        for handle in self.faces.values():
            self.gdi.DeleteObject(handle)
        self.measure.cache_clear()
        self.check_glyphs.cache_clear()

    @contextmanager
    def select(self, points, bold):
        pixels = max(1, round(points * DPI / 72))
        key = (pixels, bold)
        new_face = key not in self.faces
        if new_face:
            handle = self.gdi.CreateFontW(-pixels, 0, 0, 0, 700 if bold else 400,
                                          0, 0, 0, 1, 0, 0, 0, 0, self.family)
            if not handle:
                raise GenerationError("FONT_MEASUREMENT_FAILED", f"Unable to select {self.family!r} at {points} pt", FONT_PATH)
            self.faces[key] = handle
        previous = self.gdi.SelectObject(self.dc, self.faces[key])
        if not previous:
            raise GenerationError("FONT_MEASUREMENT_FAILED", f"Unable to select {self.family!r}", FONT_PATH)
        try:
            if new_face and self.aliases:
                actual = self.selected_name()
                if actual.casefold() not in {name.casefold() for name in self.aliases}:
                    raise GenerationError("FONT_MISSING",
                                          f"Font {self.family!r} is unavailable at {points} pt for the requested style",
                                          FONT_PATH, substituted=actual)
            yield
        finally:
            self.gdi.SelectObject(self.dc, previous)

    def _measure(self, text, points, bold):
        if not text:
            return 0.0, self.measure("Ag", points, bold)[1]
        size = wintypes.SIZE()
        with self.select(points, bold):
            units = len(text.encode("utf-16-le")) // 2
            if not self.gdi.GetTextExtentPoint32W(self.dc, text, units, ctypes.byref(size)):
                raise GenerationError("FONT_MEASUREMENT_FAILED", f"Unable to measure {text!r}", FONT_PATH)
        # Leave a small margin for Visio's paragraph and glyph rasterization.
        return size.cx / DPI * 1.05, size.cy / DPI * 1.04

    def _check_glyphs(self, text, points, bold):
        characters = "".join(dict.fromkeys(c for c in text if not c.isspace()))
        if not characters:
            return None
        units = len(characters.encode("utf-16-le")) // 2
        with self.select(points, bold):
            glyphs = (wintypes.WORD * units)()
            result = self.gdi.GetGlyphIndicesW(self.dc, characters, units, glyphs, 1)
            if result == 0xFFFFFFFF:
                raise GenerationError("FONT_MEASUREMENT_FAILED", "Unable to check required glyphs", FONT_PATH)
        position = 0
        for c in characters:
            count = 2 if ord(c) > 0xffff else 1
            if glyphs[position] == 0xffff:
                return c
            position += count
        return None


def text_frame(node, *, compat=False):
    if node["kind"] == "container":
        return {"x": node["x"] + node["padding"], "y": node["y"],
                "w": node["w"] - 2 * node["padding"], "h": node["headerHeight"]}
    scale_w, scale_h = shape_text_scale(node["kind"], compat=compat)
    return {"x": node["x"] + node["w"] * (1 - scale_w) / 2,
            "y": node["y"] + node["h"] * (1 - scale_h) / 2,
            "w": node["w"] * scale_w, "h": node["h"] * scale_h}


def shape_text_scale(kind, *, compat=False):
    if compat:
        return 1, 1
    return {"diamond": (.62, .62), "ellipse": (.78, .74), "terminator": (.83, .95)}.get(kind, (1, 1))


def source_text(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def wrap_text(font, text, points, bold, max_width, path):
    text = source_text(text)
    if "\t" in text:
        raise GenerationError("TEXT_TAB_UNSUPPORTED", "Use spaces or line breaks instead of tabs", path)
    missing = font.check_glyphs(text, points, bold)
    if missing:
        raise GenerationError("FONT_GLYPH_MISSING", f"Font {font.family!r} lacks {missing!r} (U+{ord(missing):04X})", path)
    lines = []
    for paragraph in text.split("\n"):
        while font.measure(paragraph, points, bold)[0] + 2 * TEXT_MARGIN > max_width + 1e-8:
            if not paragraph:
                raise GenerationError("TEXT_TOO_WIDE", f"Text frame is too narrow for the margins at {points} pt", path)
            lo, hi = 0, len(paragraph)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if font.measure(paragraph[:mid], points, bold)[0] + 2 * TEXT_MARGIN <= max_width + 1e-8:
                    lo = mid
                else:
                    hi = mid - 1
            if not lo:
                raise GenerationError("TEXT_TOO_WIDE", f"Character {paragraph[0]!r} does not fit at {points} pt", path,
                                      availableWidth=max_width, fontSize=points)
            # Preserve every character and prefer a word boundary when it leaves useful space.
            cut = paragraph.rfind(" ", 0, lo) + 1
            if lo / 2 <= cut:
                lo = cut
            if getattr(font, "avoid_orphans", False) and lo > 1:
                # Avoid an isolated final ideograph and closing punctuation at
                # the start of a line without changing authored paragraphs.
                while lo > 1 and paragraph[lo] in "，。；：、！？）》】」』…":
                    lo -= 1
                if len(paragraph) - lo == 1 and unicodedata.east_asian_width(paragraph[-1]) in ("W", "F"):
                    lo -= 1
                while lo > 1 and paragraph[lo-1] in "（《【「『":
                    lo -= 1
            # Keep the end of a CJK paragraph together when one more glyph
            # would otherwise occupy the final line by itself.
            if (len(paragraph) - lo == 1 and lo >= 3 and
                    all(unicodedata.east_asian_width(c) in ('W', 'F') for c in paragraph[-2:])):
                lo -= 1
            while lo < len(paragraph) and lo > 0 and unicodedata.combining(paragraph[lo]):
                lo -= 1
            if not lo:
                raise GenerationError("TEXT_TOO_WIDE", f"A character cluster does not fit at {points} pt", path)
            lines.append(paragraph[:lo])
            paragraph = paragraph[lo:]
        lines.append(paragraph)
    metrics = [font.measure(line, points, bold) for line in lines]
    line_height = max(height for _, height in metrics)
    return {"text": "\n".join(lines), "lineHeight": line_height,
            "width": max(width for width, _ in metrics) + 2 * TEXT_MARGIN,
            "height": len(lines) * line_height + 2 * VERTICAL_MARGIN}


def text_variants(font, text, points, bold, caps, path):
    """Measure distinct wraps without selecting a frame or changing the point size."""
    widest = max((font.measure(c, points, bold)[0] for c in set(text) if c not in "\r\n\t"), default=0)
    min_width = widest + 2 * TEXT_MARGIN + .03
    options = {}
    for cap in sorted(set(caps), reverse=True):
        try:
            item = wrap_text(font, text, points, bold, max(min_width, cap), path)
        except GenerationError as error:
            if not options or error.diagnostic["code"] != "TEXT_TOO_WIDE":
                raise
            continue
        options.setdefault(item["text"], item)
    return tuple(options.values())


def node_text_metrics(font, node, frame_width):
    """Measure both runs of one native text block at the candidate width."""
    path = f"node {node['id']}"
    title = wrap_text(font, node.get("text", ""), node.get("fontSize", 11),
                      node.get("bold", False), frame_width, path + ".text")
    if not node.get("detail"):
        return title
    detail = wrap_text(font, node["detail"], node.get("detailFontSize", 10),
                       False, frame_width, path + ".detail")
    return {"title": title, "detail": detail, "width": max(title["width"], detail["width"]),
            "height": title["height"] + detail["height"] - 2 * VERTICAL_MARGIN}


def fit_node_text(font, node, frame):
    if not node.get("detail"):
        fit_text(font, node, frame, f"node {node['id']}.text")
        return
    layout = node_text_metrics(font, node, frame["w"])
    if layout["height"] > frame["h"] + 1e-8:
        raise GenerationError("TEXT_TOO_TALL", "Title and detail exceed the text frame", f"node {node['id']}.size",
                              requiredHeight=layout["height"], availableHeight=frame["h"])
    top = frame["y"] + (frame["h"] - layout["height"]) / 2 + VERTICAL_MARGIN
    for key, metrics in (("text", layout["title"]), ("detail", layout["detail"])):
        wrapped = metrics["text"]
        height = metrics["height"] - 2 * VERTICAL_MARGIN
        if wrapped != node[key]:
            node["sourceText" if key == "text" else "sourceDetail"] = source_text(node[key])
        node[key] = wrapped
        node["_lineHeight" if key == "text" else "_detailLineHeight"] = metrics["lineHeight"]
        node["_titleFrame" if key == "text" else "_detailFrame"] = dict(frame, y=top, h=height)
        top += height


def fit_text(font, item, frame, path, *, base_size=11):
    original = source_text(item.get("text", ""))
    if not original:
        return
    points = item.get("fontSize", base_size)
    layout = wrap_text(font, original, points, item.get("bold", False), frame["w"], path)
    if layout["height"] > frame["h"] + 1e-8:
        raise GenerationError("TEXT_TOO_TALL", f"Text cannot fit the fixed frame at {points} pt. Enlarge the frame or adjust the layout.",
                              path, fontSize=points, requiredHeight=layout["height"], availableHeight=frame["h"])
    if original != layout["text"]:
        item["sourceText"] = original
    item["text"] = layout["text"]
    item["_lineHeight"] = layout["lineHeight"]
