"""Pick the shopping category for a new item.

1. Yuvomi itself: `/shopping/suggestions` returns the category the household last filed an
   article with the same name under – the household's own habit wins.
2. A small built-in keyword list (German and English) that maps common articles to
   Yuvomi's default categories. It is only used when such a category exists.

Items that match nothing keep Yuvomi's default ("Sonstiges"/"Other").
"""

from __future__ import annotations

import re
from typing import Any

from .api import YuvomiClient, YuvomiError, as_list

# Yuvomi default category (German name, English name) -> keywords (lower case, substrings)
KEYWORDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("Obst & Gemüse", "Fruit & Vegetables"): (
        "apfel", "äpfel", "banane", "birne", "orange", "zitrone", "limette", "mandarine", "traube", "beere",
        "erdbeer", "himbeer", "heidelbeer", "ananas", "mango", "kiwi", "melone", "pfirsich", "pflaume", "kirsche",
        "avocado", "tomate", "gurke", "paprika", "zwiebel", "schalotte", "knoblauch", "ingwer", "kartoffel",
        "möhre", "karotte", "lauch", "porree", "sellerie", "zucchini", "aubergine", "brokkoli", "blumenkohl",
        "kohl", "spinat", "salat", "rucola", "pilz", "champignon", "kürbis", "spargel", "radieschen", "rote bete",
        "fenchel", "mais", "erbse", "bohne", "dill", "petersilie", "basilikum", "schnittlauch", "koriander",
        "minze", "rosmarin", "thymian", "kräuter", "lauchzwiebel", "frühlingszwiebel",
        "apple", "banana", "pear", "lemon", "lime", "grape", "berry", "pineapple", "peach", "plum", "cherry",
        "tomato", "cucumber", "pepper", "onion", "garlic", "ginger", "potato", "carrot", "leek", "celery",
        "broccoli", "cauliflower", "cabbage", "spinach", "lettuce", "mushroom", "pumpkin", "herbs", "parsley",
    ),
    ("Backwaren", "Bakery"): (
        "brot", "brötchen", "semmel", "toast", "baguette", "croissant", "brezel", "laugen", "kuchen", "knäcke",
        "wrap", "tortilla", "bread", "roll", "bagel", "bun",
    ),
    ("Milchprodukte", "Dairy"): (
        "milch", "joghurt", "jogurt", "quark", "käse", "gouda", "emmentaler", "mozzarella", "feta", "parmesan",
        "frischkäse", "butter", "sahne", "schmand", "crème", "creme fraiche", "skyr", "kefir", "buttermilch", "ei",
        "eier", "milk", "yogurt", "yoghurt", "cheese", "cream", "egg",
    ),
    ("Fleisch & Fisch", "Meat & Fish"): (
        "fleisch", "hähnchen", "huhn", "hühn", "pute", "poulet", "rind", "schwein", "lamm", "kalb", "hack",
        "steak", "schnitzel", "wurst", "schinken", "salami", "speck", "bacon", "filet", "fisch", "lachs",
        "thunfisch", "garnele", "shrimp", "forelle", "kabeljau", "chicken", "beef", "pork", "lamb", "turkey",
        "sausage", "ham", "fish", "salmon", "tuna", "mince",
    ),
    ("Tiefkühl", "Frozen"): ("tiefkühl", "tk-", "tk ", "eis", "pizza", "pommes", "frozen", "ice cream", "fries"),
    ("Getränke", "Drinks"): (
        "wasser", "sprudel", "saft", "schorle", "limo", "cola", "bier", "radler", "wein", "sekt", "prosecco",
        "kaffee", "tee", "kakao", "water", "juice", "beer", "wine", "coffee", "tea", "soda",
    ),
    ("Haushalt", "Household"): (
        "spülmittel", "spüli", "spültab", "waschmittel", "weichspüler", "reiniger", "putzmittel", "müllbeutel",
        "müllsack", "küchenrolle", "küchenpapier", "toilettenpapier", "klopapier", "alufolie", "frischhalte",
        "backpapier", "schwamm", "batterie", "glühbirne", "detergent", "cleaner", "trash bag", "paper towel",
        "toilet paper", "foil", "sponge", "battery",
    ),
    ("Drogerie", "Drugstore"): (
        "shampoo", "duschgel", "seife", "zahnpasta", "zahnbürste", "deo", "creme", "lotion", "rasier",
        "tampon", "binde", "windel", "taschentuch", "wattepad", "pflaster", "tablette", "vitamin",
        "soap", "toothpaste", "toothbrush", "deodorant", "razor", "diaper", "tissue", "plaster",
    ),
}

_OTHER = {"sonstiges", "other", "others", "misc", ""}


def _norm(s: Any) -> str:
    return " ".join(str(s or "").lower().split())


def guess_from_keywords(name: str, categories: list[str]) -> str | None:
    """Best default category for `name`, if that category exists in the household."""
    n = _norm(name)
    if not n:
        return None
    existing = {_norm(c): c for c in categories}
    best: tuple[int, str] | None = None
    for names, words in KEYWORDS.items():
        target = next((existing[_norm(x)] for x in names if _norm(x) in existing), None)
        if target is None:
            continue
        for w in words:
            # whole word for very short keywords ("ei", "tee"), substring otherwise ("Hähnchenbrust")
            hit = re.search(rf"(^|\W){re.escape(w)}($|\W)", n) if len(w) <= 3 else w in n
            if hit and (best is None or len(w) > best[0]):
                best = (len(w), target)
    return best[1] if best else None


async def category_for(client: YuvomiClient, name: str, categories: list[str]) -> str | None:
    """Category for a new item, or None to leave it to Yuvomi."""
    try:
        sugg = as_list(await client.get("/api/v1/shopping/suggestions", params={"q": name}), "suggestions")
    except YuvomiError:
        sugg = []
    for s in sugg:
        if _norm(s.get("name")) == _norm(name) and _norm(s.get("category")) not in _OTHER:
            return s.get("category")
    return guess_from_keywords(name, categories)


def is_uncategorized(category: Any) -> bool:
    return _norm(category) in _OTHER
