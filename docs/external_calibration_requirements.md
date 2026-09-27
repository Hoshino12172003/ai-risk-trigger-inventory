# External route-risk calibration requirements

Status: `PENDING_EXTERNAL_EVIDENCE`.

Favorita contains retail demand context but no observed warehouse-route service
or transport-capacity loss. A future route-risk calibration requires verified,
time- and geography-aligned sources such as:

- road closure and reopening records;
- extreme-weather transportation-impact records;
- freight or delivery service-capacity reductions;
- audited logistics-disruption studies;
- public emergency mobility restrictions;
- documented warehouse-to-region service assignments and normal service levels.

Sources must identify measurement units, baseline service, affected geography,
event dates, duration, missingness, and whether loss is physical capacity,
throughput, travel-time service, or fulfillment share. No DOI, author,
percentage, or external claim is supplied until a source is verified.

Until then, the Stage-1 route losses 25%, 50%, 75%, and 100% are
`STRESS_TEST_ONLY`, not observed-data calibrated values.
