import os
import csv
import datetime as dt
from pathlib import Path

import mediacloud.api

TOKEN = os.environ.get("MC_API_KEY")
if not TOKEN:
    raise RuntimeError(
        '未检测到 MC_API_KEY，请先执行：$env:MC_API_KEY="你的Token"'
    )

# 正确的全国集合
COLLECTION_ID = 34412279
COLLECTION_NAME = "Ecuador - National"

OUT = Path("mediacloud_ecuador_flash_v2")
OUT.mkdir(exist_ok=True)

search = mediacloud.api.SearchApi(TOKEN)

print(f"Using collection: {COLLECTION_NAME}")
print(f"COLLECTION_ID = {COLLECTION_ID}")

# -------------------------------------------------------
# 更严格的 Flash-demand 查询
#
# 原则：
# 1. 必须有需求/购买激增语义
# 2. 必须有消费/零售上下文
# 3. 必须出现 Ecuador 或主要 Ecuador 地名
# -------------------------------------------------------

geo = """
(
Ecuador OR ecuatoriano OR ecuatoriana
OR Quito OR Guayaquil OR Cuenca
OR Guayas OR Pichincha OR Azuay
OR Manabí OR Manabi
OR "Los Ríos" OR "Los Rios"
OR "El Oro"
OR Loja
OR Tungurahua
OR Chimborazo
OR Imbabura
OR Cotopaxi
)
"""

consumer = """
(
consumidores
OR compradores
OR compras
OR supermercados
OR supermercado
OR tiendas
OR tienda
OR hogares
OR familias
OR pedidos
OR clientes
OR "ventas minoristas"
OR "comercio minorista"
)
"""

flash_queries = {
    "panic_buying":
        f'''
        (
          "compras de pánico"
          OR "compras de panico"
          OR "compras masivas"
          OR "compra masiva"
          OR "compradores acudieron masivamente"
          OR "consumidores acudieron masivamente"
        )
        AND {geo}
        ''',

    "sudden_demand":
        f'''
        (
          "aumento repentino de la demanda"
          OR "repentino aumento de la demanda"
          OR "demanda se disparó"
          OR "demanda se disparo"
          OR "fuerte aumento de la demanda"
          OR "incremento extraordinario de la demanda"
        )
        AND {consumer}
        AND {geo}
        ''',

    "purchase_surge":
        f'''
        (
          "se dispararon las compras"
          OR "aumentaron las compras"
          OR "fuerte incremento de compras"
          OR "compras crecieron"
          OR "compras aumentaron"
        )
        AND {consumer}
        AND {geo}
        ''',

    "retail_sales_surge":
        f'''
        (
          "ventas se dispararon"
          OR "ventas aumentaron considerablemente"
          OR "fuerte aumento de ventas"
          OR "récord de ventas"
          OR "record de ventas"
        )
        AND
        (
          supermercados
          OR tiendas
          OR minoristas
          OR consumidores
          OR compradores
        )
        AND {geo}
        ''',

    "stockout_demand":
        f'''
        (
          "se agotó"
          OR "se agoto"
          OR "se agotaron"
          OR "sin existencias"
          OR "agotaron existencias"
        )
        AND
        (
          compras
          OR compradores
          OR consumidores
          OR supermercados
          OR tiendas
        )
        AND {geo}
        '''
}

# -------------------------------------------------------
# 1. 年度 hit count
# -------------------------------------------------------

hit_rows = []

print("\n=== V2 Flash-demand hit audit ===")

for year in range(2015, 2026):
    start = dt.date(year, 1, 1)
    end = dt.date(year, 12, 31)

    print(f"\n--- {year} ---")

    for label, query in flash_queries.items():

        try:
            result = search.story_count(
                query,
                start,
                end,
                collection_ids=[COLLECTION_ID]
            )

            relevant = result.get("relevant", 0)
            total = result.get("total", 0)
            error = ""

        except Exception as e:
            relevant = None
            total = None
            error = str(e)

        hit_rows.append({
            "year": year,
            "query_label": label,
            "hit_count": relevant,
            "collection_total": total,
            "query": " ".join(query.split()),
            "error": error
        })

        print(f"{label}: {relevant}")

with open(
    OUT / "ecuador_flash_v2_hits_2015_2025.csv",
    "w",
    newline="",
    encoding="utf-8-sig"
) as f:
    writer = csv.DictWriter(
        f,
        fieldnames=hit_rows[0].keys()
    )
    writer.writeheader()
    writer.writerows(hit_rows)

# -------------------------------------------------------
# 2. 每组 query 每年抽样
#
# 每个 query 每年最多 10 条
# 后面按 story ID / URL 去重
# -------------------------------------------------------

samples = {}

print("\n=== Sampling candidate stories ===")

for year in range(2015, 2026):
    start = dt.date(year, 1, 1)
    end = dt.date(year, 12, 31)

    print(f"\n--- {year} ---")

    for label, query in flash_queries.items():

        try:
            rows = search.story_sample(
                query,
                start,
                end,
                collection_ids=[COLLECTION_ID],
                limit=10
            )

            print(f"{label}: sample={len(rows)}")

            for s in rows:
                sid = s.get("id")
                url = s.get("url")

                # 优先 story id 去重，没有 id 时用 URL
                key = str(sid) if sid is not None else url

                if not key:
                    continue

                if key not in samples:
                    samples[key] = {
                        "story_id": sid,
                        "year": year,
                        "publish_date": s.get("publish_date"),
                        "language": s.get("language"),
                        "media_name": s.get("media_name"),
                        "title": s.get("title"),
                        "url": url,
                        "matched_queries": set()
                    }

                samples[key]["matched_queries"].add(label)

        except Exception as e:
            print(f"{label}: ERROR: {e}")

# -------------------------------------------------------
# 3. 输出去重样本
# -------------------------------------------------------

sample_rows = []

for item in samples.values():
    sample_rows.append({
        "story_id": item["story_id"],
        "year": item["year"],
        "publish_date": item["publish_date"],
        "language": item["language"],
        "media_name": item["media_name"],
        "title": item["title"],
        "url": item["url"],
        "matched_queries": "|".join(
            sorted(item["matched_queries"])
        )
    })

sample_rows.sort(
    key=lambda x: (
        x["year"],
        str(x["publish_date"]),
        str(x["story_id"])
    )
)

with open(
    OUT / "ecuador_flash_v2_samples_deduplicated.csv",
    "w",
    newline="",
    encoding="utf-8-sig"
) as f:
    writer = csv.DictWriter(
        f,
        fieldnames=sample_rows[0].keys()
    )
    writer.writeheader()
    writer.writerows(sample_rows)

print("\n======================================")
print("V2 Flash-demand precision audit 完成")
print("Collection:", COLLECTION_NAME)
print("Unique sampled stories:", len(sample_rows))
print("输出目录:", OUT.resolve())
print("")
print("文件：")
print("1) ecuador_flash_v2_hits_2015_2025.csv")
print("2) ecuador_flash_v2_samples_deduplicated.csv")
print("======================================")
