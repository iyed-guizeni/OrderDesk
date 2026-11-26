"""A catalogue held in memory: tests, Studio and demo sandboxes.

It implements ErpPort, ErpWriter, ProductSearch, AliasStore and AliasWriter. The real ones (Odoo adapter,
Postgres full-text + pgvector search, the product_synonyms table) keep the same methods.
"""

from collections.abc import Iterable, Mapping

from orderdesk.catalogue.text import code_key, normalize, tokens
from orderdesk.domain.catalogue import Candidate, Customer, PastOrder, Product
from orderdesk.domain.quotation import AliasOutcome, QuotationRequest

DRAFT_PREFIX = "DRAFT"


class MemoryCatalogue:
    def __init__(
        self,
        products: Iterable[Product],
        customers: Mapping[str, Customer] | None = None,  # email address -> customer
        aliases: Mapping[tuple[int, str], str] | None = None,  # (customer id, client words) -> product code
        past_orders: Mapping[int, Iterable[PastOrder]] | None = None,  # customer id -> confirmed orders
    ) -> None:
        self._products = {code_key(p.code): p for p in products}
        self._customers = {email.lower(): customer for email, customer in (customers or {}).items()}
        self._aliases = {(cid, normalize(text)): code for (cid, text), code in (aliases or {}).items()}
        self._orders = {
            cid: sorted(orders, key=lambda o: o.date, reverse=True) for cid, orders in (past_orders or {}).items()
        }
        self._tokens = {key: tokens(f"{p.code} {p.name} {p.finish or ''}") for key, p in self._products.items()}
        self._draft_ids_by_origin: dict[str, str] = {}
        self._draft_quotations: dict[str, QuotationRequest] = {}

    def find_customer(self, email: str) -> Customer | None:
        return self._customers.get(email.strip().lower())

    def get_product(self, code: str) -> Product | None:
        return self._products.get(code_key(code))

    def past_orders(self, customer_id: int, limit: int) -> list[PastOrder]:
        return self._orders.get(customer_id, [])[:limit]

    def get_order(self, customer_id: int, order_id: str) -> PastOrder | None:
        wanted = code_key(order_id)
        return next((o for o in self._orders.get(customer_id, []) if code_key(o.order_id) == wanted), None)

    def find_order_by_client_ref(self, customer_id: int, client_ref: str) -> PastOrder | None:
        wanted = code_key(client_ref)
        return next(
            (
                order
                for order in self._orders.get(customer_id, [])
                if order.client_ref is not None and code_key(order.client_ref) == wanted
            ),
            None,
        )

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

    @property
    def draft_quotations(self) -> dict[str, QuotationRequest]:
        return dict(self._draft_quotations)

    def create_draft_quotation(self, request: QuotationRequest) -> str:
        existing = self._draft_ids_by_origin.get(request.origin)
        if existing is not None:
            return existing

        quotation_id = f"{DRAFT_PREFIX}-{len(self._draft_quotations) + 1:04d}"
        self._draft_ids_by_origin[request.origin] = quotation_id
        self._draft_quotations[quotation_id] = request
        return quotation_id

    def learn_alias(self, customer_id: int, text: str, code: str) -> AliasOutcome:
        key = (customer_id, normalize(text))
        existing = self._aliases.get(key)
        if existing is None:
            self._aliases[key] = code
            return "learned"
        if existing == code:
            return "unchanged"
        del self._aliases[key]
        return "forgotten"
