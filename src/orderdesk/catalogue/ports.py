"""What the resolver needs from the outside world. Implemented by MemoryCatalogue today; Odoo and Postgres later."""

from typing import Protocol

from orderdesk.domain.catalogue import Candidate, Customer, PastOrder, Product
from orderdesk.domain.quotation import AliasOutcome, QuotationRequest


class ErpPort(Protocol):
    """One instance per tenant: a method never returns another tenant's data."""

    def find_customer(self, email: str) -> Customer | None:
        """The customer this email address belongs to, or None."""
        ...

    def get_product(self, code: str) -> Product | None:
        """The product with exactly this reference, ignoring case, spaces and dashes; or None."""
        ...

    def past_orders(self, customer_id: int, limit: int) -> list[PastOrder]:
        """This customer's CONFIRMED orders, most recent first (no drafts, no cancelled orders)."""
        ...

    def get_order(self, customer_id: int, order_id: str) -> PastOrder | None:
        """One confirmed order, only if it belongs to this customer; otherwise None."""
        ...

    def find_order_by_client_ref(self, customer_id: int, client_ref: str) -> PastOrder | None:
        """This customer's confirmed order with this PO number, compared with code_key; or None."""
        ...


class ProductSearch(Protocol):
    def search(self, text: str, limit: int) -> list[Candidate]:
        """Best matches for the client's words, best first, scores between 0 and 1."""
        ...


class AliasStore(Protocol):
    def find_alias(self, customer_id: int, text: str) -> str | None:
        """The product code a person already approved for these words from this customer, or None."""
        ...


class ErpWriter(Protocol):
    """The ONLY way OrderDesk writes to the ERP. Given to the write_draft node alone, never to an agent."""

    def create_draft_quotation(self, request: QuotationRequest) -> str:
        """Create a DRAFT quotation (never a confirmed order) and return its ERP id.
        Idempotent: if a quotation with this origin already exists, return its id and create nothing."""
        ...


class AliasWriter(Protocol):
    """Remembers a person's approval. Given to the learn node alone."""

    def learn_alias(self, customer_id: int, text: str, code: str) -> AliasOutcome:
        """A person approved this product for these words; the text is normalised like find_alias's.
        No alias yet -> store it ("learned"). Same code -> nothing ("unchanged").
        ANOTHER code -> DELETE the alias ("forgotten"): unclear words go back to search, never to a guess."""
        ...
