# AI Risk-Triggered Inventory Reconfiguration

This repository contains the research setup for **Paper 3**. It studies an AI
risk-trigger that screens whether a changed demand and system state warrants
calling an exact robust inventory reconfiguration oracle.

Paper 2 is maintained separately in
[`Hoshino12172003/budget-inventory-benders`](https://github.com/Hoshino12172003/budget-inventory-benders).
Its robust inventory model and PRB-Benders algorithm are treated here as a
frozen optimization oracle at commit
`51aebd06edf8f5d6d124d0f3eebdbb901e63274f`. Paper 3 does not modify the
optimization model from Paper 2.

## Research question

> Can pre-solve demand, inventory, network and risk-state information identify
> when exact robust inventory reoptimization is decision-relevant?

> 能否利用求解前的需求、库存、网络和风险状态信息，识别何时重新运行精确鲁棒库存优化具有决策意义？

The AI component is a screening layer. It does not output the final inventory
configuration; a positive trigger invokes the frozen exact optimization oracle.

## Current status

- research setup only
- no formal experiment
- externally supplied Favorita inputs remain local and gitignored
- no optimization executed

The current decision-sensitivity feasibility pilot is documented in
[`docs/decision_sensitivity_instance_protocol.md`](docs/decision_sensitivity_instance_protocol.md).
Its checked-in result tables are schema-only while local inputs and a verified
oracle adapter are unavailable.

## Development

Requires Python 3.11 or newer.

```bash
python -m pip install -e ".[dev]"
pytest -q
```
