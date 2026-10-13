---
title: Acme Operability and Data Protection Standard (sample)
origin: ORGANIZATIONAL_STANDARD
version: "1.3"
domains: [operability, compliance]
---

# Acme Operability and Data Protection Standard (sample organisational standard)

## ACME-OPS-001 Observability
Every production service MUST emit structured logs, golden-signal metrics and distributed traces, and define
SLIs with SLOs. Alarms MUST page an on-call rotation for SLO burn.

## ACME-OPS-002 Runbooks and ownership
Each service MUST have a named owner and runbooks for its top failure modes, including queue backlog,
dead-letter handling, failed deployment and dependency outage.

## ACME-OPS-003 Deployment and rollback
Deployments MUST be progressive (rolling, blue/green or canary) with automated rollback on health regression.
Container images MUST be pinned to an immutable tag or digest.

## ACME-OPS-004 Configuration hygiene
Containers MUST declare CPU and memory requests and limits. Configuration MUST reference only defined
resources; dangling references are build failures.

## ACME-DATA-001 Data classification
Data stores MUST be tagged with a classification (public, internal, confidential, personal). Personal data
stores MUST be encrypted, access-logged and subject to a retention and deletion schedule.

## ACME-DATA-002 Residency
Personal data of EU data subjects MUST be stored and processed in EU regions unless a transfer mechanism has
been approved by the data protection officer.

## ACME-DATA-003 Retention
Retention periods MUST be enforced technically (lifecycle rules, TTLs), and deletion requests MUST be
fulfilled across backups within the documented window.
