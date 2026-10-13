"""Resilience rules: redundancy, backups, probes and failure-domain checks."""
from __future__ import annotations

from typing import Any

from ..models.domain import EvidenceLevel, Severity, ToolResult
from ..parsers.types import IacResource
from .base import ToolContext, as_bool, as_int, as_list, get_path, has, is_unresolved, make_result, register
from .k8s import REPLICATED_KINDS, containers

TOOL_ID, TOOL_NAME = "resilience_checks", "Built-in resilience checks"


def _mk(rule: str, res: IacResource | None, title: str, message: str, **kw: Any) -> ToolResult:
    domain = kw.pop("domain", "resilience")
    return make_result(tool_id=TOOL_ID, tool_name=TOOL_NAME, rule_id=rule, domain=domain, res=res, title=title, message=message, **kw)


@register("RES-HA-001", TOOL_ID, TOOL_NAME, "resilience", "Datastore deployed in a single availability zone")
def single_az_datastore(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.by_type("aws_db_instance", "AWS::RDS::DBInstance"):
        if has(get_path(res.properties, "replicate_source_db", "SourceDBInstanceIdentifier")):
            continue
        checked += 1
        engine = str(get_path(res.properties, "engine", "Engine", default="")).lower()
        if engine.startswith("aurora"):
            continue
        attr = "multi_az" if res.framework == "terraform" else "MultiAZ"
        val = get_path(res.properties, attr)
        if has(val) and is_unresolved(val):
            continue
        if has(val) and as_bool(val) is False:
            out.append(_mk("RES-HA-001", res, "Primary database runs in a single availability zone",
                           f"{res.address} sets {attr} = false, so an AZ outage or instance failure causes downtime until manual recovery.",
                           observed=f"{attr} = false", expected=f"{attr} = true", severity=Severity.HIGH, excerpt=f"{attr} = false"))
        elif not has(val):
            out.append(_mk("RES-HA-001", res, "Database multi-AZ not configured",
                           f"{res.address} does not set {attr}; the provider default is a single-AZ deployment.",
                           observed=f"{attr} not set", expected=f"{attr} = true", level=EvidenceLevel.INFERRED,
                           severity=Severity.MEDIUM))
    for res in ctx.by_type("aws_elasticache_replication_group", "AWS::ElastiCache::ReplicationGroup"):
        checked += 1
        attr = "automatic_failover_enabled" if res.framework == "terraform" else "AutomaticFailoverEnabled"
        val = get_path(res.properties, attr)
        if (has(val) and as_bool(val) is False) or not has(val):
            out.append(_mk("RES-HA-001", res, "Cache has no automatic failover",
                           f"{res.address} has {attr} {'= false' if has(val) else 'unset'}.", observed=f"{attr} {'= false' if has(val) else 'not set'}",
                           expected=f"{attr} = true", level=EvidenceLevel.CONFIRMED if has(val) else EvidenceLevel.INFERRED,
                           severity=Severity.MEDIUM))
    return out, checked


def _hpa_min_replicas(ctx: ToolContext) -> dict[str, int]:
    mins: dict[str, int] = {}
    for hpa in ctx.by_type("HorizontalPodAutoscaler"):
        spec = hpa.properties.get("spec") or {}
        target = (spec.get("scaleTargetRef") or {}).get("name")
        if target:
            mins[str(target)] = int(spec.get("minReplicas") or 1)
    return mins


@register("RES-HA-002", TOOL_ID, TOOL_NAME, "resilience", "Workload runs as a single replica")
def single_replica(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    hpa_min = _hpa_min_replicas(ctx)
    for res in ctx.by_type(*REPLICATED_KINDS):
        if res.framework != "kubernetes":
            continue
        checked += 1
        spec = res.properties.get("spec") or {}
        replicas = spec.get("replicas")
        if hpa_min.get(res.name, 0) >= 2:
            continue
        if replicas == 1:
            out.append(_mk("RES-HA-002", res, "Workload configured with a single replica",
                           f"{res.address} sets replicas: 1; any pod or node failure interrupts the service.",
                           observed="spec.replicas = 1", expected="replicas >= 2 across failure domains", severity=Severity.HIGH,
                           excerpt="replicas: 1"))
        elif replicas is None and res.resource_type == "Deployment":
            out.append(_mk("RES-HA-002", res, "Replica count not specified (defaults to 1)",
                           f"{res.address} omits spec.replicas; Kubernetes defaults to one replica.",
                           observed="spec.replicas not set", expected="replicas >= 2", level=EvidenceLevel.INFERRED,
                           severity=Severity.MEDIUM))
    return out, checked


@register("RES-HA-003", TOOL_ID, TOOL_NAME, "resilience", "Container has no readiness or liveness probe")
def missing_probes(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.by_type("Deployment", "StatefulSet", "DaemonSet"):
        if res.framework != "kubernetes":
            continue
        for c in containers(res):
            checked += 1
            missing = [p for p in ("readinessProbe", "livenessProbe") if not c.get(p)]
            if missing:
                sev = Severity.MEDIUM if "readinessProbe" in missing else Severity.LOW
                out.append(_mk("RES-HA-003", res, "Container lacks health probes",
                               f"Container '{c.get('name')}' in {res.address} defines no {' or '.join(missing)}; "
                               "traffic can reach unready pods and hung processes are not restarted.",
                               observed=f"missing: {', '.join(missing)}", expected="readinessProbe and livenessProbe configured",
                               severity=sev, excerpt=f"container {c.get('name')}: no {', '.join(missing)}", qualifier=str(c.get("name"))))
    return out, checked


@register("RES-HA-004", TOOL_ID, TOOL_NAME, "resilience", "Compute capacity is not spread or self-healing")
def compute_redundancy(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.by_type("aws_autoscaling_group", "AWS::AutoScaling::AutoScalingGroup"):
        checked += 1
        mn = as_int(get_path(res.properties, "min_size", "MinSize", default=None))
        subnets = as_list(get_path(res.properties, "vpc_zone_identifier", "VPCZoneIdentifier", default=[]))
        azs = as_list(get_path(res.properties, "availability_zones", "AvailabilityZones", default=[]))
        spread = max(len(subnets), len(azs))
        if mn is not None and mn < 2:
            out.append(_mk("RES-HA-004", res, "Auto Scaling group allows a single instance",
                           f"{res.address} has min_size = {mn}.", observed=f"min_size = {mn}", expected="min_size >= 2",
                           severity=Severity.MEDIUM, excerpt=f"min_size = {mn}", qualifier="min"))
        if spread == 1:
            out.append(_mk("RES-HA-004", res, "Auto Scaling group confined to one subnet/AZ",
                           f"{res.address} references a single subnet/AZ.", observed="1 subnet/AZ", expected=">= 2 AZs",
                           severity=Severity.HIGH, qualifier="spread"))
    asg_count = len(ctx.by_type("aws_autoscaling_group", "AWS::AutoScaling::AutoScalingGroup"))
    for res in ctx.by_type("aws_instance", "AWS::EC2::Instance"):
        checked += 1
        if asg_count == 0:
            out.append(_mk("RES-HA-004", res, "Standalone instance without Auto Scaling",
                           f"{res.address} is a stand-alone instance and the definitions contain no Auto Scaling group.",
                           observed="single instance, no ASG", expected="ASG across >= 2 AZs or documented recovery automation",
                           level=EvidenceLevel.INFERRED, severity=Severity.MEDIUM))
    return out, checked


@register("RES-BAK-001", TOOL_ID, TOOL_NAME, "resilience", "Automated backups are disabled")
def backups_disabled(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.by_type("aws_db_instance", "AWS::RDS::DBInstance"):
        if has(get_path(res.properties, "replicate_source_db")):
            continue
        checked += 1
        attr = "backup_retention_period" if res.framework == "terraform" else "BackupRetentionPeriod"
        val = get_path(res.properties, attr)
        if has(val) and as_int(val) == 0:
            out.append(_mk("RES-BAK-001", res, "Automated database backups are turned off",
                           f"{res.address} sets {attr} = 0; point-in-time recovery is unavailable.",
                           observed=f"{attr} = 0", expected=f"{attr} >= 7", severity=Severity.HIGH, excerpt=f"{attr} = 0"))
    return out, checked


@register("RES-NAT-001", TOOL_ID, TOOL_NAME, "resilience", "Single NAT gateway serves multiple subnets")
def single_nat(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    nats = ctx.by_type("aws_nat_gateway", "AWS::EC2::NatGateway")
    subnets = ctx.by_type("aws_subnet", "AWS::EC2::Subnet")
    if len(nats) != 1:
        return [], len(nats)
    private = [s for s in subnets if "private" in s.name.lower() or has(get_path(s.properties, "map_public_ip_on_launch")) is False]
    azs = {str(get_path(s.properties, "availability_zone", "AvailabilityZone", default=s.name)) for s in (private or subnets)}
    if len(azs) < 2:
        return [], 1
    nat = nats[0]
    return [_mk("RES-NAT-001", nat, "One NAT gateway is shared by several availability zones",
                f"{nat.address} is the only NAT gateway but {len(azs)} subnets/zones exist; an outage of its AZ removes outbound access for all.",
                observed=f"1 NAT gateway, {len(azs)} subnet zones", expected="One NAT gateway per AZ for production workloads",
                level=EvidenceLevel.INFERRED, severity=Severity.MEDIUM)], 1


@register("RES-MSG-001", TOOL_ID, TOOL_NAME, "resilience", "Queue has no dead-letter configuration")
def queue_without_dlq(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    queues = ctx.by_type("aws_sqs_queue", "AWS::SQS::Queue")
    for res in queues:
        checked += 1
        name = res.name.lower()
        if any(k in name for k in ("dlq", "dead", "deadletter", "dead_letter")):
            continue
        redrive = get_path(res.properties, "redrive_policy", "RedrivePolicy")
        if has(redrive):
            continue
        has_policy_resource = any(
            r.resource_type == "aws_sqs_queue_redrive_policy" and res.name in str(r.properties) for r in ctx.resources
        )
        if has_policy_resource:
            continue
        out.append(_mk("RES-MSG-001", res, "Message queue has no dead-letter queue",
                       f"{res.address} defines no redrive policy, so poison messages are retried until retention expires or are lost.",
                       observed="redrive_policy not set", expected="Redrive policy with maxReceiveCount and a DLQ + alarm",
                       level=EvidenceLevel.CONFIRMED, severity=Severity.MEDIUM, domain="resilience"))
    return out, checked


@register("RES-DEL-001", TOOL_ID, TOOL_NAME, "resilience", "Data store has no deletion safeguard")
def deletion_protection(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.by_type("aws_db_instance", "AWS::RDS::DBInstance"):
        checked += 1
        dp = get_path(res.properties, "deletion_protection", "DeletionProtection")
        skip = get_path(res.properties, "skip_final_snapshot")
        if has(skip) and as_bool(skip) is True and not (has(dp) and as_bool(dp) is True):
            out.append(_mk("RES-DEL-001", res, "Database can be deleted without a final snapshot",
                           f"{res.address} sets skip_final_snapshot = true and has no deletion protection.",
                           observed="skip_final_snapshot = true; deletion_protection off", expected="deletion_protection = true or final snapshot",
                           severity=Severity.LOW, excerpt="skip_final_snapshot = true"))
    return out, checked

