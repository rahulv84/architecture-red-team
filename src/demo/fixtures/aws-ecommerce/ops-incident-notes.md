# Operations notes - pilot environment incident (2025-03-14)

## Summary
During the pilot, the orders queue backed up after the fulfilment worker crashed on a malformed message.
Messages were retried repeatedly until the retention period expired; roughly 140 orders had to be re-created manually.

## Observations
- No dead-letter queue was configured, so the poison message blocked consumers.
- No alarm existed on queue depth; the issue was found by a customer support ticket.
- The database instance in the pilot was a single instance in one availability zone and was unavailable for 25 minutes during a maintenance event.
