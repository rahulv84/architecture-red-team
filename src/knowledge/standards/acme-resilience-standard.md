---
title: Acme Resilience and Availability Standard (sample)
origin: ORGANIZATIONAL_STANDARD
version: "1.4"
domains: [resilience, operability]
---

# Acme Resilience and Availability Standard (sample organisational standard)

## ACME-RES-001 Redundancy across failure domains
Production services with an availability objective of 99.9% or higher MUST run in at least two availability
zones. Stateful stores MUST use synchronous replication or managed multi-AZ deployment with automated failover.

## ACME-RES-002 Single points of failure
A component whose failure interrupts a customer-facing path MUST be redundant or have a documented and tested
manual recovery procedure that meets the RTO. Shared egress components (NAT, proxies) MUST be deployed per zone.

## ACME-RES-003 Backup and restore
Production data stores MUST have automated backups with at least 7 days retention and a restore test at least
quarterly. Backup frequency MUST satisfy the stated RPO.

## ACME-RES-004 Asynchronous messaging
Queues and topics MUST configure a dead-letter destination with an alarm. Consumers MUST be idempotent.
Retention MUST exceed the maximum expected consumer outage.

## ACME-RES-005 Dependency failure handling
Synchronous calls to external or cross-service dependencies MUST define timeouts, bounded retries with
backoff and a circuit-breaking or degradation strategy.

## ACME-RES-006 Health probing
Containerised workloads MUST define readiness and liveness probes; load balancer health checks MUST exercise
the application path, not only the TCP port.
