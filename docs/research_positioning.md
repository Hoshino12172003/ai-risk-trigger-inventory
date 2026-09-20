# Research Positioning

This repository is for Paper 2 and is deliberately separate from Paper 1.

## Paper 1

Paper 1 develops exact robust inventory reconfiguration. It determines whether
reconfiguration is economically justified and, when it is, how much inventory
to reconfigure and where.

## Paper 2

Paper 2 does not replace Paper 1 and does not redefine its robust optimization
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
