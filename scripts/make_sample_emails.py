"""Create a few sample .eml files for testing the intake step.

    python scripts/make_sample_emails.py

Writes them to tests/fixtures/emails/. Running it again overwrites them with identical files.
"""

from email.message import EmailMessage
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "emails"

KARIM_ORDER_ID = "<20261021091402.4f7a@btp-martin.fr>"
DUPONT_REPLY_ID = "<20261021093010.77c1@dupont-fixations.fr>"

ORDER_CSV = (
    "ref_client;designation;quantite;unite\n"
    "VHM840Z;vis tete hexagonale M8x40 zinguee;500;piece\n"
    ";rondelles M8;200;piece\n"
    ";chevilles comme la derniere fois;10;boite\n"
)


def karim_order() -> EmailMessage:
    """Order with a PO number and a CSV attachment."""
    msg = EmailMessage()
    msg["Message-ID"] = KARIM_ORDER_ID
    msg["Date"] = "Wed, 21 Oct 2026 09:14:02 +0200"
    msg["From"] = "Karim Benali <karim@btp-martin.fr>"
    msg["Reply-To"] = "achats@btp-martin.fr"
    msg["To"] = "orders@dupont-fixations.fr"
    msg["Cc"] = "compta@btp-martin.fr"
    msg["Subject"] = "Commande BM-2291 - livraison jeudi"
    msg.set_content(
        "Bonjour,\n\n"
        "Merci de nous livrer jeudi sur le chantier de Villeurbanne la commande ci-jointe.\n\n"
        "Karim Benali\nBTP Martin\n"
    )
    msg.add_attachment(
        ORDER_CSV.encode("utf-8"),
        maintype="text",
        subtype="csv",
        filename="BC_BM-2291.csv",
    )
    msg.set_boundary("orderdesk-sample-boundary")  # fixed, so the file is identical on every run
    return msg


def garage_order() -> EmailMessage:
    """Small order typed in the email body: no PO, no attachment."""
    msg = EmailMessage()
    msg["Message-ID"] = "<CAF8x2k3@mail.garage-dubois.fr>"
    msg["Date"] = "Wed, 21 Oct 2026 10:02:47 +0200"
    msg["From"] = "Garage Dubois <achats@garage-dubois.fr>"
    msg["To"] = "orders@dupont-fixations.fr"
    msg["Subject"] = "commande"
    msg.set_content(
        "Bonjour,\n\n"
        "Pouvez-vous nous envoyer 2 boites de vis a bois 5x50 et 100 ecrous M10.\n\n"
        "Merci,\nGarage Dubois\n"
    )
    return msg


def karim_correction() -> EmailMessage:
    """Karim replies to Dupont's confirmation to change a quantity."""
    msg = EmailMessage()
    msg["Message-ID"] = "<20261021112233.9b2e@btp-martin.fr>"
    msg["Date"] = "Wed, 21 Oct 2026 11:22:33 +0200"
    msg["From"] = "Karim Benali <karim@btp-martin.fr>"
    msg["To"] = "orders@dupont-fixations.fr"
    msg["Subject"] = "RE: Commande BM-2291 - livraison jeudi"
    msg["In-Reply-To"] = DUPONT_REPLY_ID
    msg["References"] = f"{KARIM_ORDER_ID} {DUPONT_REPLY_ID}"
    msg.set_content(
        "Bonjour,\n\n"
        "Petite correction : 1000 rondelles au lieu de 200.\n\n"
        "Karim\n\n"
        "> Le 21 oct. 2026 a 09:30, Dupont Fixations a ecrit :\n"
        "> Commande BM-2291 bien recue, livraison jeudi.\n"
    )
    return msg


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    order = karim_order()
    files = {
        "01_karim_order_BM-2291.eml": order,
        "02_garage_dubois_order_no_po.eml": garage_order(),
        "03_karim_correction_reply.eml": karim_correction(),
        # The exact same message delivered a second time: same Message-ID, same bytes.
        "04_karim_order_duplicate.eml": order,
    }
    for name, msg in files.items():
        path = OUT_DIR / name
        path.write_bytes(msg.as_bytes())
        print(f"wrote {path.relative_to(OUT_DIR.parent.parent.parent)}")


if __name__ == "__main__":
    main()