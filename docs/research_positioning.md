# Research Positioning

This repository is for Paper 3 and is deliberately separate from Paper 2.

## Paper 2

Paper 2 develops exact robust inventory reconfiguration. It determines whether
reconfiguration is economically justified and, when it is, how much inventory
to reconfigure and where.

## Paper 3

Paper 3 does not replace Paper 2 and does not redefine its robust optimization
model. It studies whether information available before a solve can identify
when exact reoptimization is decision-relevant. The AI component acts as a
risk-trigger or screening layer before the exact optimizer.

```text
system state
    -> AI risk trigger
        -> retain incumbent
        or
        -> invoke exact robust optimization oracle
```

The AI risk trigger does **not** directly output a final inventory
configuration. Inventory decisions remain the responsibility of the frozen
exact optimization oracle.
