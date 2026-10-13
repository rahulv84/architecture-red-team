"""Infrastructure-as-code and configuration parsers.

Supports Terraform (.tf/.tfvars via python-hcl2), CloudFormation (YAML/JSON, including
short-form intrinsic tags), Kubernetes manifests (multi-document YAML/JSON), OpenAPI
documents, Docker Compose and generic YAML/JSON.  Uploaded content is only ever *parsed*
(safe loaders, no evaluation, no execution).
"""
from __future__ import annotations

import json
import re
from typing import Any

import yaml

from ..core.exceptions import ParseError
from .document_parser import decode_text
from .types import DocSection, IacResource, ParsedDocument, ParserLimits

K8S_WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob", "Pod"}


# --------------------------------------------------------------------------- yaml helpers
class _CfnLoader(yaml.SafeLoader):
    """SafeLoader that understands CloudFormation short-form intrinsic functions."""


def _cfn_tag(loader: yaml.SafeLoader, suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)  # type: ignore[arg-type]
    name = "Ref" if suffix == "Ref" else f"Fn::{suffix}"
    if suffix == "GetAtt" and isinstance(value, str):
        value = value.split(".", 1)
    return {name: value}


_CfnLoader.add_multi_constructor("!", _cfn_tag)


def _check_yaml_safety(text: str, limits: ParserLimits) -> None:
    aliases = len(re.findall(r"(?m)(?:^|[\s:\[\-,])\*[A-Za-z0-9_\-]+", text))
    if aliases > limits.max_yaml_aliases:
        raise ParseError("YAML contains an excessive number of aliases (possible expansion attack).")


def load_structured(text: str, filename: str, limits: ParserLimits) -> list[Any]:
    """Return the list of YAML/JSON documents in ``text`` using safe loaders."""
    stripped = text.lstrip()
    if filename.lower().endswith(".json") or stripped.startswith(("{", "[")):
        try:
            return [json.loads(text)]
        except (ValueError, RecursionError) as exc:
            if filename.lower().endswith(".json"):
                raise ParseError(f"Invalid JSON: {exc}") from exc
    _check_yaml_safety(text, limits)
    try:
        return [d for d in yaml.load_all(text, Loader=_CfnLoader) if d is not None]
    except yaml.YAMLError as exc:
        raise ParseError(f"Invalid YAML: {exc}") from exc
    except RecursionError as exc:
        raise ParseError("YAML document is nested too deeply.") from exc


def _doc_start_lines(text: str) -> list[int]:
    """1-based start line of each YAML document in a multi-document stream."""
    starts = [1]
    for idx, line in enumerate(text.splitlines(), start=1):
        if line.strip() == "---" and idx != 1:
            starts.append(idx + 1)
    return starts


def _find_line(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text, re.M)
    if not match:
        return None
    # leading \s* in patterns may swallow preceding blank lines; anchor on the first non-space character
    start = match.start() + (len(match.group(0)) - len(match.group(0).lstrip()))
    return text.count("\n", 0, start) + 1


# --------------------------------------------------------------------------- terraform
def _clean_hcl(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k).strip('"'): _clean_hcl(v) for k, v in value.items() if k != "__is_block__"}
    if isinstance(value, list):
        return [_clean_hcl(v) for v in value]
    if isinstance(value, str) and len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def parse_terraform(data: bytes, filename: str, limits: ParserLimits) -> ParsedDocument:
    import hcl2

    # python-hcl2's heredoc grammar only matches LF, so CRLF files (Windows checkouts) fail on `<<EOF`.
    text = decode_text(data, limits).replace("\r\n", "\n")
    try:
        parsed = hcl2.loads(text)
    except Exception as exc:  # python-hcl2/lark raise several parser-specific error types
        raise ParseError(f"Could not parse Terraform: {exc}") from exc
    resources: list[IacResource] = []
    meta: dict[str, Any] = {"variables": [], "outputs": [], "providers": [], "modules": []}
    for block_name in ("resource", "data"):
        for item in parsed.get(block_name, []):
            for rtype, instances in item.items():
                rtype_c = rtype.strip('"')
                for rname, body in instances.items():
                    rname_c = rname.strip('"')
                    prefix = "data." if block_name == "data" else ""
                    address = f"{prefix}{rtype_c}.{rname_c}"
                    line = _find_line(text, rf'^\s*{block_name}\s+"{re.escape(rtype_c)}"\s+"{re.escape(rname_c)}"')
                    resources.append(IacResource(
                        framework="terraform", resource_type=f"{prefix}{rtype_c}", name=rname_c, address=address,
                        properties=_clean_hcl(body) if isinstance(body, dict) else {}, line=line,
                    ))
    for item in parsed.get("module", []):
        for mname, body in item.items():
            mname_c = mname.strip('"')
            resources.append(IacResource(
                framework="terraform", resource_type="module", name=mname_c, address=f"module.{mname_c}",
                properties=_clean_hcl(body) if isinstance(body, dict) else {},
                line=_find_line(text, rf'^\s*module\s+"{re.escape(mname_c)}"'),
            ))
    for item in parsed.get("variable", []):
        meta["variables"].extend(k.strip('"') for k in item)
    for item in parsed.get("output", []):
        meta["outputs"].extend(k.strip('"') for k in item)
    for item in parsed.get("provider", []):
        meta["providers"].extend(k.strip('"') for k in item)
    if filename.lower().endswith(".tfvars"):
        meta["tfvars"] = {k: _clean_hcl(v) for k, v in parsed.items() if k != "__is_block__"}
    if not resources and not meta.get("tfvars") and not meta["variables"]:
        raise ParseError("Terraform file contains no resources, modules, variables or values.")
    sections = [DocSection(heading=r.address, text=json.dumps(r.properties, default=str)[:800], line_start=r.line) for r in resources]
    return ParsedDocument(filename=filename, kind="terraform", text=text, sections=sections, resources=resources, metadata=meta)


# --------------------------------------------------------------------------- generic structured
def _is_cfn(doc: Any) -> bool:
    if not isinstance(doc, dict):
        return False
    if "AWSTemplateFormatVersion" in doc:
        return True
    res = doc.get("Resources")
    return isinstance(res, dict) and bool(res) and all(
        isinstance(v, dict) and str(v.get("Type", "")).startswith(("AWS::", "Custom::", "Alexa::")) for v in res.values()
    )


def _is_k8s(doc: Any) -> bool:
    return isinstance(doc, dict) and "apiVersion" in doc and "kind" in doc


def _parse_cfn(doc: dict[str, Any], text: str, filename: str) -> ParsedDocument:
    resources: list[IacResource] = []
    for logical, body in (doc.get("Resources") or {}).items():
        if not isinstance(body, dict):
            continue
        props = body.get("Properties") or {}
        resources.append(IacResource(
            framework="cloudformation", resource_type=str(body.get("Type", "")), name=str(logical), address=str(logical),
            properties={**({"__meta": {k: v for k, v in body.items() if k not in ("Properties", "Type")}}),
                        **(props if isinstance(props, dict) else {})},
            line=_find_line(text, rf'^\s*"?{re.escape(str(logical))}"?\s*:'),
        ))
    meta = {"parameters": list((doc.get("Parameters") or {}).keys()), "outputs": list((doc.get("Outputs") or {}).keys()),
            "description": doc.get("Description")}
    sections = [DocSection(heading=f"{r.address} ({r.resource_type})", text=json.dumps(r.properties, default=str)[:800], line_start=r.line) for r in resources]
    return ParsedDocument(filename=filename, kind="cloudformation", text=text, sections=sections, resources=resources, metadata=meta)


def _parse_k8s(docs: list[dict[str, Any]], text: str, filename: str) -> ParsedDocument:
    expanded: list[dict[str, Any]] = []
    for doc in docs:
        if doc.get("kind") == "List" and isinstance(doc.get("items"), list):
            expanded.extend(i for i in doc["items"] if isinstance(i, dict))
        else:
            expanded.append(doc)
    starts = _doc_start_lines(text)
    resources: list[IacResource] = []
    for idx, doc in enumerate(expanded):
        meta = doc.get("metadata") or {}
        name = str(meta.get("name", f"unnamed-{idx}"))
        ns = meta.get("namespace")
        kind = str(doc.get("kind"))
        address = f"{kind}/{ns}/{name}" if ns else f"{kind}/{name}"
        line = starts[idx] if idx < len(starts) else None
        resources.append(IacResource(
            framework="kubernetes", resource_type=kind, name=name, address=address, properties=doc, line=line, namespace=ns,
        ))
    sections = [DocSection(heading=r.address, text=json.dumps(r.properties, default=str)[:800], line_start=r.line) for r in resources]
    return ParsedDocument(filename=filename, kind="kubernetes", text=text, sections=sections, resources=resources)


def _parse_compose(doc: dict[str, Any], text: str, filename: str) -> ParsedDocument:
    resources = []
    for name, body in (doc.get("services") or {}).items():
        resources.append(IacResource(
            framework="compose", resource_type="compose_service", name=str(name), address=f"service/{name}",
            properties=body if isinstance(body, dict) else {}, line=_find_line(text, rf"^\s{{1,4}}{re.escape(str(name))}\s*:"),
        ))
    sections = [DocSection(heading=r.address, text=json.dumps(r.properties, default=str)[:800], line_start=r.line) for r in resources]
    return ParsedDocument(filename=filename, kind="compose", text=text, sections=sections, resources=resources)


def _parse_openapi(doc: dict[str, Any], text: str, filename: str) -> ParsedDocument:
    paths = doc.get("paths") or {}
    lines = []
    for path, ops in paths.items():
        if isinstance(ops, dict):
            for method, op in ops.items():
                if method.lower() in ("get", "post", "put", "patch", "delete", "head", "options") and isinstance(op, dict):
                    sec = "secured" if op.get("security") or doc.get("security") else "NO security requirement declared"
                    lines.append(f"{method.upper()} {path} - {op.get('summary', '')} [{sec}]")
    info = doc.get("info") or {}
    summary = f"OpenAPI: {info.get('title', 'untitled')} v{info.get('version', '?')}\n" + "\n".join(lines)
    return ParsedDocument(
        filename=filename, kind="openapi", text=text,
        sections=[DocSection(heading="API surface", text=summary)],
        metadata={"endpoints": len(lines), "security_schemes": list(((doc.get("components") or {}).get("securitySchemes") or {}).keys())},
    )


def parse_structured(data: bytes, filename: str, limits: ParserLimits) -> ParsedDocument:
    text = decode_text(data, limits)
    docs = load_structured(text, filename, limits)
    if not docs:
        raise ParseError("File contains no YAML/JSON documents.")
    dict_docs = [d for d in docs if isinstance(d, dict)]
    if dict_docs and all(_is_k8s(d) for d in dict_docs):
        return _parse_k8s(dict_docs, text, filename)
    if len(dict_docs) == 1:
        doc = dict_docs[0]
        if _is_cfn(doc):
            return _parse_cfn(doc, text, filename)
        if "openapi" in doc or "swagger" in doc:
            return _parse_openapi(doc, text, filename)
        if isinstance(doc.get("services"), dict) and not doc.get("apiVersion"):
            return _parse_compose(doc, text, filename)
    kind = "json" if filename.lower().endswith(".json") else "yaml"
    return ParsedDocument(
        filename=filename, kind=kind, text=text,
        sections=[DocSection(heading=filename, text=text[:4000], line_start=1)],
        metadata={"top_level_keys": sorted({k for d in dict_docs for k in d})[:50]},
        warnings=["Generic configuration file: no infrastructure schema recognised; treated as supporting text."],
    )
