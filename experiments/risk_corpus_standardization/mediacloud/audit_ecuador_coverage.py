import os
import csv
import datetime as dt
from pathlib import Path

import mediacloud.api

TOKEN = os.environ.get("MC_API_KEY")
if not TOKEN:
    raise RuntimeError(
        "没有检测到 MC_API_KEY。请先在当前 PowerShell 窗口执行："
        '$env:MC_API_KEY="你的新Token"'
    )

OUT = Path("mediacloud_ecuador_audit")
OUT.mkdir(exist_ok=True)

directory = mediacloud.api.DirectoryApi(TOKEN)
search = mediacloud.api.SearchApi(TOKEN)

print("1. 正在查找 Ecuador collection ...")

# 先尝试几个名称，避免目录里的正式名称和我们预想不同
candidate_names = [
    "Ecuador",
    "Ecuador - National",
    "Ecuador National",
]

collections = []

for name in candidate_names:
    try:
        result = directory.collection_list(
            platform="online_news",
            name=name,
            limit=100
        )
        rows = result.get("results", [])
        for r in rows:
            collections.append(r)
    except Exception as e:
        print(f"查询 collection '{name}' 时出现：{e}")

# 去重
unique = {}
for c in collections:
    cid = c.get("id")
    if cid is not None:
        unique[cid] = c

collections = list(unique.values())

print("\n发现的候选 collections：")
for c in collections:
    print(
        "ID =", c.get("id"),
        "| name =", c.get("name"),
        "| platform =", c.get("platform")
    )

if not collections:
    raise RuntimeError(
        "没有自动找到 Ecuador collection。"
        "请把上面的输出发给 ChatGPT，我们再查 Directory。"
    )

# 优先选择名称里最明确包含 Ecuador 的 collection
ecuador_collections = [
    c for c in collections
    if "ecuador" in str(c.get("name", "")).lower()
]

if not ecuador_collections:
    raise RuntimeError("找到了 collection，但没有名称明确包含 Ecuador 的条目。")

# 如果有多个，先全部打印并默认选择第一个
chosen = {"id": 34412279, "name": "Ecuador - National"}
COLLECTION_ID = 34412279

print("\n当前用于审查的 collection：")
print(chosen)
print(f"\nCOLLECTION_ID = {COLLECTION_ID}")

# ----------------------------------------------------------
# 2. 年度基础覆盖审查
# ----------------------------------------------------------

# Media Cloud 查询需要 q。
# '*' 是否被后端视为 match-all 需要实际验证。
# 若失败，下面会自动尝试 OR 形式的高频西语停用词。
BASE_QUERIES = [
    "*",
    "(el OR la OR de OR en OR que)"
]

coverage_rows = []

print("\n2. 开始年度基础覆盖审查 2015-2025 ...")

for year in range(2015, 2026):
    start = dt.date(year, 1, 1)
    end = dt.date(year, 12, 31)

    total_count = None
    used_query = None
    error_msg = ""

    for q in BASE_QUERIES:
        try:
            total_count = search.story_count(
                q,
                start,
                end,
                collection_ids=[COLLECTION_ID]
            )
            used_query = q
            break
        except Exception as e:
            error_msg = str(e)

    active_sources = None
    source_error = ""

    if used_query is not None:
        try:
            source_rows = search.sources(
                used_query,
                start,
                end,
                collection_ids=[COLLECTION_ID],
                limit=1000
            )
            active_sources = len(source_rows)
        except Exception as e:
            source_error = str(e)

    row = {
        "year": year,
        "total_story_count": total_count,
        "active_sources_returned": active_sources,
        "query_used": used_query,
        "coverage_error": error_msg,
        "source_error": source_error,
    }
    coverage_rows.append(row)

    print(
        f"{year}: stories={total_count}, "
        f"sources={active_sources}, query={used_query}"
    )

with open(
    OUT / "ecuador_annual_coverage_2015_2025.csv",
    "w",
    newline="",
    encoding="utf-8-sig"
) as f:
    writer = csv.DictWriter(f, fieldnames=coverage_rows[0].keys())
    writer.writeheader()
    writer.writerows(coverage_rows)

# ----------------------------------------------------------
# 3. Flash-demand 关键词年度命中
# ----------------------------------------------------------

flash_queries = {
    "panic_buying_es":
        '("compras de pánico" OR "compra de pánico")',

    "mass_buying_es":
        '("compras masivas" OR "compras compulsivas")',

    "demand_increase_es":
        '("aumento de la demanda" OR "incremento de la demanda" '
        'OR "demanda se disparó" OR "demanda se dispara")',

    "sales_surge_es":
        '("ventas se dispararon" OR "ventas aumentaron" '
        'OR "récord de ventas" OR "record de ventas")',

    "consumer_rush_es":
        '("consumidores acudieron masivamente" '
        'OR "fila de compradores" '
        'OR "compradores acudieron masivamente")',

    "stockout_with_buying_es":
        '(("agotado" OR "se agotó" OR "sin existencias") '
        'AND (compras OR compradores OR consumidores OR ventas))',

    "panic_buying_en":
        '("panic buying" OR "rush to buy" OR "consumer rush")',

    "demand_surge_en":
        '("surge in demand" OR "demand surged" OR "sales surged")'
}

flash_rows = []

print("\n3. 开始 Flash-demand 年度关键词审查 ...")

for year in range(2015, 2026):
    start = dt.date(year, 1, 1)
    end = dt.date(year, 12, 31)

    print(f"\n--- {year} ---")

    for label, q in flash_queries.items():
        count = None
        err = ""

        try:
            count = search.story_count(
                q,
                start,
                end,
                collection_ids=[COLLECTION_ID]
            )
        except Exception as e:
            err = str(e)

        flash_rows.append({
            "year": year,
            "query_label": label,
            "query": q,
            "hit_count": count,
            "error": err,
        })

        print(f"{label}: {count}")

with open(
    OUT / "ecuador_flash_query_hits_2015_2025.csv",
    "w",
    newline="",
    encoding="utf-8-sig"
) as f:
    writer = csv.DictWriter(f, fieldnames=flash_rows[0].keys())
    writer.writeheader()
    writer.writerows(flash_rows)

# ----------------------------------------------------------
# 4. 每年抽样少量需求侧文章，检查语义质量
# ----------------------------------------------------------

combined_flash_query = (
    '("compras de pánico" OR "compras masivas" '
    'OR "aumento de la demanda" OR "incremento de la demanda" '
    'OR "ventas se dispararon" OR "récord de ventas" '
    'OR "record de ventas" OR "panic buying" '
    'OR "rush to buy" OR "surge in demand" OR "demand surged")'
)

sample_rows = []

print("\n4. 每年抽样 Flash-demand 相关文章 ...")

for year in range(2015, 2026):
    start = dt.date(year, 1, 1)
    end = dt.date(year, 12, 31)

    try:
        sample = search.story_sample(
            combined_flash_query,
            start,
            end,
            collection_ids=[COLLECTION_ID],
            limit=10
        )

        print(f"{year}: sample={len(sample)}")

        for s in sample:
            sample_rows.append({
                "year": year,
                "id": s.get("id"),
                "publish_date": s.get("publish_date"),
                "language": s.get("language"),
                "media_name": s.get("media_name"),
                "title": s.get("title"),
                "url": s.get("url"),
            })

    except Exception as e:
        print(f"{year}: sample ERROR: {e}")

if sample_rows:
    with open(
        OUT / "ecuador_flash_samples_2015_2025.csv",
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=sample_rows[0].keys())
        writer.writeheader()
        writer.writerows(sample_rows)

print("\n=======================================")
print("审查完成。")
print("输出目录：", OUT.resolve())
print("主要文件：")
print("1) ecuador_annual_coverage_2015_2025.csv")
print("2) ecuador_flash_query_hits_2015_2025.csv")
print("3) ecuador_flash_samples_2015_2025.csv")
print("=======================================")
