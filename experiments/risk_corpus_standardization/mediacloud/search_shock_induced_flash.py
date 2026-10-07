import os
import csv
import datetime as dt
from pathlib import Path
import mediacloud.api

TOKEN = os.environ.get("MC_API_KEY")
if not TOKEN:
    raise RuntimeError('未检测到 MC_API_KEY')

COLLECTION_ID = 34412279
OUT = Path("mediacloud_ecuador_shock_flash_search")
OUT.mkdir(exist_ok=True)

search = mediacloud.api.SearchApi(TOKEN)

# -------------------------------------------------------
# Ecuador 地理
# -------------------------------------------------------

GEO = """
(
Ecuador OR Quito OR Guayaquil OR Cuenca
OR Guayas OR Pichincha OR Azuay
OR Manabí OR Manabi
OR Loja OR Cotopaxi OR Tungurahua
OR Chimborazo OR Imbabura
OR Latacunga OR Manta OR Machala OR Ambato
)
"""

# -------------------------------------------------------
# 明确需求响应
# 不再只搜索 shortage / stockpiling
# -------------------------------------------------------

DEMAND_RESPONSE = """
(
"compras de pánico"
OR "compras de panico"
OR "compras masivas"
OR "alta demanda"
OR "fuerte demanda"
OR "demanda se disparó"
OR "demanda se disparo"
OR "aumento repentino de la demanda"
OR "se dispararon las compras"
OR "aumentaron las compras"
OR "ventas se dispararon"
OR "ventas se dispararon"
OR "se agotaron"
OR "se agotó"
OR "se agoto"
OR "agotadas"
OR "agotados"
OR "sin existencias"
OR "largas filas"
OR "filas para comprar"
OR "incremento de pedidos"
OR "pedidos se dispararon"
)
"""

# -------------------------------------------------------
# Shock triggers
# 每一类都可能产生新的独立 Flash event
# -------------------------------------------------------

SHOCKS = {

    "health_emergency": """
    (
    pandemia OR epidemia OR brote OR contagios
    OR "emergencia sanitaria"
    OR "alerta sanitaria"
    OR enfermedad
    OR mascarillas
    )
    """,

    "power_outage": """
    (
    apagón OR apagon
    OR "corte de luz"
    OR "cortes de luz"
    OR "crisis eléctrica"
    OR "crisis electrica"
    OR racionamiento
    )
    """,

    "earthquake": """
    (
    terremoto OR sismo OR temblor
    )
    """,

    "flood_rain": """
    (
    inundación OR inundacion
    OR inundaciones
    OR lluvias
    OR "fuertes lluvias"
    OR temporal
    )
    """,

    "landslide_road_emergency": """
    (
    deslave OR derrumbe
    OR deslizamiento
    OR "cierre de vía"
    OR "cierre de via"
    OR "vía cerrada"
    OR "via cerrada"
    )
    """,

    "strike_protest": """
    (
    paro OR huelga
    OR protestas
    OR movilizaciones
    OR bloqueo
    OR bloqueos
    )
    """,

    "fuel_crisis": """
    (
    combustible OR gasolina OR diésel OR diesel
    )
    AND
    (
    escasez OR desabastecimiento
    OR crisis OR paro
    )
    """,

    "water_disruption": """
    (
    "corte de agua"
    OR "falta de agua"
    OR "agua potable"
    OR abastecimiento
    )
    """,

    "security_emergency": """
    (
    "estado de excepción"
    OR "estado de excepcion"
    OR toque de queda
    OR violencia
    OR disturbios
    OR inseguridad
    )
    """
}

# -------------------------------------------------------
# 查询
# -------------------------------------------------------

all_rows = []
seen = {}

print("=== Shock-induced Flash-demand search ===")

for shock_name, shock_query in SHOCKS.items():

    query = f"""
    {GEO}
    AND
    {shock_query}
    AND
    {DEMAND_RESPONSE}
    """

    print(f"\n### {shock_name}")

    for year in range(2015, 2026):

        start = dt.date(year, 1, 1)
        end = dt.date(year, 12, 31)

        try:
            count_result = search.story_count(
                query,
                start,
                end,
                collection_ids=[COLLECTION_ID]
            )

            hit_count = count_result.get("relevant", 0)

        except Exception as e:
            print(f"{year}: count ERROR {e}")
            hit_count = None

        print(f"{year}: {hit_count}")

        # 每个 shock × year 抽最多 20 条
        if hit_count and hit_count > 0:

            try:
                stories = search.story_sample(
                    query,
                    start,
                    end,
                    collection_ids=[COLLECTION_ID],
                    limit=20
                )

                for s in stories:

                    sid = s.get("id")
                    url = s.get("url")
                    key = str(sid) if sid is not None else url

                    if not key:
                        continue

                    if key not in seen:
                        seen[key] = {
                            "story_id": sid,
                            "year": year,
                            "publish_date": s.get("publish_date"),
                            "media_name": s.get("media_name"),
                            "language": s.get("language"),
                            "title": s.get("title"),
                            "url": url,
                            "shock_types": set()
                        }

                    seen[key]["shock_types"].add(shock_name)

            except Exception as e:
                print(f"{year}: sample ERROR {e}")

# -------------------------------------------------------
# 去重输出
# -------------------------------------------------------

for item in seen.values():

    all_rows.append({
        "story_id": item["story_id"],
        "year": item["year"],
        "publish_date": item["publish_date"],
        "media_name": item["media_name"],
        "language": item["language"],
        "title": item["title"],
        "url": item["url"],
        "shock_types": "|".join(sorted(item["shock_types"])),
        "manual_label": "",
        "manual_reason": "",
        "canonical_event_id": ""
    })

all_rows.sort(
    key=lambda x: (
        x["year"],
        str(x["publish_date"]),
        str(x["story_id"])
    )
)

outfile = OUT / "ecuador_shock_induced_flash_candidates_2015_2025.csv"

with open(
    outfile,
    "w",
    newline="",
    encoding="utf-8-sig"
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=all_rows[0].keys()
    )

    writer.writeheader()
    writer.writerows(all_rows)

print("\n======================================")
print("搜索完成")
print("Unique candidate stories:", len(all_rows))
print("输出文件:")
print(outfile.resolve())
print("======================================")
