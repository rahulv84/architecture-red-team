# LedgerBridge - Hybrid Settlement Platform

## Purpose
LedgerBridge settles interbank payment instructions overnight. Core ledger processing remains in the on-premises
data centre; a cloud extension on AWS (eu-central-1) hosts partner connectivity, archiving and reporting. This
document describes the target hybrid architecture ahead of the next audit cycle.

## Requirements
- Availability target of 99.5% for the ledger application during business hours.
- RTO of 4 hours and RPO of 1 hour for ledger data.
- The nightly settlement batch runs between 01:00 and 05:00 and must complete before the partner cut-off at 05:30.
- Peak volume is about 50,000 transactions per hour.
- The platform processes personal data of EU account holders (GDPR) and must keep data residency within the EU.
- Settlement records must be retained for 10 years; archive access must be auditable.

## On-premises components
- **Core Ledger Application** runs on virtual machines in the primary data centre and is the system of record front end.
- **Oracle Ledger DB** is the primary ledger database. A standby instance runs in the same data centre; failover is performed manually
  following a runbook.
- **Batch Processing** (Control-M) runs the nightly settlement jobs, reads ledger data and produces settlement files.
- **Nagios** provides infrastructure monitoring for on-premises hosts; cloud resources are not yet integrated.
- A **Corporate Firewall** separates the data-centre network from the corporate network and the VPN.

## Cloud components (AWS)
- **Site-to-site VPN** connects the corporate network to the AWS VPC through a single VPN gateway.
- **Partner Gateway** (AWS Lambda) calls the **Partner Bank API** synchronously to submit settlement instructions.
- **Settlement Queue** (Amazon SQS) buffers settlement files produced by the batch.
- **Reporting DB** (Amazon RDS Oracle read replica fed by GoldenGate) serves analytics and regulatory reporting.
- **Archive bucket** (Amazon S3) stores settlement exports; a second **Partner Exchange bucket** is used to share files with the partner.
- An analytics replica of reporting data is also kept in us-east-1 for the global risk team.

## Security
- Corporate users reach the ledger application through SSO.
- The VPN is the only path between the data centre and AWS.
- Partner credentials are configured on the gateway function.

## Operations
- Batch failures are reviewed by operators at the start of the business day.
- Failover of the Oracle ledger database is manual and was last tested two years ago.
