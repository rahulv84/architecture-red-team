"""Security rules over structured infrastructure definitions."""
from __future__ import annotations

import json
import re
from typing import Any

from ..models.domain import EvidenceLevel, Severity, ToolResult
from ..parsers.types import IacResource
from .base import (
    ToolContext,
    as_bool,
    as_int,
    as_list,
    get_path,
    has,
    is_unresolved,
    make_result,
    register,
    stringify,
)

TOOL_ID, TOOL_NAME = "security_checks", "Built-in security checks"
WORLD = {"0.0.0.0/0", "::/0"}
SENSITIVE_PORTS = {22: "SSH", 3389: "RDP", 1521: "Oracle", 3306: "MySQL", 5432: "PostgreSQL", 1433: "SQL Server", 6379: "Redis",
                   27017: "MongoDB", 9200: "Elasticsearch", 5672: "AMQP", 9092: "Kafka", 2049: "NFS", 445: "SMB"}
_SECRET_KEY = re.compile(r"(?i)(^|[_\-.])(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential|masteruserpassword)($|[_\-.])|password$|secret$")
_AWS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")


def _mk(rule: str, res: IacResource | None, title: str, message: str, **kw: Any) -> ToolResult:
    return make_result(tool_id=TOOL_ID, tool_name=TOOL_NAME, rule_id=rule, domain="security", res=res, title=title, message=message, **kw)


# --------------------------------------------------------------------------- network exposure
def _ingress_rules(res: IacResource) -> list[dict[str, Any]]:
    """Normalise ingress permissions across Terraform and CloudFormation to dicts."""
    rules: list[dict[str, Any]] = []
    p = res.properties
    if res.resource_type == "aws_security_group":
        for blk in as_list(get_path(p, "ingress")):
            if isinstance(blk, dict):
                cidrs = as_list(blk.get("cidr_blocks")) + as_list(blk.get("ipv6_cidr_blocks"))
                rules.append({"from": as_int(blk.get("from_port")), "to": as_int(blk.get("to_port")),
                              "proto": str(blk.get("protocol", "tcp")), "cidrs": cidrs})
    elif res.resource_type == "aws_security_group_rule" and str(get_path(p, "type", default="")).lower() == "ingress":
        cidrs = as_list(get_path(p, "cidr_blocks", default=[])) + as_list(get_path(p, "ipv6_cidr_blocks", default=[]))
        rules.append({"from": as_int(get_path(p, "from_port", default=None)), "to": as_int(get_path(p, "to_port", default=None)),
                      "proto": str(get_path(p, "protocol", default="tcp")), "cidrs": cidrs})
    elif res.resource_type == "aws_vpc_security_group_ingress_rule":
        cidr = as_list(get_path(p, "cidr_ipv4", default=[])) + as_list(get_path(p, "cidr_ipv6", default=[]))
        rules.append({"from": as_int(get_path(p, "from_port", default=None)), "to": as_int(get_path(p, "to_port", default=None)),
                      "proto": str(get_path(p, "ip_protocol", default="tcp")), "cidrs": cidr})
    elif res.resource_type == "AWS::EC2::SecurityGroup":
        for blk in as_list(get_path(p, "SecurityGroupIngress", default=[])):
            if isinstance(blk, dict):
                rules.append({"from": as_int(blk.get("FromPort")), "to": as_int(blk.get("ToPort")),
                              "proto": str(blk.get("IpProtocol", "tcp")), "cidrs": [blk.get("CidrIp"), blk.get("CidrIpv6")]})
    elif res.resource_type == "AWS::EC2::SecurityGroupIngress":
        rules.append({"from": as_int(get_path(p, "FromPort", default=None)), "to": as_int(get_path(p, "ToPort", default=None)),
                      "proto": str(get_path(p, "IpProtocol", default="tcp")), "cidrs": [get_path(p, "CidrIp", default=None), get_path(p, "CidrIpv6", default=None)]})
    return rules


@register("SEC-NET-001", TOOL_ID, TOOL_NAME, "security", "Security group allows the world on non-web ports")
def open_ingress(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    types = ("aws_security_group", "aws_security_group_rule", "aws_vpc_security_group_ingress_rule",
             "AWS::EC2::SecurityGroup", "AWS::EC2::SecurityGroupIngress")
    for res in ctx.by_type(*types):
        checked += 1
        for rule in _ingress_rules(res):
            if not any(c in WORLD for c in rule["cidrs"] if isinstance(c, str)):
                continue
            lo, hi, proto = rule["from"], rule["to"], str(rule["proto"]).lower()
            if proto in ("icmp", "icmpv6", "1", "58"):
                continue
            if proto in ("-1", "all"):
                lo, hi = 0, 65535
            if lo is None or hi is None:
                continue
            if lo == hi and lo in (80, 443):
                continue  # public web ports are expected
            all_ports = lo <= 0 and hi >= 65535
            exposed = {p: n for p, n in SENSITIVE_PORTS.items() if lo <= p <= hi}
            world = ", ".join(sorted(c for c in rule["cidrs"] if c in WORLD))
            observed = f"{proto} {lo}-{hi} from {world}"
            if all_ports or exposed:
                critical_ports = {22, 3389, 3306, 5432, 1433, 6379, 27017, 9200}
                sev = Severity.CRITICAL if (all_ports or critical_ports & set(exposed)) else Severity.HIGH
                names = "all ports" if all_ports else ", ".join(f"{n}/{p}" for p, n in sorted(exposed.items()))
                out.append(_mk("SEC-NET-001", res, "Network rule exposes sensitive ports to the internet",
                               f"{res.address} allows {observed}, which exposes {names}.",
                               observed=observed, expected="Ingress restricted to specific CIDRs/security groups; only 80/443 public",
                               severity=sev, excerpt=observed, qualifier=observed))
            else:
                out.append(_mk("SEC-NET-001", res, "Security group opens a non-web port range to the internet",
                               f"{res.address} allows {observed}.", observed=observed,
                               expected="Only 80/443 public; other ports restricted", severity=Severity.MEDIUM,
                               excerpt=observed, qualifier=observed))
    return out, checked


@register("SEC-NET-002", TOOL_ID, TOOL_NAME, "security", "Database or cache is publicly accessible")
def public_database(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.by_type("aws_db_instance", "aws_rds_cluster_instance", "aws_redshift_cluster", "aws_dms_replication_instance",
                           "AWS::RDS::DBInstance", "AWS::Redshift::Cluster"):
        checked += 1
        val = get_path(res.properties, "publicly_accessible", "PubliclyAccessible")
        if has(val) and not is_unresolved(val) and as_bool(val) is True:
            out.append(_mk("SEC-NET-002", res, "Datastore is directly reachable from the internet",
                           f"{res.address} sets publicly_accessible = true; the database endpoint is assigned a public address.",
                           observed="publicly_accessible = true", expected="publicly_accessible = false (private subnets only)",
                           severity=Severity.CRITICAL, excerpt="publicly_accessible = true"))
    return out, checked


@register("SEC-STO-001", TOOL_ID, TOOL_NAME, "security", "Object storage is publicly readable or writable")
def public_storage(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.by_type("aws_s3_bucket", "aws_s3_bucket_acl", "AWS::S3::Bucket"):
        checked += 1
        acl = get_path(res.properties, "acl", "AccessControl")
        if has(acl) and str(acl).lower().replace("_", "-") in ("public-read", "public-read-write", "publicread", "publicreadwrite"):
            out.append(_mk("SEC-STO-001", res, "Bucket ACL grants public access",
                           f"{res.address} uses the public ACL '{acl}'.", observed=f"acl = {acl}",
                           expected="Private ACL with public access block enabled", severity=Severity.CRITICAL,
                           excerpt=f"acl = {acl}"))
    for res in ctx.by_type("aws_s3_bucket_public_access_block"):
        checked += 1
        flags = {k: as_bool(get_path(res.properties, k, default=None)) for k in
                 ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets")}
        disabled = [k for k, v in flags.items() if v is False]
        if disabled:
            out.append(_mk("SEC-STO-001", res, "S3 public access block is disabled",
                           f"{res.address} disables {', '.join(disabled)}.", observed=", ".join(f"{k}=false" for k in disabled),
                           expected="All four public access block settings true", severity=Severity.HIGH,
                           excerpt=", ".join(disabled)))
    return out, checked


# --------------------------------------------------------------------------- encryption
@register("SEC-ENC-001", TOOL_ID, TOOL_NAME, "security", "Storage resource is not encrypted at rest")
def unencrypted_storage(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    specs = [
        ("aws_db_instance", "storage_encrypted", "AWS::RDS::DBInstance", "StorageEncrypted", "database storage"),
        ("aws_ebs_volume", "encrypted", "AWS::EC2::Volume", "Encrypted", "EBS volume"),
        ("aws_efs_file_system", "encrypted", "AWS::EFS::FileSystem", "Encrypted", "EFS file system"),
        ("aws_elasticache_replication_group", "at_rest_encryption_enabled", "AWS::ElastiCache::ReplicationGroup", "AtRestEncryptionEnabled", "cache data"),
    ]
    for tf_type, tf_attr, cfn_type, cfn_attr, label in specs:
        for res in ctx.by_type(tf_type, cfn_type):
            if res.resource_type == "aws_db_instance" and has(get_path(res.properties, "replicate_source_db")):
                continue
            checked += 1
            attr = tf_attr if res.framework == "terraform" else cfn_attr
            val = get_path(res.properties, attr)
            if has(val) and is_unresolved(val):
                continue
            if has(val) and as_bool(val) is False:
                out.append(_mk("SEC-ENC-001", res, f"Encryption at rest disabled for {label}",
                               f"{res.address} explicitly sets {attr} = false.", observed=f"{attr} = false",
                               expected=f"{attr} = true", severity=Severity.HIGH, excerpt=f"{attr} = false"))
            elif not has(val) and not has(get_path(res.properties, "kms_key_id", "KmsKeyId")):
                out.append(_mk("SEC-ENC-001", res, f"Encryption at rest not configured for {label}",
                               f"{res.address} does not set {attr}; the provider default for this resource is unencrypted.",
                               observed=f"{attr} not set", expected=f"{attr} = true", level=EvidenceLevel.INFERRED,
                               severity=Severity.MEDIUM))
    return out, checked


@register("SEC-ENC-002", TOOL_ID, TOOL_NAME, "security", "Traffic is served without transport encryption")
def plaintext_transport(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.by_type("aws_lb_listener", "aws_alb_listener", "AWS::ElasticLoadBalancingV2::Listener"):
        checked += 1
        proto = str(get_path(res.properties, "protocol", "Protocol", default="HTTP")).upper()
        port = as_int(get_path(res.properties, "port", "Port", default=None))
        if proto in ("HTTP", "TCP") and port not in (None, 443):
            actions = as_list(get_path(res.properties, "default_action", "DefaultActions", default=[]))
            redirects = any(isinstance(a, dict) and str(a.get("type", a.get("Type", ""))).lower() == "redirect" for a in actions)
            if not redirects and proto == "HTTP":
                out.append(_mk("SEC-ENC-002", res, "Listener accepts plaintext HTTP without redirect to HTTPS",
                               f"{res.address} listens on {proto}/{port} and forwards without an HTTPS redirect.",
                               observed=f"protocol = {proto}, port = {port}, no redirect action", expected="HTTPS listener or HTTP->HTTPS redirect",
                               severity=Severity.MEDIUM, excerpt=f"protocol = {proto}"))
    for res in ctx.by_type("Ingress"):
        checked += 1
        spec = get_path(res.properties, "spec", default={})
        if isinstance(spec, dict) and not spec.get("tls"):
            out.append(_mk("SEC-ENC-002", res, "Kubernetes Ingress has no TLS configuration",
                           f"{res.address} declares no spec.tls; TLS may terminate elsewhere but is not shown in the manifest.",
                           observed="spec.tls absent", expected="spec.tls with a certificate secret", level=EvidenceLevel.INFERRED,
                           severity=Severity.MEDIUM))
    return out, checked


# --------------------------------------------------------------------------- secrets
def _walk_literals(value: Any, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        # Kubernetes env var shape: {name: DB_PASSWORD, value: literal}
        if "name" in value and "value" in value and isinstance(value["value"], str) and not path.endswith("valueFrom"):
            if _SECRET_KEY.search(str(value["name"])):
                found.append((f"{path}.{value['name']}", value["value"]))
        for k, v in value.items():
            key_path = f"{path}.{k}" if path else str(k)
            if isinstance(v, str) and _SECRET_KEY.search(str(k)) and k not in ("name",):
                found.append((key_path, v))
            else:
                found.extend(_walk_literals(v, key_path))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found.extend(_walk_literals(v, f"{path}[{i}]"))
    return found


@register("SEC-SEC-001", TOOL_ID, TOOL_NAME, "security", "Credential literal committed in infrastructure definition")
def hardcoded_secrets(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.resources:
        if res.resource_type.startswith("data."):
            continue
        checked += 1
        props = res.properties
        for path, value in _walk_literals(props):
            if not isinstance(value, str) or not value.strip():
                continue
            if is_unresolved(value) or value.startswith(("arn:", "/")) or re.fullmatch(r"\s*\$\{[^}]*\}\s*", value):
                continue
            if re.search(r"(?i)(secret(s)?_?(name|arn|id)|_arn$|_name$|_id$|_file$|path$)", path.split(".")[-1]):
                continue
            out.append(_mk("SEC-SEC-001", res, "Credential value hard-coded in configuration",
                           f"{res.address} contains a literal value at '{path}' (value redacted).",
                           observed=f"{path} = <literal, {len(value)} chars>", expected="Reference to a secret manager or runtime-injected secret",
                           severity=Severity.HIGH, excerpt=f"{path} = ****", qualifier=path))
        text = json.dumps(props, default=str)
        if _AWS_KEY.search(text):
            out.append(_mk("SEC-SEC-001", res, "AWS access key identifier present in configuration",
                           f"{res.address} contains a string matching an AWS access key ID pattern (value redacted).",
                           observed="AKIA… pattern found", expected="No static cloud credentials in IaC",
                           severity=Severity.CRITICAL, excerpt="AKIA****", qualifier="akia"))
        if res.resource_type == "Secret" and res.framework == "kubernetes":
            data = {**(props.get("data") or {}), **(props.get("stringData") or {})}
            if data:
                out.append(_mk("SEC-SEC-001", res, "Kubernetes Secret values are committed in the manifest",
                               f"{res.address} carries {len(data)} secret value(s) inline; base64 is encoding, not encryption.",
                               observed=f"{len(data)} inline key(s)", expected="Secrets injected from an external secret manager or sealed",
                               level=EvidenceLevel.CONFIRMED, severity=Severity.MEDIUM, qualifier="k8s-secret"))
    return out, checked


# --------------------------------------------------------------------------- IAM
def _statements_from_policy(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        stmts = value.get("Statement", value.get("statement"))
        return [s for s in as_list(stmts) if isinstance(s, dict)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value.strip())
            return _statements_from_policy(parsed)
        except ValueError:
            pass
        found: list[dict[str, Any]] = []
        for blk in re.findall(r"\{[^{}]*\}", value):
            if re.search(r'"?Action"?\s*[:=]', blk):
                stmt: dict[str, Any] = {}
                for key in ("Effect", "Action", "Resource"):
                    m = re.search(rf'"?{key}"?\s*[:=]\s*(\[[^\]]*\]|"[^"]*")', blk)
                    if m:
                        raw = m.group(1)
                        stmt[key] = re.findall(r'"([^"]*)"', raw) if raw.startswith("[") else raw.strip('"')
                found.append(stmt)
        return found
    return []


@register("SEC-IAM-001", TOOL_ID, TOOL_NAME, "security", "IAM policy grants wildcard actions or resources")
def wildcard_iam(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    candidates: list[tuple[IacResource, Any]] = []
    for res in ctx.by_type("aws_iam_policy", "aws_iam_role_policy", "aws_iam_user_policy", "aws_iam_group_policy"):
        candidates.append((res, get_path(res.properties, "policy", default=None)))
    for res in ctx.by_type("AWS::IAM::Policy", "AWS::IAM::ManagedPolicy", "AWS::IAM::Role", "AWS::IAM::User"):
        pd = get_path(res.properties, "PolicyDocument", default=None)
        if pd is not None:
            candidates.append((res, pd))
        for pol in as_list(get_path(res.properties, "Policies", default=[])):
            if isinstance(pol, dict):
                candidates.append((res, pol.get("PolicyDocument")))
    for res in ctx.by_type("data.aws_iam_policy_document"):
        stmts = []
        for st in as_list(get_path(res.properties, "statement", default=[])):
            if isinstance(st, dict):
                stmts.append({"Effect": st.get("effect", "Allow"), "Action": st.get("actions", []), "Resource": st.get("resources", [])})
        candidates.append((res, {"Statement": stmts}))
    for res, policy in candidates:
        checked += 1
        for stmt in _statements_from_policy(policy):
            if str(stmt.get("Effect", "Allow")).lower() != "allow":
                continue
            actions = [str(a) for a in as_list(stmt.get("Action"))]
            resources = [str(r) for r in as_list(stmt.get("Resource"))]
            star_action = any(a == "*" for a in actions)
            service_star = [a for a in actions if a.endswith(":*") and a != "*"]
            star_res = any(r == "*" for r in resources)
            if star_action and star_res:
                sev, msg = Severity.CRITICAL, "allows all actions on all resources"
            elif star_action or (service_star and star_res):
                sev, msg = Severity.HIGH, f"allows {', '.join(service_star or ['*'])} on {'all resources' if star_res else 'selected resources'}"
            else:
                continue
            out.append(_mk("SEC-IAM-001", res, "IAM policy is overly permissive",
                           f"{res.address} {msg}.", observed=f"Action={stringify(actions, 80)} Resource={stringify(resources, 80)}",
                           expected="Least-privilege actions scoped to specific resources", severity=sev,
                           excerpt=f'Action: {", ".join(actions)[:60]}; Resource: {", ".join(resources)[:40]}', qualifier=msg))
    return out, checked


# --------------------------------------------------------------------------- kubernetes
def _pod_spec(res: IacResource) -> dict[str, Any] | None:
    from .k8s import pod_spec

    return pod_spec(res)


@register("SEC-K8S-001", TOOL_ID, TOOL_NAME, "security", "Workload runs with elevated privileges")
def privileged_workloads(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.resources:
        spec = _pod_spec(res)
        if spec is None:
            continue
        checked += 1
        for flag in ("hostNetwork", "hostPID", "hostIPC"):
            if spec.get(flag) is True:
                out.append(_mk("SEC-K8S-001", res, f"Pod uses {flag}", f"{res.address} sets {flag}: true, sharing the node namespace.",
                               observed=f"{flag}: true", expected=f"{flag} unset", severity=Severity.MEDIUM, excerpt=f"{flag}: true", qualifier=flag))
        for c in as_list(spec.get("containers")):
            sc = (c or {}).get("securityContext") or {}
            if sc.get("privileged") is True:
                out.append(_mk("SEC-K8S-001", res, "Privileged container", f"Container '{c.get('name')}' in {res.address} runs privileged.",
                               observed="securityContext.privileged: true", expected="privileged: false", severity=Severity.HIGH,
                               excerpt="privileged: true", qualifier=str(c.get("name"))))
            elif sc.get("allowPrivilegeEscalation") is True:
                out.append(_mk("SEC-K8S-001", res, "Container allows privilege escalation",
                               f"Container '{c.get('name')}' in {res.address} sets allowPrivilegeEscalation: true.",
                               observed="allowPrivilegeEscalation: true", expected="false", severity=Severity.MEDIUM,
                               qualifier=f"esc-{c.get('name')}"))
    return out, checked


@register("SEC-K8S-002", TOOL_ID, TOOL_NAME, "security", "No NetworkPolicy isolates workloads")
def missing_network_policy(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    workloads = [r for r in ctx.resources if _pod_spec(r) is not None and r.resource_type in ("Deployment", "StatefulSet", "DaemonSet")]
    if len(workloads) < 2 or ctx.by_type("NetworkPolicy"):
        return [], len(workloads)
    first = workloads[0]
    return [_mk("SEC-K8S-002", first, "No NetworkPolicy resources in supplied manifests",
                f"{len(workloads)} workloads are defined but the manifests contain no NetworkPolicy, so pod-to-pod traffic is unrestricted by default "
                "(unless a CNI default-deny is applied elsewhere).",
                observed=f"{len(workloads)} workloads, 0 NetworkPolicy", expected="Default-deny plus explicit allow policies",
                level=EvidenceLevel.INFERRED, severity=Severity.MEDIUM, components=[w.address for w in workloads],
                qualifier="no-netpol")], len(workloads)


@register("SEC-API-001", TOOL_ID, TOOL_NAME, "security", "API method declared without authorization")
def unauthenticated_api(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    out: list[ToolResult] = []
    checked = 0
    for res in ctx.by_type("aws_api_gateway_method", "aws_apigatewayv2_route", "AWS::ApiGateway::Method", "AWS::ApiGatewayV2::Route"):
        checked += 1
        auth = get_path(res.properties, "authorization", "authorization_type", "AuthorizationType", default=None)
        if has(auth) and str(auth).upper() == "NONE":
            http = get_path(res.properties, "http_method", "HttpMethod", "route_key", "RouteKey", default="")
            out.append(_mk("SEC-API-001", res, "API method has no authorization", f"{res.address} ({http}) sets authorization = NONE.",
                           observed="authorization = NONE", expected="IAM, Cognito or custom authorizer", severity=Severity.HIGH,
                           excerpt="authorization = NONE"))
    return out, checked
