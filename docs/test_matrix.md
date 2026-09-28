| # | Scenario | Injected faults | Result | Notes |
|---|---|---|---|---|
| 1 | approach | none | PASS | faults reported: HEARTBEAT |
| 2 | stationary | none | PASS | faults reported: HEARTBEAT |
| 3 | cut_in | none | PASS | faults reported: HEARTBEAT |
| 4 | lateral_pass | none | PASS | faults reported: HEARTBEAT |
| 5 | follow | none | PASS | faults reported: HEARTBEAT |
| 6 | multi | none | PASS | faults reported: HEARTBEAT |
| 7 | approach | drop 10% | PASS | faults reported: HEARTBEAT|SEQ_GAP |
| 8 | approach | corrupt-crc 5% | PASS | faults reported: HEARTBEAT|CRC|SEQ_GAP |
| 9 | approach | reorder 5% | PASS | faults reported: HEARTBEAT|SEQ_GAP|SEQ_OLD |
| 10 | approach | duplicate 5% | PASS | faults reported: HEARTBEAT|SEQ_OLD |
| 11 | approach | rate 15 Hz | PASS | faults reported: HEARTBEAT |
| 12 | follow | burst-loss 200ms@1.00s | PASS | failover 105.9 ms; recovery 335 ms; faults reported: HEARTBEAT |
| 13 | follow | burst-loss 60ms@1.00s | PASS | faults reported: HEARTBEAT|SEQ_GAP |
| 14 | follow | stop-at 2.00s | PASS | failover 105.3 ms; faults reported: HEARTBEAT |
