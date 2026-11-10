"""What the Extract step produces from one order email.

Rule: Extract records what the client WROTE. It never chooses a product, a price or a customer:
those come later from the ERP (Resolve, Validate). Header data (sender, date, attachments) comes from the parser.

Bound to the LLM as a tool: the field descriptions reach the model. Only hard fields get a description.
Field order matters: the model writes the JSON top to bottom, so raw_text comes first in each line.
Works for any business: company-specific words come from the business profile, not from here.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Unit = Literal["piece", "box", "pack", "set", "pallet", "meter", "kg", "tonne", "liter", "other"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")  # the LLM may not add fields of its own


class ProductInfo(_Model):
    kind: Literal["named", "previous_order", "missing"] = Field(
        description=(
            "named: a product is written, even vaguely ('mitigeur Profilo', 'des cartouches', 'M8 screws'). "
            "previous_order: instead of naming the product, the client points to an earlier order "
            "('les mêmes que la dernière fois', 'comme d'habitude', 'same as last time'). "
            "missing: no product AND no reference ('et 10 boîtes aussi')."
        )
    )
    text: str | None = Field(
        None,
        description="Product words exactly as written, without quantity or variant. Never translate or complete.",
    )
    variant_text: str | None = Field(
        None,
        description="Finish, colour, size or model option exactly as written ('noir brossé', 'chrome', 'M8x40').",
    )
    code_as_written: str | None = Field(
        None,
        description="A product reference ONLY if the client wrote one (e.g. 'PF60375'). Never invent one.",
    )
    reference_text: str | None = Field(
        None,
        description="When kind is previous_order: the client's exact words that point to the earlier order.",
    )


class ExtractedLine(_Model):
    raw_text: str = Field(description="The whole line exactly as the client wrote it, including the quantity.")
    quantity: float | None = Field(
        None,
        description=(
            "Number of units ordered. French: '1 000' and '1.000' -> 1000, '1,5' -> 1.5. "
            "English: '1,000' -> 1000, '1.5' -> 1.5. 'une douzaine' / 'a dozen' -> 12. "
            "For '2 cartons de 100' the quantity is 2 (unit box). null if no quantity is written."
        ),
    )
    unit: Unit | None = Field(
        None,
        description=(
            "boîte, carton, box -> box; paquet, sachet, lot, bag -> pack; kit, ensemble, set -> set; "
            "palette -> pallet; m -> meter; t, tonne -> tonne; l, litre -> liter; "
            "pièce, unité, pcs, or no unit written -> piece; anything else -> other."
        ),
    )
    product: ProductInfo
    price_mentioned: float | None = Field(
        None,
        description="A unit price ONLY if the client wrote one for this line. Never use prices you know.",
    )
    note: str | None = Field(
        None, description="A condition the client set on this line ('livrer séparément'). Not the product."
    )
    source: Literal["body", "attachment"] = Field(
        "body", description="Where the line was found: the email text, or the attachment text."
    )


class Delivery(_Model):
    kind: Literal["usual", "address", "site", "pickup"] = Field(
        description=(
            "usual: 'comme d'habitude', 'à l'adresse habituelle'. address: a postal address is written. "
            "site: a construction site or place name without full address ('chantier de Vienne'). "
            "pickup: the client comes to collect ('on passe récupérer', 'retrait au comptoir')."
        )
    )
    text: str | None = Field(None, description="The delivery words exactly as written.")


class RequestedDate(_Model):
    text: str = Field(description="The client's words about timing exactly as written ('avant vendredi', 'urgent').")
    value: date | None = Field(
        None,
        description=(
            "The date as YYYY-MM-DD ONLY if it is certain from the text and the email date given in the prompt. "
            "null for vague timing ('dès que possible') or ambiguous dates ('03/11' could be two dates)."
        ),
    )


class OrderReference(_Model):
    reference_text: str = Field(
        description=(
            "The client's exact words asking to repeat a whole earlier order ('même commande que le mois dernier')."
        )
    )
    which_order: str | None = Field("last", description="'last' by default, or the order the client names.")


class ExtractedOrder(_Model):
    po_number: str | None = Field(
        None,
        description=(
            "The client's own order reference (bon de commande, BC, PO, 'notre commande n°'). "
            "Not an invoice number, not a phone number, not one of OUR quotation or order numbers."
        ),
    )
    customer_name_as_written: str | None = Field(
        None, description="The client's company name as written, usually in the signature or the attachment."
    )
    contact_name: str | None = Field(None, description="The name of the person who wrote the email.")
    delivery: Delivery | None = None
    requested_date: RequestedDate | None = None
    notes: list[str] = Field(
        default_factory=list,
        description="Short instructions for the whole order that fit no other field ('livrer avant 8h').",
    )
    attachment_mentioned: bool = Field(
        False,
        description="true if the text says a file is attached ('ci-joint', 'en pièce jointe', 'please find attached').",
    )
    order_reference: OrderReference | None = Field(
        None, description="Only when the client asks to repeat a WHOLE earlier order instead of listing lines."
    )
    lines: list[ExtractedLine] = Field(
        default_factory=list,
        description=(
            "One entry per product line, in the order written, from the email text and the attachment text. "
            "Do not merge or split lines. If the same line appears in both, keep it once (source attachment). "
            "Ignore greetings, signatures, totals and lines that are not products."
        ),
    )