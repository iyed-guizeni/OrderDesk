"""What the resolver needs from the outside world. Implemented by MemoryCatalogue today; Odoo and Postgres later."""

from typing import Protocol

from orderdesk.domain.catalogue import Candidate, Customer, Product


class ErpPort(Protocol):
    def find_customer(self, email: str) -> Customer | None:
        """The customer this email address belongs to, or None."""
        ...

    def get_product(self, code: str) -> Product | None:
        """The product with exactly this reference, ignoring case, spaces and dashes; or None."""
        ...


class ProductSearch(Protocol):
    def search(self, text: str, limit: int) -> list[Candidate]:
        """Best matches for the client's words, best first, scores between 0 and 1."""
        ...


class AliasStore(Protocol):
    def find_alias(self, customer_id: int, text: str) -> str | None:
        """The product code a person already approved for these words from this customer, or None."""
        ...
