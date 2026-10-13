---
title: Acme FinOps and Performance Guidelines (sample)
origin: ORGANIZATIONAL_STANDARD
version: "1.2"
domains: [finops, performance]
---

# Acme FinOps and Performance Guidelines (sample organisational standard)

## ACME-FIN-001 Right-sizing
Instance and database classes MUST be justified by load-test or utilisation evidence. Capacity larger than
four times the observed peak requires an exception. Reserved or committed capacity is considered only after
three months of stable utilisation data.

## ACME-FIN-002 Elasticity
Stateless compute MUST scale horizontally on demand metrics; fixed capacity needs documented justification.

## ACME-FIN-003 Data lifecycle
Object storage and log groups MUST define lifecycle or retention rules. Logs default to 30 days online and
are archived according to the retention schedule.

## ACME-FIN-004 Network cost
Traffic to managed services from private subnets SHOULD use private endpoints rather than NAT egress.
Cross-zone and cross-region transfer paths MUST be identified in the design.

## ACME-FIN-005 Cost allocation
All billable resources MUST carry owner and cost-center tags.

## ACME-PERF-001 Latency budgets
Every user-facing request path MUST have a latency budget allocated across hops. Synchronous call chains
longer than three tiers require an explicit justification or asynchronous decoupling.

## ACME-PERF-002 Caching
Read-heavy data paths SHOULD define a caching layer with an invalidation strategy.

## ACME-PERF-003 Data tier scaling
Shared relational stores serving multiple services MUST document connection pooling, read-replica strategy
and the expected saturation point. Capacity targets MUST be validated by a load test before launch.
