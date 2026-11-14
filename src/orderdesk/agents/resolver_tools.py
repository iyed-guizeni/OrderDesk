"""The Resolver agent's tools: read-only, built for ONE line of ONE customer, returning short text tables.

Design rules
- Read-only: no tool can change anything in the ERP.
- Bound by code: the customer is fixed when the tools are built. No tool takes a customer id,
  so the model (or an injected email) can never ask for another customer's data.
- Small results: a few rows, a few columns; never whole ERP records (cost, noise, privacy).
- Problems are answers, not exceptions: "nothing found, try X" lets the agent change its plan.
- Arguments are clamped: a model asking for 500 orders gets MAX_PAST_ORDERS.
"""

from langchain_core.tools import BaseTool, tool

from orderdesk.catalogue.ports import ErpPort, ProductSearch
from orderdesk.domain.catalogue import Customer, Product

MAX_PAST_ORDERS = 5
MAX_SEARCH_RESULTS = 5
MAX_QUERY_CHARS = 100


def _finish(finish: str | None) -> str:
    return finish or "no finish"


def product_row(product: Product) -> str:
    status = "active" if product.active else "discontinued"
    return f"{product.code} | {product.name} | {_finish(product.finish)} | sold by {product.uom} | {status}"


def make_resolver_tools(erp: ErpPort, search: ProductSearch, customer: Customer | None) -> list[BaseTool]:
    """The tools for one line. Without a known customer there is no history tool at all."""

    @tool
    def search_catalogue(text: str) -> str:
        """Search the company's catalogue with a few words (product type, model, finish, size).
        Use it to try other wordings than the client's: a synonym, fewer words, without the finish.
        Returns up to 5 products as: code | name | finish | unit | status."""
        query = text.strip()[:MAX_QUERY_CHARS]
        if not query:
            return "Give a few words to search for."
        rows = [product_row(c.product) for c in search.search(query, MAX_SEARCH_RESULTS)]
        if not rows:
            return f"No product matches '{query}'. Try fewer or other words (a synonym, without the finish)."
        return "\n".join(rows)

    @tool
    def get_product(code: str) -> str:
        """Look up one product by its exact code, e.g. to check a code from an order or a possible typo.
        Returns: code | name | finish | unit | status."""
        product = erp.get_product(code.strip()[:MAX_QUERY_CHARS])
        if product is None:
            return f"No product with code '{code}'. If it may be a typo, search the catalogue with the product words."
        return product_row(product)

    tools: list[BaseTool] = [search_catalogue, get_product]
    if customer is None:
        return tools

    @tool
    def find_past_orders(limit: int = 3) -> str:
        """This customer's most recent CONFIRMED orders (newest first), one row per order line:
        order id | date | code | name | finish | quantity. Use it for 'same as last time' or a usual finish."""
        orders = erp.past_orders(customer.id, max(1, min(limit, MAX_PAST_ORDERS)))
        if not orders:
            return "This customer has no confirmed orders yet."
        return "\n".join(
            f"{order.order_id} | {order.date.isoformat()} | {line.code} | {line.name} | {_finish(line.finish)}"
            f" | qty {line.quantity:g}"
            for order in orders
            for line in order.lines
        )

    return [*tools, find_past_orders]
