"""A small SYNTHETIC catalogue for LangGraph Studio and demos, until the Odoo adapter exists.
Product names and references are invented in the style of a tap and shower distributor.
"""

from orderdesk.catalogue.memory import MemoryCatalogue
from orderdesk.domain.catalogue import Customer, Product

_FINISHES = {
    "Mitigeur lavabo Profilo": ("PF60375", ["chrome", "noir brossé", "noir mat", "or rose brossé"]),
    "Mitigeur bain-douche Profilo": ("PF60380", ["chrome", "noir mat"]),
    "Colonne de douche thermostatique": ("PH60251", ["chrome", "noir mat", "vieux bronze"]),
    "Douchette ronde": ("DR10010", ["chrome", "noir mat"]),
}


def _family(name: str, first_code: str, finishes: list[str]) -> list[Product]:
    prefix, number = first_code[:2], int(first_code[2:])
    return [Product(code=f"{prefix}{number + i}", name=name, family=name, finish=finish)
            for i, finish in enumerate(finishes)]


DEMO_PRODUCTS = [
    *(product for name, (code, finishes) in _FINISHES.items() for product in _family(name, code, finishes)),
    Product(code="PE70210", name="Ensemble de douche encastré Profilo", family="Ensemble de douche encastré Profilo",
            finish="chrome"),
    Product(code="CAR-35", name="Cartouche céramique 35 mm", family="Cartouche céramique 35 mm"),
    Product(code="CAR-40", name="Cartouche céramique 40 mm", family="Cartouche céramique 40 mm"),
    Product(code="INV-3V", name="Inverseur 3 voies", family="Inverseur 3 voies", finish="chrome"),
    Product(code="FLX-150", name="Flexible inox", family="Flexible inox", uom="meter"),
]

DEMO_CUSTOMERS = {
    "achats@sanitaire-rhone.example": Customer(id=1, name="Sanitaire Rhône"),
    "contact@bains-and-co.example": Customer(id=2, name="Bains & Co"),
}

DEMO_ALIASES = {(2, "robinet profilo noir"): "PF60376"}  # Bains & Co calls the brushed-black basin mixer this


def make_demo_catalogue() -> MemoryCatalogue:
    return MemoryCatalogue(DEMO_PRODUCTS, DEMO_CUSTOMERS, DEMO_ALIASES)
