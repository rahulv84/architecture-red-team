"""Configuration, reference-integrity, observability and requirement-conformance rules."""
from __future__ import annotations

import re
from typing import Any

from ..models.domain import EvidenceLevel, Severity, ToolResult
from ..parsers.types import IacResource
from .base import ToolContext, as_bool, as_list, get_path, has, make_result, references_in, register
from .k8s import containers, pod_labels, pod_spec

TOOL_ID, TOOL_NAME = "configuration_checks", "Built-in configuration checks"


def _mk(rule: str, domain: str, res: IacResource | None, title: str, message: str, **kw: Any) -> ToolResult:
    return make_result(tool_id=TOOL_ID, tool_name=TOOL_NAME, rule_id=rule, domain=domain, res=res, title=title, message=message, **kw)


@register("CFG-K8S-001", TOOL_ID, TOOL_NAME, "operability", "Container has no resource requests/limits")
def missing_limits(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.resources:
        for c in containers(res):
            checked += 1
            r = c.get("resources") or {}
            lim, req = r.get("limits") or {}, r.get("requests") or {}
            missing = []
            if "memory" not in lim:
                missing.append("limits.memory")
            if "cpu" not in lim:
                missing.append("limits.cpu")
            if "cpu" not in req and "memory" not in req:
                missing.append("requests")
            if missing:
                out.append(_mk("CFG-K8S-001", "operability", res, "Container resource limits not configured",
                               f"Container '{c.get('name')}' in {res.address} does not set {', '.join(missing)}; it can starve neighbours or be OOM-killed unpredictably.",
                               observed=f"missing: {', '.join(missing)}", expected="CPU/memory requests and limits",
                               severity=Severity.MEDIUM if "limits.memory" in missing else Severity.LOW,
                               excerpt=f"container {c.get('name')}: no {', '.join(missing)}", qualifier=str(c.get("name"))))
    return out, checked


@register("CFG-K8S-002", TOOL_ID, TOOL_NAME, "operability", "Container image is not pinned")
def unpinned_images(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.resources:
        for c in containers(res):
            checked += 1
            image = str(c.get("image", ""))
            if not image:
                continue
            name_part = image.split("@")[0]
            tag = name_part.rsplit(":", 1)[1] if ":" in name_part.split("/")[-1] else None
            if "@sha256:" in image:
                continue
            if tag is None or tag == "latest":
                out.append(_mk("CFG-K8S-002", "operability", res, "Container image uses a mutable tag",
                               f"Container '{c.get('name')}' in {res.address} uses image '{image}' ({'untagged' if tag is None else ':latest'}); "
                               "deployments are not reproducible and rollbacks are unreliable.",
                               observed=f"image = {image}", expected="Immutable version tag or digest", severity=Severity.LOW,
                               excerpt=f"image: {image}", qualifier=str(c.get("name"))))
    return out, checked


def _tf_addresses(ctx: ToolContext) -> set[str]:
    return {r.address for r in ctx.resources if r.framework == "terraform"}


def _cfn_walk_refs(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        if set(value) == {"Ref"} and isinstance(value["Ref"], str):
            found.add(value["Ref"])
        if set(value) == {"Fn::GetAtt"}:
            ga = value["Fn::GetAtt"]
            if isinstance(ga, list) and ga:
                found.add(str(ga[0]))
            elif isinstance(ga, str):
                found.add(ga.split(".")[0])
        for v in value.values():
            found |= _cfn_walk_refs(v)
    elif isinstance(value, list):
        for v in value:
            found |= _cfn_walk_refs(v)
    return found


@register("CFG-REF-001", TOOL_ID, TOOL_NAME, "operability", "Configuration references a resource that is not defined")
def dangling_references(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    declared = _tf_addresses(ctx)
    declared_data = {a for a in declared if a.startswith("data.")}
    declared_mods = {r.address for r in ctx.resources if r.resource_type == "module"}
    for res in ctx.resources:
        if res.framework != "terraform":
            continue
        checked += 1
        for ref in sorted(references_in(res.properties)):
            if ref.startswith("module."):
                if ref.split(".")[0] + "." + ref.split(".")[1] not in declared_mods:
                    out.append(_mk("CFG-REF-001", "operability", res, "Reference to undeclared module",
                                   f"{res.address} references {ref} but no such module is declared in the supplied files.",
                                   observed=ref, expected="Declared module", level=EvidenceLevel.NEEDS_VALIDATION,
                                   severity=Severity.LOW, qualifier=ref))
                continue
            base = ".".join(ref.split(".")[: 3 if ref.startswith("data.") else 2])
            if base not in declared and base not in declared_data and base != res.address:
                out.append(_mk("CFG-REF-001", "operability", res, "Reference to a resource that is not declared",
                               f"{res.address} references {base}, which is not declared in the supplied Terraform. "
                               "It may live in another file that was not uploaded.",
                               observed=base, expected="Declared resource", level=EvidenceLevel.NEEDS_VALIDATION,
                               severity=Severity.LOW, qualifier=base))
    cfn_ids = {r.address for r in ctx.resources if r.framework == "cloudformation"}
    pseudo = {"AWS::Region", "AWS::AccountId", "AWS::StackName", "AWS::StackId", "AWS::NoValue", "AWS::URLSuffix", "AWS::Partition"}
    for res in ctx.resources:
        if res.framework != "cloudformation":
            continue
        checked += 1
        for ref in sorted(_cfn_walk_refs(res.properties)):
            if ref in cfn_ids or ref in pseudo or ref in ctx.parameters:
                continue
            out.append(_mk("CFG-REF-001", "operability", res, "Reference to an undefined logical ID",
                           f"{res.address} references '{ref}', which is neither a resource nor a parameter in the supplied template.",
                           observed=ref, expected="Declared resource or parameter", level=EvidenceLevel.NEEDS_VALIDATION,
                           severity=Severity.LOW, qualifier=ref))
    # Kubernetes service -> workload and ingress -> service integrity
    workloads = [r for r in ctx.resources if pod_spec(r) is not None]
    services = {r.name: r for r in ctx.by_type("Service") if r.framework == "kubernetes"}
    for svc in services.values():
        checked += 1
        sel = (svc.properties.get("spec") or {}).get("selector") or {}
        if not sel or not workloads:
            continue
        matched = any(all(pod_labels(w).get(k) == v for k, v in sel.items()) for w in workloads)
        if not matched:
            out.append(_mk("CFG-REF-001", "operability", svc, "Service selector matches no workload",
                           f"{svc.address} selects {sel} but no workload pod template carries those labels.",
                           observed=f"selector {sel}", expected="Selector matching pod labels", severity=Severity.MEDIUM,
                           qualifier="selector"))
    for ing in ctx.by_type("Ingress"):
        checked += 1
        for rule in as_list((ing.properties.get("spec") or {}).get("rules")):
            for p in as_list(((rule or {}).get("http") or {}).get("paths")):
                backend = (p.get("backend") or {})
                svc_name = (backend.get("service") or {}).get("name") or backend.get("serviceName")
                if svc_name and services and svc_name not in services:
                    out.append(_mk("CFG-REF-001", "operability", ing, "Ingress routes to an undefined service",
                                   f"{ing.address} routes to service '{svc_name}' which is not defined in the supplied manifests.",
                                   observed=str(svc_name), expected="Defined Service", severity=Severity.MEDIUM, qualifier=str(svc_name)))
    return out, checked


def _availability_target(ctx: ToolContext) -> tuple[float | None, str | None]:
    for nfr in ctx.model.nfrs:
        if nfr.category == "availability":
            m = re.search(r"(\d{2,3}(?:\.\d+)?)\s*%", nfr.value or nfr.statement)
            if m:
                return float(m.group(1)), nfr.statement
    return None, None


@register("CFG-NFR-001", TOOL_ID, TOOL_NAME, "resilience", "Declared availability requirement contradicts configuration")
def availability_vs_config(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    target, statement = _availability_target(ctx)
    ha_stated = any(re.search(r"(?i)multi-?az|high availability|no single point|redundan|active-active|failover", n.statement) for n in ctx.model.nfrs)
    if not ((target is not None and target >= 99.9) or ha_stated):
        return [], 0
    nfr_text = statement or next((n.statement for n in ctx.model.nfrs if re.search(r"(?i)multi-?az|high availability|no single point|redundan|failover", n.statement)), "high availability requirement")
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.by_type("aws_db_instance", "AWS::RDS::DBInstance"):
        checked += 1
        attr = "multi_az" if res.framework == "terraform" else "MultiAZ"
        val = get_path(res.properties, attr)
        if has(val) and as_bool(val) is False:
            out.append(_mk("CFG-NFR-001", "resilience", res, "Single-AZ database contradicts stated availability requirement",
                           f"Requirement: \"{nfr_text[:140]}\". {res.address} sets {attr} = false.",
                           observed=f"{attr} = false", expected=f"{attr} = true to meet the stated requirement",
                           severity=Severity.HIGH, excerpt=f"{attr} = false", qualifier="db"))
    for res in ctx.by_type("Deployment", "StatefulSet"):
        if res.framework != "kubernetes":
            continue
        checked += 1
        if (res.properties.get("spec") or {}).get("replicas") == 1:
            out.append(_mk("CFG-NFR-001", "resilience", res, "Single-replica workload contradicts stated availability requirement",
                           f"Requirement: \"{nfr_text[:140]}\". {res.address} runs replicas: 1.", observed="replicas: 1",
                           expected="replicas >= 2", severity=Severity.HIGH, excerpt="replicas: 1", qualifier="k8s"))
    return out, checked


@register("OPS-ALARM-001", TOOL_ID, TOOL_NAME, "operability", "Infrastructure defines no alarms")
def no_alarms(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    infra = [r for r in ctx.resources if r.framework in ("terraform", "cloudformation") and not r.resource_type.startswith("data.")]
    if len(infra) < 5:
        return [], len(infra)
    alarm_types = ("aws_cloudwatch_metric_alarm", "aws_cloudwatch_composite_alarm", "AWS::CloudWatch::Alarm", "aws_sns_topic")
    if ctx.by_type(*alarm_types):
        return [], len(infra)
    return [_mk("OPS-ALARM-001", "operability", infra[0], "No alerting resources in the supplied infrastructure",
                f"{len(infra)} infrastructure resources are defined but none are alarms or notification topics. Alerting may be defined elsewhere.",
                observed="0 alarm resources", expected="Alarms on SLO-relevant signals with a notification route",
                level=EvidenceLevel.NEEDS_VALIDATION, severity=Severity.MEDIUM, components=[r.address for r in infra[:8]],
                qualifier="alarms")], len(infra)


@register("OPS-LOG-001", TOOL_ID, TOOL_NAME, "operability", "Load balancer access logging is disabled")
def lb_access_logs(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.by_type("aws_lb", "aws_alb"):
        checked += 1
        logs = get_path(res.properties, "access_logs", default=None)
        enabled = as_bool(get_path(logs, "enabled", default=None)) if isinstance(logs, dict) else None
        if enabled is not True:
            out.append(_mk("OPS-LOG-001", "operability", res, "Load balancer access logs are not enabled",
                           f"{res.address} does not enable access_logs; request-level forensics and troubleshooting are limited.",
                           observed="access_logs.enabled not true", expected="access_logs.enabled = true",
                           level=EvidenceLevel.CONFIRMED if enabled is False else EvidenceLevel.INFERRED, severity=Severity.LOW))
    return out, checked


@register("COMP-LOG-001", TOOL_ID, TOOL_NAME, "compliance", "No audit-trail resources defined")
def audit_logging(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    aws = [r for r in ctx.resources if r.resource_type.startswith(("aws_", "AWS::")) and not r.resource_type.startswith("data.")]
    if len(aws) < 5 or ctx.by_type("aws_cloudtrail", "AWS::CloudTrail::Trail"):
        return [], len(aws)
    return [_mk("COMP-LOG-001", "compliance", aws[0], "No audit trail defined for the account/environment",
                f"{len(aws)} AWS resources are defined with no CloudTrail trail. It may be enabled organisation-wide outside this code.",
                observed="no aws_cloudtrail", expected="Multi-region trail with log-file validation, delivered to a protected bucket",
                level=EvidenceLevel.NEEDS_VALIDATION, severity=Severity.MEDIUM, components=[r.address for r in aws[:6]],
                qualifier="trail")], len(aws)


@register("COMP-NET-001", TOOL_ID, TOOL_NAME, "compliance", "Network flow logging is not defined")
def flow_logs(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    vpcs = ctx.by_type("aws_vpc", "AWS::EC2::VPC")
    if not vpcs or ctx.by_type("aws_flow_log", "AWS::EC2::FlowLog"):
        return [], len(vpcs)
    return [_mk("COMP-NET-001", "compliance", vpcs[0], "VPC flow logs are not defined",
                f"{vpcs[0].address} has no associated flow log resource in the supplied definitions.", observed="no aws_flow_log",
                expected="Flow logs retained per policy", level=EvidenceLevel.NEEDS_VALIDATION, severity=Severity.LOW,
                qualifier="flow")], len(vpcs)


@register("COMP-DATA-001", TOOL_ID, TOOL_NAME, "compliance", "Personal-data store lacks retention and backup controls")
def personal_data_controls(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    """Stores explicitly tagged/named as holding personal data are checked for backup retention control."""
    out: list[ToolResult] = []
    checked = 0
    pii = re.compile(r"(?i)\b(pii|personal|gdpr|customer|cardholder|pan)\b")
    for res in ctx.by_type("aws_db_instance", "AWS::RDS::DBInstance", "aws_s3_bucket", "AWS::S3::Bucket"):
        tags = get_path(res.properties, "tags", "Tags", default={})
        text = f"{res.name} {tags}"
        if not pii.search(text):
            continue
        checked += 1
        retention_set = has(get_path(res.properties, "backup_retention_period", "BackupRetentionPeriod", "lifecycle_rule", "LifecycleConfiguration"))
        if not retention_set:
            out.append(_mk("COMP-DATA-001", "compliance", res, "Personal-data store has no declared retention/lifecycle control",
                           f"{res.address} is named/tagged as holding personal data but defines no retention or lifecycle setting.",
                           observed="no retention/lifecycle setting", expected="Retention and deletion mechanism aligned to data-protection policy",
                           level=EvidenceLevel.INFERRED, severity=Severity.MEDIUM))
    return out, checked
