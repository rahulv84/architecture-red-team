# ShopSphere Online Store - Solution Architecture

## Purpose
ShopSphere is a consumer e-commerce platform that lets customers browse a catalogue, place orders and pay online.
This document describes the proposed production architecture on AWS (region eu-west-1) for review before go-live.

## Business and Non-Functional Requirements
- The storefront must achieve 99.95% availability measured monthly.
- Order data must survive a loss of one availability zone with an RPO of 5 minutes and an RTO of 30 minutes.
- Peak traffic is expected at 1,500 requests per second during seasonal campaigns.
- Checkout API p95 latency should stay below 300 ms.
- The platform stores personal data of EU customers and must comply with GDPR; payment card data is handled by an external payment provider.
- Customer invoices must be retained for 7 years.

## Logical Architecture
Customers reach the storefront through an Application Load Balancer (ALB) in two public subnets. The ALB forwards
traffic to the Storefront application, a Java service running on an Auto Scaling group of EC2 instances.

The Storefront application:
- reads and writes orders and customer profiles in the Orders PostgreSQL database (Amazon RDS);
- publishes order events to the Orders queue (Amazon SQS) which is consumed by the Fulfilment worker;
- stores product images in the Media S3 bucket and customer invoices in the Invoices S3 bucket;
- calls the external Payment Provider API synchronously during checkout.

Outbound internet access from private subnets goes through a NAT gateway.

## Security
- Application instances are deployed in private subnets.
- An IAM role is attached to the application instances for access to AWS services.
- Operational access is performed by engineers over SSH from their workstations.
- TLS is terminated at the load balancer.

## Operations
- Application logs are shipped to CloudWatch Logs.
- Deployments are performed by replacing instances in the Auto Scaling group.
- No formal runbook for queue failures exists yet.
