"""Observed region_origin_code values are two-digit KATO region prefixes.
Raw inventory: waiting extract has 20 prefixes, vaccination extract has names
but no authoritative join. Reference: https://stat.gov.kz/ru/classifiers/statistical/21/
Verified against official KATO_17.07.2026.xlsx, top-level rows and AB column.
Origin region is not the destination organization's location.
"""

REGIONS = dict(
    zip(
        [
            "10",
            "11",
            "15",
            "19",
            "23",
            "27",
            "31",
            "33",
            "35",
            "39",
            "43",
            "47",
            "55",
            "59",
            "61",
            "62",
            "63",
            "71",
            "75",
            "79",
        ],
        [
            "Абайская область",
            "Акмолинская область",
            "Актюбинская область",
            "Алматинская область",
            "Атырауская область",
            "Западно-Казахстанская область",
            "Жамбылская область",
            "Жетысуская область",
            "Карагандинская область",
            "Костанайская область",
            "Кызылординская область",
            "Мангистауская область",
            "Павлодарская область",
            "Северо-Казахстанская область",
            "Туркестанская область",
            "Улытауская область",
            "Восточно-Казахстанская область",
            "Астана",
            "Алматы",
            "Шымкент",
        ],
        strict=True,
    )
)


def region_name(code):
    normalized = str(code).strip()
    return REGIONS.get(normalized, f"Неизвестный регион (код {normalized})")
