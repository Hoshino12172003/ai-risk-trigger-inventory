import pandas as pd
from pathlib import Path
import re

INPUT = Path("mediacloud_ecuador_flash_v2/ecuador_flash_v2_samples_deduplicated.csv")
OUTDIR = Path("mediacloud_ecuador_flash_v3")
OUTDIR.mkdir(exist_ok=True)

if not INPUT.exists():
    raise FileNotFoundError(
        f"找不到输入文件：{INPUT.resolve()}"
    )

df = pd.read_csv(INPUT)

# ---------------------------------------------------------
# 1. 基础文本标准化
# ---------------------------------------------------------

def norm(x):
    if pd.isna(x):
        return ""
    return str(x).lower().strip()

df["title_norm"] = df["title"].apply(norm)

# ---------------------------------------------------------
# 2. 明显不属于消费者/零售 Flash demand 的排除规则
#
# 注意：
# 这里只标记“likely false positive”，不删除原始记录。
# ---------------------------------------------------------

government_procurement = [
    "contratación pública",
    "contratacion publica",
    "licitación",
    "licitacion",
    "ministerio",
    "gobierno compra",
    "compras públicas",
    "compras publicas",
    "adquisición pública",
    "adquisicion publica",
    "iess",
    "hospital",
    "hospitales",
]

macro_or_industrial = [
    "petróleo",
    "petroleo",
    "opep",
    "exportaciones",
    "exportación",
    "exportacion",
    "demanda mundial",
    "demanda global",
    "demanda internacional",
    "mercado internacional",
    "sector automotor",
    "automotriz",
    "vehículos",
    "vehiculos",
    "producción industrial",
    "produccion industrial",
]

non_retail_financial = [
    "acciones",
    "bolsa de valores",
    "bonos",
    "mercado bursátil",
    "mercado bursatil",
    "inversionistas",
]

# ---------------------------------------------------------
# 3. 正向证据词
# ---------------------------------------------------------

flash_positive = [
    "compras de pánico",
    "compras de panico",
    "compras masivas",
    "se dispararon las compras",
    "aumentaron las compras",
    "alta demanda",
    "fuerte demanda",
    "demanda se dispar",
    "aumento repentino de la demanda",
    "récord de ventas",
    "record de ventas",
    "ventas se dispar",
    "duplicaron las ventas",
    "triplicaron las ventas",
    "agotado",
    "agotada",
    "agotados",
    "agotadas",
    "se agotó",
    "se agoto",
    "se agotaron",
    "sin existencias",
    "sold out",
    "desabastecimiento",
]

consumer_context = [
    "consumidor",
    "consumidores",
    "comprador",
    "compradores",
    "cliente",
    "clientes",
    "supermercado",
    "supermercados",
    "tienda",
    "tiendas",
    "centro comercial",
    "centros comerciales",
    "hogar",
    "hogares",
    "familias",
    "pedidos",
    "ventas",
    "compras",
]

ecuador_geo = [
    "ecuador",
    "quito",
    "guayaquil",
    "cuenca",
    "guayas",
    "pichincha",
    "azuay",
    "manabí",
    "manabi",
    "los ríos",
    "los rios",
    "el oro",
    "loja",
    "tungurahua",
    "chimborazo",
    "imbabura",
    "cotopaxi",
    "latacunga",
    "manta",
    "machala",
    "ambato",
    "riobamba",
]

# ---------------------------------------------------------
# 4. 打标签函数
# ---------------------------------------------------------

def contains_any(text, terms):
    return any(term in text for term in terms)

def classify(row):
    title = row["title_norm"]

    reasons = []

    # 明显排除项
    if contains_any(title, government_procurement):
        reasons.append("government_or_health_procurement")

    if contains_any(title, macro_or_industrial):
        reasons.append("macro_or_industrial_demand")

    if contains_any(title, non_retail_financial):
        reasons.append("financial_market_context")

    # 正向证据
    has_flash = contains_any(title, flash_positive)
    has_consumer = contains_any(title, consumer_context)
    has_geo = contains_any(title, ecuador_geo)

    # -----------------------------------------------------
    # 保守分类：
    #
    # 1. 明显采购/宏观/金融 -> LIKELY_FALSE_POSITIVE
    # 2. 标题同时有 flash + consumer + Ecuador -> KEEP_FOR_REVIEW
    # 3. 其他 -> NEEDS_MANUAL_REVIEW
    #
    # 不会自动判 TRUE_FLASH
    # -----------------------------------------------------

    if reasons:
        return pd.Series([
            "LIKELY_FALSE_POSITIVE",
            "|".join(reasons),
            has_flash,
            has_consumer,
            has_geo,
        ])

    if has_flash and has_consumer and has_geo:
        return pd.Series([
            "KEEP_FOR_REVIEW",
            "strong_title_level_signal",
            has_flash,
            has_consumer,
            has_geo,
        ])

    missing = []

    if not has_flash:
        missing.append("no_explicit_flash_signal")

    if not has_consumer:
        missing.append("no_consumer_retail_context")

    if not has_geo:
        missing.append("no_ecuador_geo_in_title")

    return pd.Series([
        "NEEDS_MANUAL_REVIEW",
        "|".join(missing),
        has_flash,
        has_consumer,
        has_geo,
    ])

df[
    [
        "v3_prelabel",
        "v3_reason",
        "has_flash_signal",
        "has_consumer_context",
        "has_ecuador_geo_title",
    ]
] = df.apply(classify, axis=1)

# ---------------------------------------------------------
# 5. 增加人工审核字段
# ---------------------------------------------------------

df["manual_label"] = ""
df["manual_reason"] = ""
df["event_location"] = ""
df["affected_product_or_category"] = ""
df["flash_evidence"] = ""
df["final_event_id"] = ""

# ---------------------------------------------------------
# 6. 输出完整 review 表
# ---------------------------------------------------------

columns = [
    "story_id",
    "year",
    "publish_date",
    "media_name",
    "title",
    "url",
    "matched_queries",
    "v3_prelabel",
    "v3_reason",
    "has_flash_signal",
    "has_consumer_context",
    "has_ecuador_geo_title",
    "manual_label",
    "manual_reason",
    "event_location",
    "affected_product_or_category",
    "flash_evidence",
    "final_event_id",
]

review = df[columns].copy()

review.to_csv(
    OUTDIR / "ecuador_flash_v3_review.csv",
    index=False,
    encoding="utf-8-sig"
)

# ---------------------------------------------------------
# 7. 分别输出三类
# ---------------------------------------------------------

for label in [
    "KEEP_FOR_REVIEW",
    "NEEDS_MANUAL_REVIEW",
    "LIKELY_FALSE_POSITIVE",
]:
    subset = review[review["v3_prelabel"] == label]

    subset.to_csv(
        OUTDIR / f"{label.lower()}.csv",
        index=False,
        encoding="utf-8-sig"
    )

# ---------------------------------------------------------
# 8. 输出统计表
# ---------------------------------------------------------

summary = (
    review["v3_prelabel"]
    .value_counts(dropna=False)
    .rename_axis("category")
    .reset_index(name="count")
)

summary["share"] = summary["count"] / len(review)

summary.to_csv(
    OUTDIR / "v3_screening_summary.csv",
    index=False,
    encoding="utf-8-sig"
)

print("\n======================================")
print("Flash-demand V3 conservative screening 完成")
print("======================================")
print(f"输入记录数: {len(review)}")
print("")
print(summary.to_string(index=False))
print("")
print("输出目录:")
print(OUTDIR.resolve())
print("")
print("主要文件:")
print("1) ecuador_flash_v3_review.csv")
print("2) keep_for_review.csv")
print("3) needs_manual_review.csv")
print("4) likely_false_positive.csv")
print("5) v3_screening_summary.csv")
print("======================================")
