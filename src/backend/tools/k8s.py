"""Kubernetes manifest helpers shared by deterministic checks."""
from __future__ import annotations

from typing import Any

from ..parsers.types import IacResource

WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob", "Pod")
REPLICATED_KINDS = ("Deployment", "StatefulSet", "ReplicaSet")


def pod_spec(res: IacResource) -> dict[str, Any] | None:
    """Return the pod spec for workload kinds, else ``None``."""
    if res.framework != "kubernetes" or res.resource_type not in WORKLOAD_KINDS:
        return None
    spec = res.properties.get("spec") or {}
    if not isinstance(spec, dict):
        return None
    if res.resource_type == "Pod":
        return spec
    if res.resource_type == "CronJob":
        spec = ((spec.get("jobTemplate") or {}).get("spec") or {})
    template = spec.get("template") or {}
    pod = template.get("spec") if isinstance(template, dict) else None
    return pod if isinstance(pod, dict) else None


def pod_labels(res: IacResource) -> dict[str, str]:
    spec = res.properties.get("spec") or {}
    if res.resource_type == "Pod":
        return dict((res.properties.get("metadata") or {}).get("labels") or {})
    template = (spec.get("template") or {}) if isinstance(spec, dict) else {}
    return dict(((template.get("metadata") or {}).get("labels")) or {})


def containers(res: IacResource) -> list[dict[str, Any]]:
    spec = pod_spec(res)
    if not spec:
        return []
    return [c for c in (spec.get("containers") or []) if isinstance(c, dict)]
