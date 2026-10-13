# Incident report - VPN outage during settlement (2025-11-06)

## Summary
The single site-to-site VPN tunnel dropped at 02:10 during the nightly batch. Settlement files could not reach the
partner gateway and the batch overran the 05:30 partner cut-off. The Control-M job reported success because the file
hand-off step does not verify delivery.

## Timeline
- 02:10 VPN tunnel down; no alert was raised in Nagios because cloud resources are not monitored.
- 05:30 Partner cut-off missed; partner support contacted by phone.
- 08:40 Operators noticed the failure while reviewing batch results; tunnel restored manually.

## Findings
- The batch failed silently; there is no alert on undelivered settlement files.
- There is no secondary VPN or Direct Connect circuit.
- The runbook for restoring the tunnel was out of date.
