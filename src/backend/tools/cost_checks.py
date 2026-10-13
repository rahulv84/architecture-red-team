"""FinOps rules.  These flag *cost risk*; they never invent prices."""
from __future__ import annotations

import re
from typing import Any

from ..models.domain import EvidenceLevel, Severity, ToolResult
from ..parsers.types import IacResource
from .base import ToolContext, as_int, get_path, has, make_result, references_in, register
from .k8s import REPLICATED_KINDS

TOOL_ID, TOOL_NAME = "cost_checks", "Built-in cost-risk checks"


def _mk(rule: str, res: IacResource | None, title: str, message: str, **kw: Any) -> ToolResult:
    return make_result(tool_id=TOOL_ID, tool_name=TOOL_NAME, rule_id=rule, domain="finops", res=res, title=title, message=message, **kw)


def _size_rank(size: str) -> int:
    m = re.search(r"\.(\d+)xlarge$", size)
    if m:
        return int(m.group(1))
    return 1 if size.endswith(".xlarge") else 0


@register("COST-SIZE-001", TOOL_ID, TOOL_NAME, "finops", "Large instance class without utilization evidence")
def large_instances(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    specs = [("aws_instance", "instance_type"), ("aws_db_instance", "instance_class"), ("aws_elasticache_replication_group", "node_type"),
             ("AWS::EC2::Instance", "InstanceType"), ("AWS::RDS::DBInstance", "DBInstanceClass")]
    for rtype, attr in specs:
        for res in ctx.by_type(rtype):
            checked += 1
            size = get_path(res.properties, attr, default=None)
            if isinstance(size, str) and _size_rank(size) >= 4:
                out.append(_mk("COST-SIZE-001", res, "Large instance class selected",
                               f"{res.address} uses {size}. No utilization or sizing data was supplied to justify this capacity.",
                               observed=f"{attr} = {size}", expected="Sizing backed by load test or utilization data",
                               level=EvidenceLevel.NEEDS_VALIDATION, severity=Severity.MEDIUM, excerpt=f"{attr} = {size}"))
    return out, checked


@register("COST-LOG-001", TOOL_ID, TOOL_NAME, "finops", "Log group retains data forever")
def unbounded_log_retention(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.by_type("aws_cloudwatch_log_group", "AWS::Logs::LogGroup"):
        checked += 1
        attr = "retention_in_days" if res.framework == "terraform" else "RetentionInDays"
        if not has(get_path(res.properties, attr)):
            out.append(_mk("COST-LOG-001", res, "Log group has no retention period",
                           f"{res.address} sets no {attr}; logs are kept indefinitely and storage cost grows without bound.",
                           observed=f"{attr} not set", expected=f"{attr} set per data-retention policy", severity=Severity.LOW))
    return out, checked


@register("COST-S3-001", TOOL_ID, TOOL_NAME, "finops", "Bucket has no lifecycle policy")
def no_lifecycle(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    buckets = ctx.by_type("aws_s3_bucket", "AWS::S3::Bucket")
    lifecycle_targets: set[str] = set()
    for lc in ctx.by_type("aws_s3_bucket_lifecycle_configuration"):
        lifecycle_targets |= references_in(lc.properties) | {str(get_path(lc.properties, "bucket", default=""))}
    for res in buckets:
        if res.framework == "cloudformation":
            has_lc = has(get_path(res.properties, "LifecycleConfiguration"))
        else:
            has_lc = has(get_path(res.properties, "lifecycle_rule")) or any(res.address in t or res.name in t for t in lifecycle_targets)
        if not has_lc:
            out.append(_mk("COST-S3-001", res, "Object storage has no lifecycle rules",
                           f"{res.address} defines no lifecycle configuration; data stays in the standard tier indefinitely.",
                           observed="no lifecycle configuration", expected="Tiering/expiry rules aligned to retention requirements",
                           level=EvidenceLevel.INFERRED, severity=Severity.LOW))
    return out, len(buckets)


@register("COST-NAT-001", TOOL_ID, TOOL_NAME, "finops", "NAT egress used without private endpoints for AWS services")
def nat_without_endpoints(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    nats = ctx.by_type("aws_nat_gateway", "AWS::EC2::NatGateway")
    buckets = ctx.by_type("aws_s3_bucket", "AWS::S3::Bucket")
    if not nats or not buckets:
        return [], len(nats)
    endpoints = ctx.by_type("aws_vpc_endpoint", "AWS::EC2::VPCEndpoint")
    has_s3_ep = any("s3" in str(get_path(e.properties, "service_name", "ServiceName", default="")).lower() for e in endpoints)
    if has_s3_ep:
        return [], len(nats)
    nat = nats[0]
    return [_mk("COST-NAT-001", nat, "S3 traffic likely traverses the NAT gateway",
                "A NAT gateway and S3 bucket(s) exist but no S3 gateway endpoint is defined; private-subnet S3 traffic would incur NAT data-processing charges.",
                observed="NAT present, no S3 VPC endpoint", expected="S3 gateway endpoint in route tables of private subnets",
                level=EvidenceLevel.INFERRED, severity=Severity.LOW, components=[n.address for n in nats] + [b.address for b in buckets],
                qualifier="s3-ep")], len(nats)


@register("COST-SCALE-001", TOOL_ID, TOOL_NAME, "finops", "Fixed capacity without autoscaling")
def fixed_capacity(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    hpa_targets = {str((h.properties.get("spec") or {}).get("scaleTargetRef", {}).get("name")) for h in ctx.by_type("HorizontalPodAutoscaler")}
    for res in ctx.by_type(*REPLICATED_KINDS):
        if res.framework != "kubernetes" or res.resource_type == "StatefulSet":
            continue
        checked += 1
        replicas = (res.properties.get("spec") or {}).get("replicas")
        if isinstance(replicas, int) and replicas >= 3 and res.name not in hpa_targets:
            out.append(_mk("COST-SCALE-001", res, "Fixed replica count without autoscaler",
                           f"{res.address} runs {replicas} replicas with no HorizontalPodAutoscaler; capacity is paid for at peak all day.",
                           observed=f"replicas = {replicas}, no HPA", expected="HPA scaling on utilization", level=EvidenceLevel.INFERRED,
                           severity=Severity.LOW))
    for res in ctx.by_type("aws_autoscaling_group", "AWS::AutoScaling::AutoScalingGroup"):
        checked += 1
        mn = as_int(get_path(res.properties, "min_size", "MinSize", default=None))
        mx = as_int(get_path(res.properties, "max_size", "MaxSize", default=None))
        if mn is not None and mx is not None and mn == mx and mn >= 3:
            out.append(_mk("COST-SCALE-001", res, "Auto Scaling group is pinned to a fixed size",
                           f"{res.address} has min_size = max_size = {mn}.", observed=f"min = max = {mn}", expected="Elastic range",
                           level=EvidenceLevel.INFERRED, severity=Severity.LOW))
    return out, checked


@register("COST-XFER-001", TOOL_ID, TOOL_NAME, "finops", "Cross-region replication adds transfer and storage cost")
def cross_region_replication(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    for res in ctx.by_type("aws_s3_bucket_replication_configuration", "aws_db_instance_automated_backups_replication", "aws_dynamodb_table_replica"):
        out.append(_mk("COST-XFER-001", res, "Cross-region replication configured",
                       f"{res.address} replicates data to another region, which incurs inter-region transfer and duplicate storage cost.",
                       observed="replication resource present", expected="Documented RPO/DR justification and cost estimate",
                       level=EvidenceLevel.NEEDS_VALIDATION, severity=Severity.LOW))
    return out, len(out)


@register("COST-TAG-001", TOOL_ID, TOOL_NAME, "finops", "Resources lack cost-allocation tags")
def missing_tags(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    taggable = ctx.by_type(
        "aws_instance", "aws_db_instance", "aws_s3_bucket", "aws_lb", "aws_sqs_queue", "aws_autoscaling_group",
        "AWS::EC2::Instance", "AWS::RDS::DBInstance", "AWS::S3::Bucket",
    )
    if len(taggable) < 3:
        return [], len(taggable)
    untagged = [r for r in taggable if not has(get_path(r.properties, "tags", "Tags", "tags_all"))]
    if len(untagged) / len(taggable) < 0.5:
        return [], len(taggable)
    return [_mk("COST-TAG-001", untagged[0], "Most billable resources carry no tags",
                f"{len(untagged)} of {len(taggable)} taggable resources have no tags, which limits cost allocation and showback.",
                observed=f"{len(untagged)}/{len(taggable)} untagged", expected="Mandatory cost-center / owner tags",
                level=EvidenceLevel.CONFIRMED, severity=Severity.LOW, components=[r.address for r in untagged], qualifier="tags")], len(taggable)
