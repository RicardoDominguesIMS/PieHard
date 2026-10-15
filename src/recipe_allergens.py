"""Allergen terminology used to interpret recipe-search constraints."""

import re


ALLERGEN_KEYWORDS = {
    "cereals containing gluten": (
        "wheat", "barley", "rye", "spelt", "durum", "semolina", "triticale",
        "malt", "oat", "oats", "trigo", "cevada", "centeio", "espelta",
        "farro", "sêmola", "semola", "malte", "aveia", "flour", "bread",
        "biscuit", "cookie", "wafer", "pão", "pao", "bolacha", "biscoito",
        "broa", "massa", "panko", "couscous", "cuscuz",
    ),
    "crustaceans": ("crab", "crayfish", "lobster", "prawn", "shrimp", "camarão", "caranguejo", "lagosta"),
    "eggs": ("egg", "eggs", "albumen", "yolk", "mayonnaise", "ovo", "ovos", "gema", "clara", "maionese"),
    "fish": ("fish", "anchovy", "cod", "salmon", "sardine", "trout", "tuna", "peixe", "bacalhau", "atum", "sardinha"),
    "peanuts": ("peanut", "peanuts", "groundnut", "amendoim"),
    "soybeans": ("soy", "soya", "tofu", "tempeh", "edamame", "soja"),
    "milk": ("milk", "butter", "cream", "cheese", "whey", "casein", "yoghurt", "yogurt", "ghee", "chantilly", "quark", "mascarpone", "leite", "manteiga", "nata", "natas", "queijo", "soro", "caseína", "caseina", "iogurte", "requeijão", "requeijao"),
    "tree nuts": (
        "almond", "hazelnut", "walnut", "cashew", "pecan", "pistachio",
        "macadamia", "chestnut", "marzipan", "amêndoa", "amendoa", "avelã",
        "avela", "noz", "nozes", "caju", "pecã", "pistácio", "pistacio",
        "macadâmia", "macadamia", "castanha", "pinhão", "pinhao", "pinenut", "hazelnuts", "almonds", "walnuts",
    ),
    "celery": ("celery", "celeriac", "aipo", "aipo-rábano"),
    "mustard": ("mustard", "mostarda"),
    "sesame": ("sesame", "tahini", "sésamo", "sesamo"),
    "sulphur dioxide and sulphites": ("sulphite", "sulfite", "e220", "e221", "e222", "e223", "e224", "e225", "e226", "e227", "e228"),
    "lupin": ("lupin", "lupine", "tremoço", "tremoco"),
    "molluscs": ("mollusc", "mollusk", "mussel", "oyster", "clam", "scallop", "squid", "octopus", "snail", "molusco", "mexilhão", "mexilhao", "ostra", "amêijoa", "ameijoa", "vieira", "lula", "polvo", "caracol"),
}

ALLERGEN_LABELS = {
    "gluten": "cereals containing gluten",
    "cereals containing gluten": "cereals containing gluten",
    "crustacean": "crustaceans",
    "crustaceans": "crustaceans",
    "egg": "eggs",
    "eggs": "eggs",
    "fish": "fish",
    "peanut": "peanuts",
    "peanuts": "peanuts",
    "soy": "soybeans",
    "soya": "soybeans",
    "soybean": "soybeans",
    "soybeans": "soybeans",
    "milk": "milk",
    "tree nuts": "tree nuts",
    "nuts": "tree nuts",
    "celery": "celery",
    "mustard": "mustard",
    "sesame": "sesame",
    "sulphites": "sulphur dioxide and sulphites",
    "sulfites": "sulphur dioxide and sulphites",
    "sulphur dioxide and sulphites": "sulphur dioxide and sulphites",
    "lupin": "lupin",
    "mollusc": "molluscs",
    "molluscs": "molluscs",
    "glúten": "cereals containing gluten",
    "trigo": "cereals containing gluten",
    "cevada": "cereals containing gluten",
    "centeio": "cereals containing gluten",
    "ovo": "eggs",
    "ovos": "eggs",
    "amendoim": "peanuts",
    "soja": "soybeans",
    "leite": "milk",
    "laticínios": "milk",
    "laticinios": "milk",
    "frutos de casca rija": "tree nuts",
    "frutos secos": "tree nuts",
    "amêndoa": "tree nuts",
    "amendoa": "tree nuts",
    "avelã": "tree nuts",
    "avela": "tree nuts",
    "noz": "tree nuts",
    "sésamo": "sesame",
    "sesamo": "sesame",
    "mostarda": "mustard",
    "aipo": "celery",
    "tremoço": "lupin",
    "tremoco": "lupin",
    "moluscos": "molluscs",
    "molusco": "molluscs",
}


# "Flour" without one of these qualifiers is treated conservatively as wheat flour.
NON_GLUTEN_FLOUR_QUALIFIERS = frozenset({
    "arroz", "milho", "amêndoa", "amendoa", "avelã", "avela", "coco", "grão",
    "rice", "corn", "almond", "hazelnut", "coconut", "chickpea",
})
FLOUR_TOKENS = frozenset({"farinha", "flour"})
GLUTEN = "cereals containing gluten"


def _token_forms(token: str) -> set[str]:
    forms = {token}
    if token.endswith("es") and len(token) > 4:
        forms.add(token[:-2])
    if token.endswith("s") and len(token) > 3:
        forms.add(token[:-1])
    return forms


def allergens_from_ingredients(ingredients: list[object]) -> list[dict[str, object]]:
    """Derive named allergen groups only from explicitly listed ingredients."""
    evidence: dict[str, list[str]] = {}
    for ingredient in ingredients:
        if isinstance(ingredient, dict):
            name = ingredient.get("name")
        else:
            name = ingredient
        if not isinstance(name, str) or not name.strip():
            continue
        raw_tokens = re.findall(r"[^\W_]+", name.casefold(), flags=re.UNICODE)
        tokens = set().union(*(_token_forms(token) for token in raw_tokens)) if raw_tokens else set()
        matched = {
            group
            for group, keywords in ALLERGEN_KEYWORDS.items()
            if any(keyword in tokens for keyword in keywords)
        }
        if tokens & FLOUR_TOKENS and not tokens & NON_GLUTEN_FLOUR_QUALIFIERS:
            matched.add(GLUTEN)
        for group in matched:
            evidence.setdefault(group, [])
            if name not in evidence[group]:
                evidence[group].append(name)
    return [
        {"name": group, "source_ingredients": source_ingredients}
        for group, source_ingredients in evidence.items()
    ]
