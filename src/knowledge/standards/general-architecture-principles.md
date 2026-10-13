---
title: General architecture good practices
origin: GENERAL_PRACTICE
version: "1.0"
domains: [security, resilience, finops, compliance, performance, operability]
---

# General architecture good practices (not an organisational standard)

These are widely recommended practices summarised in the authors' own words. They are **not** mandatory
organisational controls; findings that rely on them are labelled "general practice".

## Defence in depth
Layer controls: network segmentation, identity-based access, encryption and monitoring. Assume any single
control can fail and limit the blast radius of a compromised component.

## Least privilege
Grant the minimum permissions for the shortest time. Prefer scoped roles over shared credentials; prefer
managed identities over long-lived keys.

## Design for failure
Assume dependencies fail. Remove single points of failure for critical paths, isolate failure domains,
set timeouts, use bounded retries with jitter and degrade gracefully.

## Recovery objectives drive design
State RTO and RPO per workload and verify that backup, replication and failover design can meet them; test
the restore path regularly.

## Measure before optimising
Use load tests and production telemetry to size capacity and locate bottlenecks. Cache and decouple only
where measurements show a benefit.

## Cost is a design constraint
Make cost drivers (data transfer, always-on capacity, retention) visible early and tag resources for
allocation. Prefer elasticity to peak provisioning.

## Operate what you build
Ship observability, runbooks and an on-call owner with the service. Automate deployment and rollback.
