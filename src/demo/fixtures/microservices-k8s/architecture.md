# PayFlow Marketplace Payments - Microservices Architecture

## Purpose
PayFlow is a cloud-agnostic payments and ordering platform built from containerised microservices running on a
Kubernetes cluster. It exposes a public API to merchants and mobile apps and processes card payments through
an external acquirer. This document describes the target deployment for the production cluster.

## Requirements
- Availability target of 99.9% per month for the public API.
- RTO of 15 minutes and RPO of 5 minutes for order and payment data.
- Expected load of 800 requests per second at peak with checkout p95 latency below 200 ms.
- The platform processes cardholder data and is in scope for PCI DSS; personal data of EU users is also processed (GDPR).
- Audit logs must be retained for 12 months.

## Components
- **API Gateway** (Kong) is the single public entry point behind a Kubernetes Ingress; it authenticates merchants with API keys and forwards requests.
- **Orders Service** manages orders and calls the Payments Service synchronously during checkout.
- **Payments Service** calls the external Payment Acquirer API and writes ledger entries to PostgreSQL.
- **Inventory Service** reads and updates stock levels in PostgreSQL and caches hot items in Redis.
- **Notification Service** consumes order events from Kafka and sends emails and push notifications.
- **PostgreSQL** is the system of record for orders, payments and inventory (single StatefulSet).
- **Redis** provides caching for catalogue and inventory reads.
- **Kafka** carries order events between services.
- **Monitoring** uses Prometheus and Grafana dashboards.

## Security
- TLS is terminated at the Ingress controller.
- Services authenticate users through the API Gateway; service-to-service authentication is planned for a later phase.
- Database credentials are provided to services through environment variables.

## Operations
- Deployments are performed with `kubectl apply` from the CI pipeline.
- Dashboards exist in Grafana; alert rules have not been defined yet.
