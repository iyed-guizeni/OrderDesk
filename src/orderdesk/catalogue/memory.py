"""A catalogue held in memory: tests, LangGraph Studio and demo sandboxes. Implements ErpPort, ProductSearch
and AliasStore. The real ones (Odoo adapter, Postgres full-text + pgvector search) keep the same methods.
"""

from collections.abc import Iterable, Mapping

from orderdesk.catalogue.text import code_key, normalize, tokens
from orderdesk.domain.catalogue import Candidate, Customer, Product


class MemoryCatalogue:
    def __init__(
        self,
        products: Iterable[Product],
        customers: Mapping[str, Customer] | None = None,  # email address -> customer
        aliases: Mapping[tuple[int, str], str] | None = None,  # (customer id, client words) -> product code
    ) -> None:
        self._products = {code_key(p.code): p for p in products}
        self._customers = {email.lower(): customer for email, customer in (customers or {}).items()}
        self._aliases = {(cid, normalize(text)): code for (cid, text), code in (aliases or {}).items()}
        self._tokens = {
            key: tokens(f"{p.code} {p.name} {p.finish or ''}") for key, p in self._products.items()
        }

    def find_customer(self, email: str) -> Customer | None:
        return self._customers.get(email.strip().lower())

    def get_product(self, code: str) -> Product | None:
        return self._products.get(code_key(code))

    def search(self, text: str, limit: int) -> list[Candidate]:
        """Score = share of the client's words found in the product (code, name, finish)."""
        wanted = tokens(text)
        if not wanted:
            return []
        scored = [
            Candidate(product=self._products[key], score=round(len(wanted & words) / len(wanted), 3))
            for key, words in self._tokens.items()
            if wanted & words
        ]
        return sorted(scored, key=lambda c: (-c.score, c.product.code))[:limit]

    def find_alias(self, customer_id: int, text: str) -> str | None:
        return self._aliases.get((customer_id, normalize(text)))
