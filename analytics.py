"""
Чистые функции агрегации над данными Wipon. Не зависят от Streamlit,
поэтому их легко тестировать и позже переиспользовать в FastAPI.
"""

from __future__ import annotations

import pandas as pd


def to_float(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def build_cash_lookup(payment_methods: list) -> dict:
    """
    id способа оплаты -> {"is_cash": bool, "name": str}.
    По доке type == 0 -> Наличные, остальное -> безнал.
    """
    lookup = {}
    for m in payment_methods:
        mid = m.get("id")
        lookup[mid] = {"is_cash": m.get("type") == 0, "name": m.get("name") or f"method {mid}"}
    return lookup


def iter_sale_payments(sale: dict):
    """
    Достаёт (payment_method_id, amount) из продажи максимально терпимо к формату,
    т.к. точная структура payments в доке скрыта комментарием.
    """
    for p in sale.get("payments") or []:
        if not isinstance(p, dict):
            continue
        mid = p.get("payment_method_id")
        if mid is None:
            mid = (p.get("payment_method") or {}).get("id")
        amount = p.get("amount", p.get("sum"))
        yield mid, to_float(amount)


def summarize_sales(sales: list, cash_lookup: dict) -> dict:
    """Итоги за период: общая выручка + разбивка нал/безнал."""
    total = cash = cashless = unknown = 0.0
    for s in sales:
        pays = list(iter_sale_payments(s))
        if not pays:
            # не смогли разобрать оплату — кладём весь sum в unknown, чтобы не терять деньги
            unknown += to_float(s.get("sum"))
            continue
        for mid, amount in pays:
            info = cash_lookup.get(mid)
            if info is None:
                unknown += amount
            elif info["is_cash"]:
                cash += amount
            else:
                cashless += amount
    total = cash + cashless + unknown
    return {
        "total": total,
        "cash": cash,
        "cashless": cashless,
        "unknown": unknown,
        "count": len(sales),
    }


def sales_daily(sales: list) -> pd.DataFrame:
    """Выручка по дням (для линейного графика в деньгах)."""
    rows = []
    for s in sales:
        created = s.get("created_at")
        rows.append({"date": created, "sum": to_float(s.get("sum"))})
    if not rows:
        return pd.DataFrame(columns=["date", "sum"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df = df.dropna(subset=["date"])
    return df.groupby("date", as_index=False)["sum"].sum().sort_values("date")


def stock_table(items: list) -> tuple[pd.DataFrame, float]:
    """
    Таблица остатков по наименованиям + общая сумма остатков по цене прихода.
    balance берём из arrival_balance (фолбэк на quantity),
    цена прихода — previous_purchase_price.
    """
    rows = []
    for it in items:
        balance = it.get("arrival_balance", it.get("quantity"))
        balance = to_float(balance)
        purchase = to_float(it.get("previous_purchase_price"))
        rows.append({
            "Наименование": it.get("title"),
            "Остаток": balance,
            "Цена прихода": purchase,
            "Сумма по приходу": round(balance * purchase, 2),
        })
    df = pd.DataFrame(rows, columns=["Наименование", "Остаток", "Цена прихода", "Сумма по приходу"])
    total_cost = float(df["Сумма по приходу"].sum()) if not df.empty else 0.0
    return df, total_cost


def refunds_total(refunds: list) -> float:
    return sum(to_float(r.get("amount")) for r in refunds)


# ======================================================================
# Расширения для демо: продажи в штуках, топ товаров, обеспеченность
# ======================================================================

def _item_name(it: dict) -> str:
    for key in ("item_name", "title", "name"):
        v = it.get(key)
        if v:
            return v
    obj = it.get("item")
    if isinstance(obj, dict):
        return obj.get("title") or obj.get("name") or f"item {it.get('item_id')}"
    return f"item {it.get('item_id')}"


def iter_sale_items(sale: dict):
    """
    Позиции чека из продажи. Точная структура item_sale в доке скрыта,
    поэтому берём поля терпимо (quantity/count, selling_price/price, sum/total).
    """
    entries = sale.get("item_sale") or sale.get("items") or []
    for it in entries:
        if not isinstance(it, dict):
            continue
        qty = to_float(it.get("quantity", it.get("count")))
        price = to_float(it.get("selling_price", it.get("price")))
        total = it.get("sum", it.get("total", it.get("amount")))
        total = to_float(total) if total is not None else qty * price
        yield {
            "item_id": it.get("item_id") or (it.get("item") or {}).get("id"),
            "name": _item_name(it),
            "qty": qty,
            "price": price,
            "total": total,
        }


def total_units(sales: list) -> float:
    return sum(x["qty"] for s in sales for x in iter_sale_items(s))


def units_daily(sales: list) -> pd.DataFrame:
    """Продажи в штуках по дням (линейный график в шт)."""
    rows = []
    for s in sales:
        units = sum(x["qty"] for x in iter_sale_items(s))
        rows.append({"date": s.get("created_at"), "units": units})
    if not rows:
        return pd.DataFrame(columns=["date", "units"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df = df.dropna(subset=["date"])
    return df.groupby("date", as_index=False)["units"].sum().sort_values("date")


def _id_to_title(items: list) -> dict:
    out = {}
    for it in items:
        out[it.get("id")] = it.get("title")
    return out


def top_products(sales: list, items: list | None = None) -> pd.DataFrame:
    """Список реализованных товаров: сколько штук и на какую сумму."""
    id2title = _id_to_title(items or [])
    agg: dict = {}
    for s in sales:
        for x in iter_sale_items(s):
            key = x["item_id"] if x["item_id"] is not None else x["name"]
            a = agg.setdefault(key, {"name": id2title.get(x["item_id"]) or x["name"],
                                     "qty": 0.0, "revenue": 0.0})
            a["qty"] += x["qty"]
            a["revenue"] += x["total"]
    rows = [{"Товар": v["name"], "Продано, шт": round(v["qty"], 3),
             "Выручка": round(v["revenue"], 2)} for v in agg.values()]
    df = pd.DataFrame(rows, columns=["Товар", "Продано, шт", "Выручка"])
    if not df.empty:
        df = df.sort_values("Выручка", ascending=False).reset_index(drop=True)
    return df


def _sold_qty_by_id(sales: list) -> dict:
    agg: dict = {}
    for s in sales:
        for x in iter_sale_items(s):
            key = x["item_id"] if x["item_id"] is not None else x["name"]
            agg[key] = agg.get(key, 0.0) + x["qty"]
    return agg


def stock_coverage(sales_30: list, sales_14: list, items: list,
                   low_cover_days: float = 14.0) -> pd.DataFrame:
    """
    Обеспеченность запасами: продано за 14 и 30 дней против текущего остатка,
    средние продажи в день и на сколько дней хватит остатка.
    """
    sold30 = _sold_qty_by_id(sales_30)
    sold14 = _sold_qty_by_id(sales_14)
    rows = []
    for it in items:
        iid = it.get("id")
        stock = to_float(it.get("arrival_balance", it.get("quantity")))
        s30 = sold30.get(iid, 0.0)
        s14 = sold14.get(iid, 0.0)
        avg_day = s30 / 30.0
        cover_days = round(stock / avg_day, 1) if avg_day > 0 else None
        # флаг: заканчивается (есть продажи, но остатка мало) либо уже ноль
        low = (avg_day > 0 and cover_days is not None and cover_days < low_cover_days) \
            or (stock <= 0 and s30 > 0)
        rows.append({
            "Товар": it.get("title"),
            "Остаток": round(stock, 3),
            "Продано 14 дн": round(s14, 3),
            "Продано 30 дн": round(s30, 3),
            "Хватит на, дн": cover_days,
            "Заканчивается": "⚠️" if low else "",
        })
    df = pd.DataFrame(rows, columns=["Товар", "Остаток", "Продано 14 дн",
                                     "Продано 30 дн", "Хватит на, дн", "Заканчивается"])
    if not df.empty:
        # сначала то, что заканчивается, потом по продажам за 30 дней
        df = df.sort_values(["Заканчивается", "Продано 30 дн"],
                            ascending=[False, False]).reset_index(drop=True)
    return df
