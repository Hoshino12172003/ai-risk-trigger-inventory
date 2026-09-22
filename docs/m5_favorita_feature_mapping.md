# M5–Favorita Feature Mapping

| concept | Favorita field | M5 field | available? | transformation | notes |
|---|---|---|---|---|---|
| demand | `sales` | `d_*` columns | both | reshape M5 wide days to long form | observed unit demand |
| item | `family` | `item_id` | both | retain IDs; aggregation is study-specific | M5 is finer grained |
| department | unavailable in current panel | `dept_id` | M5 only | categorical encoding | observed M5 hierarchy |
| category | `family` proxy only | `cat_id` | both, not equivalent | document aggregation | do not equate directly |
| store | `store_nbr` | `store_id` | both | categorical key | demand region, not warehouse |
| state | `state` | `state_id` | both | normalize labels | observed geography |
| price | unavailable in current panel | `sell_price` | M5 only | lagged level/change | join on store, item, week |
| explicit promotion | `onpromotion` | unavailable | Favorita only | aggregate to week | M5 price change is only a proxy |
| transactions | `transactions_clean` | unavailable | Favorita only | lagged change | no direct M5 analogue |
| holiday/event | `holiday_any` | `event_name_1/2`, `event_type_1/2` | both | calendar join and indicators | M5 has named events |
| SNAP context | unavailable | `snap_CA/TX/WI` | M5 only | state-specific indicator | observed program context |
| seasonality | `date` | calendar date fields | both | lag-safe calendar features | derived |
| demand center | lagged rolling sales | lagged rolling `d_*` sales | both | shift before rolling | common uncertainty interface |
| demand deviation | lagged residual/volatility | lagged residual/volatility | both | nonnegative calibrated estimate | common uncertainty interface |
| warehouse nodes | unavailable | unavailable | neither | calibrate explicitly | never observed |
| inventory | unavailable | unavailable | neither | calibrate explicitly | never observed |
| capacity | unavailable | unavailable | neither | calibrate explicitly | never observed |
| transport cost | unavailable | unavailable | neither | calibrate explicitly | never observed |
| shortage penalty | unavailable | unavailable | neither | calibrate explicitly | never observed |
| reconfiguration cost | unavailable | unavailable | neither | calibrate explicitly | never observed |
