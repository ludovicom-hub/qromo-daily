#!/usr/bin/env python3
"""
Qromo Daily Digest — Telegram bot
Esegue alle 9:00 ogni mattina, manda lo snapshot del giorno precedente.

Variabili d'ambiente richieste:
  METABASE_URL        es. https://metabase.qromo.it
  METABASE_API_KEY    API key generata in Metabase (Admin > Settings > API Keys)
  TELEGRAM_TOKEN      token del bot da @BotFather
  TELEGRAM_CHAT_ID    chat_id (numerico) destinatario
"""

import os
import sys
import json
import requests
from datetime import datetime

# ---------- Config ----------
METABASE_URL = os.environ["METABASE_URL"].rstrip("/")
METABASE_API_KEY = os.environ["METABASE_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
DATABASE_ID = 2  # qromo_analytics (Redshift)

HEADERS = {
    "x-api-key": METABASE_API_KEY,
    "Content-Type": "application/json",
}

# Mappa giorni della settimana in italiano
GIORNI_IT = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]


# ---------- Helpers ----------
def fmt_eur(n):
    """Formatta un numero come euro all'italiana: 1.195.727 → '€1.195.727'"""
    if n is None:
        return "—"
    return f"€{n:,.0f}".replace(",", ".")


def fmt_pct(n):
    if n is None:
        return "—"
    sign = "+" if n >= 0 else ""
    return f"{sign}{n:.1f}%"


def run_query(sql):
    """Esegue una query SQL via Metabase /api/dataset"""
    payload = {
        "type": "native",
        "native": {"query": sql},
        "database": DATABASE_ID,
    }
    r = requests.post(
        f"{METABASE_URL}/api/dataset",
        headers=HEADERS,
        json=payload,
        timeout=180,
    )
    r.raise_for_status()
    data = r.json()
    if "error" in data.get("data", {}):
        raise RuntimeError(f"Metabase error: {data['data']['error']}")
    cols = [c["name"] for c in data["data"]["cols"]]
    rows = data["data"]["rows"]
    return [dict(zip(cols, row)) for row in rows]


def send_telegram(text):
    """Invia messaggio Telegram in modalità HTML"""
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    r.raise_for_status()


# ---------- Query SQL ----------
SQL_PULSE = """
WITH date_ranges AS (
    SELECT
        DATE_TRUNC('day', SYSDATE) - INTERVAL '21 hours' AS ieri_start,
        DATE_TRUNC('day', SYSDATE) + INTERVAL '3 hours' AS ieri_end,
        DATE_TRUNC('day', SYSDATE) - INTERVAL '7 days' - INTERVAL '21 hours' AS wow_start,
        DATE_TRUNC('day', SYSDATE) - INTERVAL '7 days' + INTERVAL '3 hours' AS wow_end,
        DATE_TRUNC('day', SYSDATE) - INTERVAL '7 days' - INTERVAL '21 hours' AS l7d_start,
        DATE_TRUNC('day', SYSDATE) + INTERVAL '3 hours' AS l7d_end
),
tov_ieri AS (
    SELECT COUNT(o.order_id) AS n, SUM(o.grand_total)/100.0 AS tov
    FROM qromo.orders o, date_ranges d
    WHERE o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
      AND o.paid = 1 AND o.date >= d.ieri_start AND o.date < d.ieri_end
),
tov_wow AS (
    SELECT SUM(o.grand_total)/100.0 AS tov
    FROM qromo.orders o, date_ranges d
    WHERE o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
      AND o.paid = 1 AND o.date >= d.wow_start AND o.date < d.wow_end
),
ttv_ieri_single AS (
    SELECT SUM(o.grand_total)/100.0 AS ttv
    FROM qromo.orders o, date_ranges d
    WHERE o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
      AND o.paid = 1 AND o.type = 'stripe'
      AND o.date >= d.ieri_start AND o.date < d.ieri_end
),
ttv_ieri_multi AS (
    SELECT SUM(amount)/100.0 AS ttv FROM (
        SELECT DISTINCT op.payment_id, p.amount
        FROM qromo.orders_payments op
        JOIN qromo.payments p ON p.payment_id = op.payment_id
        JOIN qromo.orders o ON o.order_id = op.order_id
        CROSS JOIN date_ranges d
        WHERE p.type = 'stripe'
          AND o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
          AND o.paid = 1 AND o.type != 'stripe'
          AND o.date >= d.ieri_start AND o.date < d.ieri_end
    )
),
ttv_wow_single AS (
    SELECT SUM(o.grand_total)/100.0 AS ttv
    FROM qromo.orders o, date_ranges d
    WHERE o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
      AND o.paid = 1 AND o.type = 'stripe'
      AND o.date >= d.wow_start AND o.date < d.wow_end
),
ttv_wow_multi AS (
    SELECT SUM(amount)/100.0 AS ttv FROM (
        SELECT DISTINCT op.payment_id, p.amount
        FROM qromo.orders_payments op
        JOIN qromo.payments p ON p.payment_id = op.payment_id
        JOIN qromo.orders o ON o.order_id = op.order_id
        CROSS JOIN date_ranges d
        WHERE p.type = 'stripe'
          AND o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
          AND o.paid = 1 AND o.type != 'stripe'
          AND o.date >= d.wow_start AND o.date < d.wow_end
    )
),
mer_ieri_tov AS (
    SELECT COUNT(DISTINCT o.business_id) AS n
    FROM qromo.orders o, date_ranges d
    WHERE o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
      AND o.paid = 1 AND o.date >= d.ieri_start AND o.date < d.ieri_end
),
mer_ieri_ttv AS (
    SELECT COUNT(DISTINCT business_id) AS n FROM (
        SELECT DISTINCT o.business_id
        FROM qromo.orders o, date_ranges d
        WHERE o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
          AND o.paid = 1 AND o.type = 'stripe'
          AND o.date >= d.ieri_start AND o.date < d.ieri_end
        UNION
        SELECT DISTINCT o.business_id
        FROM qromo.orders_payments op
        JOIN qromo.payments p ON p.payment_id = op.payment_id
        JOIN qromo.orders o ON o.order_id = op.order_id
        CROSS JOIN date_ranges d
        WHERE p.type = 'stripe'
          AND o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
          AND o.paid = 1 AND o.type != 'stripe'
          AND o.date >= d.ieri_start AND o.date < d.ieri_end
    )
),
mer_l7d_tov_avg AS (
    SELECT COUNT(DISTINCT o.business_id || '|' || DATE_TRUNC('day', o.date))::DECIMAL / 7 AS n_avg
    FROM qromo.orders o, date_ranges d
    WHERE o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
      AND o.paid = 1 AND o.date >= d.l7d_start AND o.date < d.l7d_end
),
mer_l7d_ttv_avg AS (
    SELECT COUNT(DISTINCT business_id_day)::DECIMAL / 7 AS n_avg FROM (
        SELECT DISTINCT o.business_id || '|' || DATE_TRUNC('day', o.date) AS business_id_day
        FROM qromo.orders o, date_ranges d
        WHERE o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
          AND o.paid = 1 AND o.type = 'stripe'
          AND o.date >= d.l7d_start AND o.date < d.l7d_end
        UNION
        SELECT DISTINCT o.business_id || '|' || DATE_TRUNC('day', o.date) AS business_id_day
        FROM qromo.orders_payments op
        JOIN qromo.payments p ON p.payment_id = op.payment_id
        JOIN qromo.orders o ON o.order_id = op.order_id
        CROSS JOIN date_ranges d
        WHERE p.type = 'stripe'
          AND o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
          AND o.paid = 1 AND o.type != 'stripe'
          AND o.date >= d.l7d_start AND o.date < d.l7d_end
    )
)
SELECT
    ti.n AS orders_ieri, ti.tov AS tov_ieri,
    tw.tov AS tov_wow,
    COALESCE(tis.ttv, 0) + COALESCE(tim.ttv, 0) AS ttv_ieri,
    COALESCE(tws.ttv, 0) + COALESCE(twm.ttv, 0) AS ttv_wow,
    mi.n AS merchant_ieri,
    ml.n_avg AS merchant_l7d_avg
FROM tov_ieri ti, tov_wow tw, ttv_ieri_single tis, ttv_ieri_multi tim,
     ttv_wow_single tws, ttv_wow_multi twm, mer_ieri mi, mer_l7d_avg ml
"""

SQL_NUOVI_MERCHANT = """
WITH date_ranges AS (
    SELECT
        DATE_TRUNC('day', SYSDATE) - INTERVAL '21 hours' AS ieri_start,
        DATE_TRUNC('day', SYSDATE) + INTERVAL '3 hours' AS ieri_end
),
first_paid AS (
    SELECT business_id, MIN(date) AS first_order_at
    FROM qromo.orders
    WHERE status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
      AND paid = 1
    GROUP BY business_id
)
SELECT
    fp.business_id,
    b.name AS merchant_name,
    DATEDIFF(day, b.registration_date, fp.first_order_at) AS days_reg_to_first,
    COUNT(o.order_id) AS n_orders,
    SUM(o.grand_total)/100.0 AS tov_eur
FROM first_paid fp
JOIN date_ranges d ON fp.first_order_at >= d.ieri_start AND fp.first_order_at < d.ieri_end
LEFT JOIN qromo.businesses b ON b.business_id = fp.business_id
LEFT JOIN qromo.orders o ON o.business_id = fp.business_id
    AND o.date >= d.ieri_start AND o.date < d.ieri_end
    AND o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
    AND o.paid = 1
GROUP BY fp.business_id, b.name, b.registration_date, fp.first_order_at
ORDER BY tov_eur DESC NULLS LAST
"""

SQL_TOP3 = """
WITH date_ranges AS (
    SELECT
        DATE_TRUNC('day', SYSDATE) - INTERVAL '21 hours' AS ieri_start,
        DATE_TRUNC('day', SYSDATE) + INTERVAL '3 hours' AS ieri_end
)
SELECT
    o.business_id,
    b.name AS merchant_name,
    SUM(o.grand_total)/100.0 AS tov_eur
FROM qromo.orders o
LEFT JOIN qromo.businesses b ON b.business_id = o.business_id
CROSS JOIN date_ranges d
WHERE o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
  AND o.paid = 1
  AND o.date >= d.ieri_start AND o.date < d.ieri_end
GROUP BY o.business_id, b.name
ORDER BY tov_eur DESC
LIMIT 3
"""

SQL_DROP = """
WITH date_ranges AS (
    SELECT
        DATE_TRUNC('day', SYSDATE) - INTERVAL '21 hours' AS ieri_start,
        DATE_TRUNC('day', SYSDATE) + INTERVAL '3 hours' AS ieri_end,
        DATE_TRUNC('day', SYSDATE) - INTERVAL '7 days' - INTERVAL '21 hours' AS l7d_prev_start
),
top_merchants AS (
    SELECT o.business_id, SUM(o.grand_total)/100.0 AS tov_l7d
    FROM qromo.orders o, date_ranges d
    WHERE o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
      AND o.paid = 1 AND o.date >= d.l7d_prev_start AND o.date < d.ieri_end
    GROUP BY o.business_id
    ORDER BY tov_l7d DESC LIMIT 30
),
continuity AS (
    SELECT tm.business_id,
        COUNT(DISTINCT DATE_TRUNC('day', o.date - INTERVAL '3 hours')) AS active_days_prev6d
    FROM top_merchants tm
    JOIN qromo.orders o ON o.business_id = tm.business_id
    CROSS JOIN date_ranges d
    WHERE o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
      AND o.paid = 1 AND o.date >= d.l7d_prev_start AND o.date < d.ieri_start
    GROUP BY tm.business_id
),
tov_per_merchant AS (
    SELECT tm.business_id,
        SUM(CASE WHEN o.date >= d.ieri_start AND o.date < d.ieri_end THEN o.grand_total ELSE 0 END)/100.0 AS tov_ieri,
        SUM(CASE WHEN o.date >= d.l7d_prev_start AND o.date < d.ieri_start THEN o.grand_total ELSE 0 END)/100.0/6.0 AS tov_avg_prev6d
    FROM top_merchants tm
    JOIN qromo.orders o ON o.business_id = tm.business_id
    CROSS JOIN date_ranges d
    WHERE o.status NOT IN ('canceled','unconfirmed','failed','deleted','merged')
      AND o.paid = 1 AND o.date >= d.l7d_prev_start AND o.date < d.ieri_end
    GROUP BY tm.business_id
)
SELECT
    t.business_id, b.name AS merchant_name,
    t.tov_ieri, t.tov_avg_prev6d,
    ROUND(100.0 * t.tov_ieri / NULLIF(t.tov_avg_prev6d, 0), 1) AS pct_vs_avg
FROM tov_per_merchant t
JOIN continuity c ON c.business_id = t.business_id
LEFT JOIN qromo.businesses b ON b.business_id = t.business_id
WHERE c.active_days_prev6d >= 5
  AND t.tov_avg_prev6d > 100
  AND t.tov_ieri < t.tov_avg_prev6d * 0.5
ORDER BY t.tov_avg_prev6d - t.tov_ieri DESC
LIMIT 10
"""


# ---------- Message builder ----------
def build_message():
    pulse = run_query(SQL_PULSE)[0]
    nuovi = run_query(SQL_NUOVI_MERCHANT)
    top3 = run_query(SQL_TOP3)
    drop = run_query(SQL_DROP)

    # Data "ieri" (l'ora attuale in UTC potrebbe variare; usiamo data locale Europe/Rome)
    from datetime import timedelta, timezone
    rome_now = datetime.now(timezone.utc) + timedelta(hours=2)  # CEST, basta per il display
    ieri = rome_now.date() - timedelta(days=1)
    label_giorno = f"{GIORNI_IT[ieri.weekday()]} {ieri.strftime('%d/%m')}"

    # Pulse deltas
    tov_ieri = pulse["tov_ieri"] or 0
    tov_wow = pulse["tov_wow"] or 0
    ttv_ieri = pulse["ttv_ieri"] or 0
    ttv_wow = pulse["ttv_wow"] or 0
    delta_tov = ((tov_ieri - tov_wow) / tov_wow * 100) if tov_wow else None
    delta_ttv = ((ttv_ieri - ttv_wow) / ttv_wow * 100) if ttv_wow else None
    stripe_pct = (ttv_ieri / tov_ieri * 100) if tov_ieri else 0
    orders_ieri = pulse["orders_ieri"] or 0
    merchant_ieri = pulse["merchant_ieri"] or 0
    merchant_avg = pulse["merchant_l7d_avg"] or 0

    lines = []
    lines.append(f"☀️ <b>QROMO DAILY</b> — {label_giorno}")
    lines.append("")
    lines.append("💰 <b>PULSE</b>")
    lines.append(f"TOV  {fmt_eur(tov_ieri)}  {fmt_pct(delta_tov)} WoW")
    lines.append(f"TTV  {fmt_eur(ttv_ieri)}  {fmt_pct(delta_ttv)} WoW")
    lines.append(f"Stripe%  {stripe_pct:.1f}%")
    lines.append(f"Ordini {orders_ieri:,}".replace(",", ".") +
                 f"  ·  Merchant {merchant_ieri} (avg7d {merchant_avg:.0f})")
    lines.append("")

    # Nuovi merchant
    lines.append("🆕 <b>NUOVI MERCHANT</b>")
    if not nuovi:
        lines.append("Nessuno")
    else:
        for r in nuovi[:10]:
            days = r["days_reg_to_first"]
            if days is None:
                emoji = "•"
            elif days <= 30:
                emoji = "🟢"
            elif days <= 180:
                emoji = "🟡"
            else:
                emoji = "🔵"
            name = (r["merchant_name"] or f"ID {r['business_id']}")[:25]
            lines.append(f"{emoji} {name}  {fmt_eur(r['tov_eur'])}")
        if len(nuovi) > 10:
            lines.append(f"<i>… e altri {len(nuovi) - 10}</i>")
    lines.append("")

    # Top 3
    lines.append("🏆 <b>TOP 3</b>")
    for i, r in enumerate(top3, 1):
        name = (r["merchant_name"] or f"ID {r['business_id']}")[:25]
        lines.append(f"{i}. {name}  {fmt_eur(r['tov_eur'])}")
    lines.append("")

    # Drop anomalo
    lines.append("⚠️ <b>DROP ANOMALO</b>")
    if not drop:
        lines.append("Nessun drop rilevante")
    else:
        for r in drop:
            name = (r["merchant_name"] or f"ID {r['business_id']}")[:25]
            pct = r["pct_vs_avg"] or 0
            emoji = "🔴" if pct < 20 else "🟠"
            lines.append(
                f"{emoji} {name}  {fmt_eur(r['tov_ieri'])} "
                f"(avg {fmt_eur(r['tov_avg_prev6d'])})"
            )

    return "\n".join(lines)


def main():
    try:
        msg = build_message()
        send_telegram(msg)
        print("✓ Messaggio inviato")
    except Exception as e:
        try:
            send_telegram(f"❌ <b>Qromo Daily — errore</b>\n<code>{type(e).__name__}: {e}</code>")
        except Exception:
            pass
        print(f"✗ Errore: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
