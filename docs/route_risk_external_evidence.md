# Stage 3D external route-risk evidence record

## Evidence hierarchy

Stage 3D uses three evidence levels with different, non-interchangeable roles:

- `LEVEL_1` — Ecuador-specific evidence supports local operational relevance,
  the presence of road-network disruption, and the realism of partial
  disruption, severe disruption, and closure states.
- `LEVEL_2` — Andean or Latin American evidence supports regional transfer and
  demonstrates the consequences of disruption and low network redundancy.
- `LEVEL_3` — international quantitative transport-disruption evidence anchors
  broad lower-loss and higher-loss ranges.

No single source determines either interval. The intervals [0.10, 0.40] and
[0.40, 0.80] are cross-evidence constructs. None of the source metrics is
silently treated as an observation of the model's `delta_A`.

## Level 1: Ecuador context

1. Solberg, Hale, and Benavides document Ecuador's recurring floods,
   landslides, earthquakes, and volcanic hazards and their implications for
   road-network functionality and economic flows. The paper is used only for
   local context; it explicitly identifies itself as a discussion working
   paper rather than a peer-reviewed parameter study. [IDB, 2003](https://publications.iadb.org/publications/english/document/Natural-Disaster-Management-and-the-Road-Network-in-Ecuador-Policy-Issues-and-Recommendations.pdf)

2. The IDB Sustainability Report records 83 critical locations in an Ecuador
   road-risk study where landslides could produce partial or total closure. This
   supports the state taxonomy, not a percentage loss. [IDB Sustainability
   Report, 2019](https://publications.iadb.org/publications/english/document/Inter-American-Development-Bank-Sustainability-Report-2019.pdf)

3. An Ecuador road-project case study identifies critical landslide-risk zones
   and evaluates mitigation alternatives for the Bellavista–Zumba–La Balsa
   corridor. It supplies local hazard relevance but no transferable route-loss
   percentage. [IDB technical note, 2022](https://doi.org/10.18235/0003916)

These three records are `LOCAL_CONTEXT_SUPPORT`, not
`DIRECT_ECUADOR_PARAMETER_ESTIMATE`.

## Level 2: Andean and Latin American transfer

The IDB's regional transport-resilience report describes a major landslide
closing the Pasto–Popayán segment of the Pan-American Highway, a corridor
important to the connection with Ecuador. It reports that interruptions in
low-redundancy settings can require 400 km or more of additional travel. This
supports severe-disruption and closure relevance, but the detour distance is
not converted into `delta_A`. [IDB Transportation 2050,
2023](https://publications.iadb.org/publications/english/document/Transportation-2050-pathways-to-decarbonization-and-climate-resilience-in-Latin-America-and-the-Caribbean.pdf)

This record is `REGIONAL_TRANSFER_SUPPORT`.

## Level 3: international quantitative anchors

The quantitative anchors establish that transport service loss spans both
partial and high-loss regimes, and that network effects can exceed direct
physical exposure:

- A World Bank assessment covering 2,564 settlement clusters reports mean
  simulated route-failure shares from 11.58% in a lower-return-period flood
  scenario to 65.97% in a higher-return-period scenario at the reported 30 cm
  threshold. [He et al., 2022](https://documents.worldbank.org/curated/en/099552305172228687/pdf/IDU0e38cb88d018d704a8e08a220c09ce9f1be91.pdf)
- Video and vehicle-flow observations from Metro Manila report 40%–70% flow
  reduction per lane-kilometer as flood-related lane closures increase, based
  on 433 samples. [Mamuyac et al., 2024](https://doi.org/10.5109/7323361)
- Hurricane Harvey traffic observations show nonlinear propagation: 2.2%
  flood-induced compound failure was associated with a 17.7% decrease in the
  network's giant-component size. [Dong et al.,
  2022](https://doi.org/10.1038/s43247-022-00366-0)

Route-failure share, vehicle-flow reduction, and giant-component reduction are
different metrics. They are `QUANTITATIVE_RANGE_ANCHOR` records; none is a
direct measurement of planning-period warehouse-region effective service loss.

## Transfer conclusion and limitations

Together, the evidence supports a lower partial-loss regime, a higher severe-
loss regime, and a separate closure endpoint. Stage 3D freezes:

- `MODERATE_DISRUPTION`: [0.10, 0.40];
- `SEVERE_DISRUPTION`: [0.40, 0.80].

The overlap at 0.40 is a deliberate boundary between adjacent sensitivity
ranges. The representatives 0.25 and 0.60 are transparent interior points, not
statistical estimates. The 0.80 upper sensitivity bound is a conservative
cross-evidence modeling bound below closure, not a value reported as Ecuador's
true capacity loss.

Geography, hazard, network redundancy, baseline service, response behavior,
and metric definitions differ across the sources. Accordingly, Stage 3D makes
no Ecuador-specific exact route-loss claim and no physical-capacity claim.
