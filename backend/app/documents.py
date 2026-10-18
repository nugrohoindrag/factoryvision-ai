from __future__ import annotations

from typing import Any

from app import plant

FEB_WORKING_DAYS = 24
OEM_A_SHARE = 0.80
PO_CHANGE_PCT = 40


def schedule_lines() -> list[dict[str, Any]]:
    rows = []
    for sku in ("FB-ARKA", "RB-ARKA", "FB-CITRA", "RB-CITRA"):
        share = dict(plant.CUSTOMER_MIX[plant.PRODUCT_BY_SKU[sku].model])["CUS-OEMA"]
        prev = round(plant.FEB_PLAN_PER_DAY[sku] * FEB_WORKING_DAYS * share)
        new = round(prev * (1 + PO_CHANGE_PCT / 100)) if sku == "FB-ARKA" else prev
        rows.append({"sku": sku, "month": "2027-02", "previous_qty": prev, "qty": new})
    return rows


def po_text() -> list[str]:
    lines = [
        "NUSANTARA MOTOR (OEM-A)  -  Purchasing Division",
        "REVISED DELIVERY SCHEDULE / PURCHASE ORDER",
        "",
        "PO number: NM-PO-2027-0142 (revision 2)",
        "Issue date: 2027-01-31",
        "Supplier: Vidad bumper plant",
        "",
        "Reason: MPV ARKA facelift launch and Lebaran sales program.",
        "Please confirm capacity within 2 working days.",
        "",
        "Part no.    Month     Previous qty   Revised qty",
    ]
    lines.extend(f"{r['sku']:<11} {r['month']:<9} {r['previous_qty']:>12,}   {r['qty']:>11,}" for r in schedule_lines())
    lines += ["", "Delivery: JIT to OEM-A plant, returnable racks, 3 call-offs per day.", "Late delivery penalty as per supply agreement."]
    return lines


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf(lines: list[str]) -> bytes:
    stream = "BT /F1 11 Tf 50 790 Td 15 TL\n" + "\n".join(f"({_esc(line)}) Tj T*" for line in lines) + "\nET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
        f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for k, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{k} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + "".join(f"{x:010d} 00000 n \n" for x in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


VENDOR_EMAIL = """From: sales@andalas-polimindo.example
To: purchasing@vidad.example
Date: 2027-02-01 08:12
Subject: RE: Next PP-EPDM-T20 replenishment

Dear Vidad Purchasing team,

Thank you for your forecast. For the next PP-EPDM-T20 lot (160,000 kg) our standard
lead time would give a delivery on 2027-02-08. Because of a reactor maintenance at our
compounding line, the earliest delivery we can confirm is 2027-02-11.

We apologise for the inconvenience.

Best regards,
Sales Admin, PT Andalas Polimindo
"""

VENDOR_EMAIL_FACTS = {
    "vendor_name": "PT Andalas Polimindo",
    "material_id": "PP-EPDM-T20",
    "original_date": "2027-02-08",
    "new_date": "2027-02-11",
    "delay_days": 3,
}


def demo_po_line() -> dict[str, Any]:
    r = next(x for x in schedule_lines() if x["sku"] == "FB-ARKA")
    return {**r, "change_pct": round(100 * (r["qty"] / r["previous_qty"] - 1), 1)}


DOCUMENTS = {
    "customer-po-oem-a.pdf": ("application/pdf", lambda: pdf(po_text())),
    "vendor-email-andalas.txt": ("text/plain", lambda: VENDOR_EMAIL.encode()),
}
