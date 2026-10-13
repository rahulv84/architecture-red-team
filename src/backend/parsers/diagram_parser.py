"""Deterministic diagram parsers: Draw.io XML, Mermaid, PlantUML and SVG labels.

These extract structure that is explicitly present in the diagram source.  They never
infer connections that are not drawn.  SVG yields text labels only (connectors in a
rendered SVG are anonymous paths), which is reported as a limitation.
"""
from __future__ import annotations

import base64
import html
import re
import urllib.parse
import zlib

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from ..core.exceptions import ParseError
from .document_parser import decode_text
from .types import DiagramEdge, DiagramNode, ParsedDocument, ParserLimits


def _strip_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value or "", flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _diagram_text(nodes: list[DiagramNode], edges: list[DiagramEdge]) -> str:
    labels = {n.node_id: n.label for n in nodes}
    lines = ["Diagram nodes:"]
    lines += [f"- {n.label} (id={n.node_id}{', group=' + n.group if n.group else ''})" for n in nodes]
    if edges:
        lines.append("Diagram connections:")
        lines += [
            f"- {labels.get(e.source, e.source)} -> {labels.get(e.target, e.target)}" + (f" [{e.label}]" if e.label else "")
            for e in edges
        ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- draw.io
def _decode_drawio_payload(text: str) -> str:
    """Draw.io stores compressed diagrams as base64(raw-deflate(urlencoded xml))."""
    raw = base64.b64decode(text.strip())
    xml = zlib.decompress(raw, -15)
    if len(xml) > 20_000_000:
        raise ParseError("Draw.io diagram expands beyond the allowed size.")
    return urllib.parse.unquote(xml.decode("utf-8"))


def parse_drawio(data: bytes, filename: str, limits: ParserLimits) -> ParsedDocument:
    text = decode_text(data, limits)
    try:
        root = SafeET.fromstring(text)
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise ParseError(f"Invalid or unsafe Draw.io XML: {exc}") from exc

    graph_roots = []
    if root.tag == "mxGraphModel":
        graph_roots.append(root)
    else:
        for diagram in root.iter("diagram"):
            inner = [c for c in diagram if c.tag == "mxGraphModel"]
            if inner:
                graph_roots.append(inner[0])
            elif (diagram.text or "").strip():
                try:
                    graph_roots.append(SafeET.fromstring(_decode_drawio_payload(diagram.text)))
                except Exception as exc:  # corrupt/compressed payload variants
                    raise ParseError(f"Could not decode compressed Draw.io diagram: {exc}") from exc
    if not graph_roots:
        raise ParseError("No mxGraphModel found; file does not look like a Draw.io diagram.")

    nodes: list[DiagramNode] = []
    edges: list[DiagramEdge] = []
    containers: dict[str, str] = {}
    cells = []
    for graph in graph_roots:
        for el in graph.iter():
            if el.tag in ("object", "UserObject"):
                inner = next((c for c in el if c.tag == "mxCell"), None)
                if inner is not None:
                    cells.append((el.get("id", ""), el.get("label") or el.get("name") or "", inner))
            elif el.tag == "mxCell":
                cells.append((el.get("id", ""), el.get("value") or "", el))
    seen_ids: set[str] = set()
    for cell_id, label, cell in cells:
        if cell_id in seen_ids and cell_id:
            continue
        seen_ids.add(cell_id)
        if cell.get("vertex") == "1":
            style = cell.get("style", "")
            label_text = _strip_html(label)
            if "container=1" in style or "swimlane" in style or style.startswith("group"):
                containers[cell_id] = label_text
            if label_text:
                nodes.append(DiagramNode(node_id=cell_id, label=label_text, shape=style.split(";")[0] or None,
                                         group=cell.get("parent")))
        elif cell.get("edge") == "1":
            src, dst = cell.get("source"), cell.get("target")
            if src and dst:
                edges.append(DiagramEdge(source=src, target=dst, label=_strip_html(label)))
    for node in nodes:
        node.group = containers.get(node.group or "") if node.group in containers else None
    known = {n.node_id for n in nodes}
    warnings: list[str] = []
    dangling = [e for e in edges if e.source not in known or e.target not in known]
    if dangling:
        warnings.append(f"{len(dangling)} connector(s) reference unlabeled shapes and were kept without labels.")
        for e in dangling:
            for end in (e.source, e.target):
                if end not in known:
                    nodes.append(DiagramNode(node_id=end, label=f"(unlabeled shape {end})"))
                    known.add(end)
    if len(nodes) > limits.max_nodes:
        raise ParseError("Diagram contains too many nodes.")
    return ParsedDocument(
        filename=filename, kind="drawio", text=_diagram_text(nodes, edges), nodes=nodes, edges=edges, warnings=warnings,
    )


# --------------------------------------------------------------------------- mermaid
_MERMAID_SHAPES = [
    ("[[", "]]"), ("((", "))"), ("[(", ")]"), ("{{", "}}"), ("[/", "/]"), ("[\\", "\\]"),
    ("[", "]"), ("(", ")"), ("{", "}"), (">", "]"),
]
_NODE_RE = re.compile(r"^\s*([A-Za-z_][\w\-]*)\s*(.*)$", re.S)
_EDGE_SPLIT = re.compile(r"\s*(<?[-=.~]{2,}[>ox]?(?:\|[^|]*\|)?|<?-\.+-?>?(?:\|[^|]*\|)?)\s*")


def _parse_mermaid_node(token: str) -> tuple[str, str | None, str | None] | None:
    token = token.strip().rstrip(";")
    if not token:
        return None
    match = _NODE_RE.match(token)
    if not match:
        return None
    node_id, rest = match.group(1), match.group(2).strip()
    label = None
    shape = None
    if rest:
        for opener, closer in _MERMAID_SHAPES:
            if rest.startswith(opener) and rest.endswith(closer):
                label = rest[len(opener):-len(closer)].strip().strip('"')
                shape = opener + closer
                break
        rest = ""
    return node_id, _strip_html(label) if label else None, shape


def parse_mermaid(data: bytes, filename: str, limits: ParserLimits) -> ParsedDocument:
    text = decode_text(data, limits)
    lines = text.splitlines()
    nodes: dict[str, DiagramNode] = {}
    edges: list[DiagramEdge] = []
    groups: list[str] = []
    kind = "flowchart"
    warnings: list[str] = []
    for lineno, raw in enumerate(lines, start=1):
        line = raw.split("%%")[0].strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith(("graph", "flowchart")):
            continue
        if low.startswith("sequencediagram"):
            kind = "sequence"
            continue
        if low.startswith(("classdef", "class ", "style ", "linkstyle", "click ", "direction")):
            continue
        if kind == "sequence":
            part = re.match(r"^(participant|actor)\s+(\S+)(?:\s+as\s+(.+))?$", line, re.I)
            if part:
                nodes[part.group(2)] = DiagramNode(node_id=part.group(2), label=(part.group(3) or part.group(2)).strip(), line=lineno)
                continue
            msg = re.match(r"^(\S+?)\s*(--?>>?|--?x|--?\)|-->>|->>)\s*\+?-?(\S+?)\s*:\s*(.*)$", line)
            if msg:
                for ident in (msg.group(1), msg.group(3)):
                    nodes.setdefault(ident, DiagramNode(node_id=ident, label=ident, line=lineno))
                edges.append(DiagramEdge(source=msg.group(1), target=msg.group(3), label=msg.group(4).strip(), line=lineno))
            continue
        sub = re.match(r"^subgraph\s+(.+)$", line, re.I)
        if sub:
            title = sub.group(1).strip()
            bracket = re.match(r"^[\w\-]+\s*\[(.+)\]$", title)
            groups.append(_strip_html((bracket.group(1) if bracket else title).strip('"')))
            continue
        if low == "end":
            if groups:
                groups.pop()
            continue
        # Normalise "A -- text --> B" to "A -->|text| B"
        line = re.sub(r"--\s+([^->|]+?)\s+-->", r"-->|\1|", line)
        line = re.sub(r"==\s+([^=>|]+?)\s+==>", r"==>|\1|", line)
        parts = _EDGE_SPLIT.split(line)
        if len(parts) == 1:
            parsed = _parse_mermaid_node(parts[0])
            if parsed:
                nid, label, shape = parsed
                prev = nodes.get(nid)
                nodes[nid] = DiagramNode(node_id=nid, label=label or (prev.label if prev else nid), shape=shape or (prev.shape if prev else None),
                                         group=groups[-1] if groups else (prev.group if prev else None), line=lineno)
            continue
        operands = parts[0::2]
        operators = parts[1::2]
        resolved: list[list[str]] = []
        for operand in operands:
            ids = []
            for token in re.split(r"\s+&\s+", operand):
                parsed = _parse_mermaid_node(token)
                if not parsed:
                    continue
                nid, label, shape = parsed
                prev = nodes.get(nid)
                nodes[nid] = DiagramNode(
                    node_id=nid, label=label or (prev.label if prev else nid), shape=shape or (prev.shape if prev else None),
                    group=groups[-1] if groups else (prev.group if prev else None), line=lineno,
                )
                ids.append(nid)
            resolved.append(ids)
        for idx, operator in enumerate(operators):
            label_match = re.search(r"\|([^|]*)\|", operator)
            label = label_match.group(1).strip() if label_match else ""
            if idx + 1 >= len(resolved):
                break
            for s in resolved[idx]:
                for t in resolved[idx + 1]:
                    edges.append(DiagramEdge(source=s, target=t, label=_strip_html(label), line=lineno))
    if not nodes:
        raise ParseError("No Mermaid nodes could be parsed.")
    if kind == "sequence":
        warnings.append("Sequence diagram: participants and messages extracted; deployment topology is not shown.")
    node_list = list(nodes.values())
    return ParsedDocument(
        filename=filename, kind="mermaid", text=_diagram_text(node_list, edges), nodes=node_list, edges=edges, warnings=warnings,
        metadata={"diagram_type": kind},
    )


# --------------------------------------------------------------------------- plantuml
_PUML_ELEMENT = re.compile(
    r'^(component|node|database|queue|cloud|rectangle|actor|package|folder|frame|storage|artifact|interface|'
    r'boundary|control|entity|collections|usecase|agent|file|card|stack|person|system|container)\s+'
    r'(?:"([^"]+)"|\[([^\]]+)\]|([\w\-\.]+))(?:\s+as\s+([\w\-\.]+))?\s*(?:<<[^>]*>>)?\s*(\{)?\s*$',
    re.I,
)
_PUML_BRACKET_ALIAS = re.compile(r"^\[([^\]]+)\]\s+as\s+([\w\-\.]+)\s*$")
_PUML_ARROW = re.compile(
    r'^(\[[^\]]+\]|\([^)]+\)|"[^"]+"|[\w\-\.]+)\s*(<?[-.=]+(?:\w+[-.=]*)?[>\]]?|<[-.=]+)\s*'
    r'(\[[^\]]+\]|\([^)]+\)|"[^"]+"|[\w\-\.]+)\s*(?::\s*(.*))?$'
)


def parse_plantuml(data: bytes, filename: str, limits: ParserLimits) -> ParsedDocument:
    text = decode_text(data, limits)
    nodes: dict[str, DiagramNode] = {}
    edges: list[DiagramEdge] = []
    groups: list[str] = []
    aliases: dict[str, str] = {}

    def ref(token: str, lineno: int) -> str:
        token = token.strip()
        label = token.strip('[]()"')
        key = aliases.get(token) or aliases.get(label) or label
        if key not in nodes:
            nodes[key] = DiagramNode(node_id=key, label=label, group=groups[-1] if groups else None, line=lineno)
        return key

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("'", "@", "skinparam", "!", "title", "left to right", "top to bottom", "note", "legend", "end note", "hide", "show")):
            continue
        if line == "}":
            if groups:
                groups.pop()
            continue
        el = _PUML_ELEMENT.match(line)
        if el:
            kind = el.group(1).lower()
            label = el.group(2) or el.group(3) or el.group(4)
            alias = el.group(5) or label
            aliases[alias] = alias
            aliases[label] = alias
            if el.group(6):  # "kind <label> {" opens a container; its members get it as group
                groups.append(label)
                continue
            nodes[alias] = DiagramNode(node_id=alias, label=label, shape=kind, group=groups[-1] if groups else None, line=lineno)
            continue
        al = _PUML_BRACKET_ALIAS.match(line)
        if al:
            aliases[al.group(2)] = al.group(2)
            aliases[al.group(1)] = al.group(2)
            nodes[al.group(2)] = DiagramNode(node_id=al.group(2), label=al.group(1), shape="component", group=groups[-1] if groups else None, line=lineno)
            continue
        arrow = _PUML_ARROW.match(line)
        if arrow:
            left, op, right, label = arrow.group(1), arrow.group(2), arrow.group(3), arrow.group(4) or ""
            s, t = ref(left, lineno), ref(right, lineno)
            if op.startswith("<") and not op.endswith(">"):
                s, t = t, s
            edges.append(DiagramEdge(source=s, target=t, label=label.strip(), line=lineno))
    if not nodes:
        raise ParseError("No PlantUML elements could be parsed.")
    node_list = list(nodes.values())
    return ParsedDocument(filename=filename, kind="plantuml", text=_diagram_text(node_list, edges), nodes=node_list, edges=edges)


# --------------------------------------------------------------------------- svg
def parse_svg(data: bytes, filename: str, limits: ParserLimits) -> ParsedDocument:
    text = decode_text(data, limits)
    try:
        root = SafeET.fromstring(text)
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise ParseError(f"Invalid or unsafe SVG: {exc}") from exc
    labels: list[str] = []
    has_raster = False
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in ("text", "title", "desc", "tspan"):
            content = _strip_html("".join(el.itertext()))
            if content and content not in labels:
                labels.append(content)
        if tag == "image":
            has_raster = True
    if len(labels) > limits.max_nodes:
        raise ParseError("SVG contains too many text elements.")
    nodes = [DiagramNode(node_id=f"label-{i}", label=lab) for i, lab in enumerate(labels, start=1)]
    warnings = ["SVG: text labels extracted only; connectors in rendered SVG are anonymous paths and were not inferred."]
    if has_raster:
        warnings.append("SVG embeds raster image(s) which were not interpreted.")
    return ParsedDocument(
        filename=filename, kind="svg", text="SVG labels:\n" + "\n".join(f"- {n}" for n in labels), nodes=nodes,
        warnings=warnings, requires_vision=has_raster,
    )
