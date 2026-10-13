---
title: Acme Cloud Security Standard (sample)
origin: ORGANIZATIONAL_STANDARD
version: "2.1"
domains: [security, compliance]
---

# Acme Cloud Security Standard (sample organisational standard)

This is a **sample** standard shipped with Architecture Red Team so that findings can cite an
organisational control. Replace or extend it with your own policies by editing files in
`knowledge/standards/` or by uploading documents with the "Organizational standard" role.

## ACME-SEC-001 Network exposure
Databases, caches and message brokers MUST NOT be reachable from the internet. Only load balancers,
API gateways and CDN endpoints may accept inbound traffic from `0.0.0.0/0`, and only on ports 80 and 443.
Administrative protocols (SSH, RDP) MUST be reached through a bastion or session manager, never directly.

## ACME-SEC-002 Encryption
All data stores holding customer or internal data MUST be encrypted at rest using managed keys.
Data in transit MUST use TLS 1.2 or later, including traffic between a load balancer and its targets
when it crosses a trust boundary. Plaintext HTTP listeners MUST redirect to HTTPS.

## ACME-SEC-003 Secrets management
Credentials, API keys and tokens MUST NOT be stored in source control, infrastructure definitions or images.
Secrets MUST be retrieved at runtime from a secrets manager and rotated at least every 90 days.

## ACME-SEC-004 Identity and least privilege
Workload roles MUST grant only the actions and resources required. Wildcard actions (`*`) combined with
wildcard resources are prohibited. Human access MUST use SSO with multi-factor authentication.

## ACME-SEC-005 Public object storage
Object storage buckets MUST block public access unless a documented exception is approved by the security
architecture board. Public static content MUST be served through a CDN with origin access control.

## ACME-SEC-006 Logging and detection
Audit logging (control-plane and data-access) MUST be enabled and retained for at least 12 months.
Network flow logs MUST be enabled for production networks.
