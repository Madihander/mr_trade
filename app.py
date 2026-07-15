"""
MVP-дашборд по Блоку 1 (данные из Wipon).
Запуск локально:  streamlit run app.py

Конфигурация (токен/employee_id) берётся из st.secrets, чтобы задеплоенное
приложение было уже подключено и зритель ничего не вводил. Локально можно
ввести данные вручную в сайдбаре (если секреты не заданы).
"""

from __future__ import annotations

import datetime as dt

import plotly.graph_objects as go
import streamlit as st

import analytics as A
from wipon_client import WiponClient

st.set_page_config(page_title="Wipon — MR-TRADE", layout="wide")


def money(x: float) -> str:
    return f"{x:,.0f} ₸".replace(",", " ")


def secret(key: str, default=None):
    """Безопасный доступ к st.secrets: не падает, если секретов нет."""
    try:
        return st.secrets[key]
    except Exception:  # noqa: BLE001
        return default


def secrets_keys():
    """Список ключей из secrets.toml, либо None если файл не найден/не читается."""
    try:
        return list(st.secrets.keys())
    except Exception:  # noqa: BLE001
        return None


def filter_recent(sales: list, days: int, ref: dt.date) -> list:
    since = ref - dt.timedelta(days=days)
    out = []
    for s in sales:
        created = str(s.get("created_at", ""))[:10]
        try:
            d = dt.date.fromisoformat(created)
        except ValueError:
            continue
        if since <= d <= ref:
            out.append(s)
    return out


# ---------- конфиг: сначала секреты, потом ручной ввод ----------
cfg_base = secret("base_url", "https://api.wipon.kz")
cfg_token = str(secret("token", "") or "")
cfg_emp = str(secret("employee_id", "") or "")
configured = bool(cfg_token.strip())

today = dt.date.today()
default_from = today - dt.timedelta(days=30)

with st.sidebar:
    st.header("Wipon — MR-TRADE")

    if configured:
        base_url, token, emp_override = cfg_base, cfg_token, cfg_emp
        st.caption("✅ Подключение настроено администратором.")
    else:
        keys = secrets_keys()
        if keys is None:
            st.caption("⚠️ Файл .streamlit/secrets.toml не найден или не читается "
                       "(проверьте путь и синтаксис TOML).")
        elif "token" not in keys:
            st.caption(f"⚠️ secrets.toml прочитан, но нет ключа `token`. "
                       f"Найденные ключи: {keys}")
        else:
            st.caption("⚠️ Ключ `token` пустой в secrets.toml.")
        st.caption("Ниже — ручной ввод (режим разработки).")
        base_url = st.text_input("Базовый URL", value=cfg_base)
        token = st.text_area("Bearer-токен", value="", height=120)
        emp_override = st.text_input("employee_id (необязательно)", value=cfg_emp)

    # срок годности токена
    if token.strip():
        info = WiponClient(base_url=base_url, token=token).token_info()
        if info.get("expires_at"):
            days = info.get("days_left")
            if info.get("expired"):
                st.error("Токен истёк — обновите его в секретах.")
            elif days is not None and days < 14:
                st.warning(f"Токен истекает через {days} дн.")
            else:
                st.caption(f"Токен действует до {info['expires_at']:%Y-%m-%d}")

    st.divider()
    date_range = st.date_input("Период", value=(default_from, today), max_value=today)
    reload_clicked = st.button("Обновить данные", type="primary", use_container_width=True)

if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
    date_from, date_to = date_range
else:
    date_from = date_to = date_range


st.title("Дашборд Wipon — MR-TRADE")

if not token.strip():
    st.info("Подключение не настроено. Задайте token в секретах приложения "
            "(или введите вручную слева в режиме разработки).")
    st.stop()

# автозагрузка при первом открытии, если приложение уже настроено
auto_first = configured and "data" not in st.session_state and not st.session_state.get("_tried")
should_load = reload_clicked or auto_first
if should_load:
    st.session_state["_tried"] = True
    try:
        client = WiponClient(base_url=base_url, token=token)
        with st.spinner("Получаем данные из Wipon…"):
            employee_id = int(emp_override) if str(emp_override).strip() else client.resolve_employee_id()
            methods = client.get_payment_methods(employee_id)
            sales = client.get_sales(employee_id, date_from=str(date_from), date_to=str(date_to))
            refunds = client.get_refunds(employee_id, date_from=str(date_from), date_to=str(date_to))
            items = client.get_items(employee_id)
            cov_from = today - dt.timedelta(days=30)
            sales_30 = client.get_sales(employee_id, date_from=str(cov_from), date_to=str(today))
        st.session_state["data"] = {
            "employee_id": employee_id, "methods": methods, "sales": sales,
            "refunds": refunds, "items": items, "sales_30": sales_30,
        }
    except Exception as e:  # noqa: BLE001
        st.error(f"Ошибка при загрузке: {e}")

data = st.session_state.get("data")
if not data:
    st.info("Нажмите «Обновить данные».")
    st.stop()

# ---------- агрегации ----------
cash_lookup = A.build_cash_lookup(data["methods"])
summary = A.summarize_sales(data["sales"], cash_lookup)
refund_sum = A.refunds_total(data["refunds"])
units = A.total_units(data["sales"])
avg_check = summary["total"] / summary["count"] if summary["count"] else 0.0

# ---------- верхние KPI ----------
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Выручка", money(summary["total"]))
k2.metric("Наличные", money(summary["cash"]))
k3.metric("Безнал", money(summary["cashless"]))
k4.metric("Продано, шт", f"{units:,.0f}".replace(",", " "))
k5.metric("Средний чек", money(avg_check))
k6.metric("Чеков", f"{summary['count']}")

if summary["unknown"] > 0:
    st.warning(
        f"Не отнесено к нал/безнал: {money(summary['unknown'])}. "
        "Вероятно, иная структура поля payments — см. вкладку «Отладка»."
    )

tab_over, tab_items, tab_cover, tab_stock, tab_debug = st.tabs(
    ["📈 Обзор", "🏆 Товары", "📦 Обеспеченность запасами", "🗃 Остатки", "🔧 Отладка"]
)

with tab_over:
    c_left, c_right = st.columns([2, 1])
    with c_left:
        st.caption("Продажи в деньгах по дням")
        daily = A.sales_daily(data["sales"])
        if not daily.empty:
            st.line_chart(daily.set_index("date")["sum"], height=240)
        else:
            st.info("Нет продаж за выбранный период.")

        st.caption("Продажи в штуках по дням")
        ud = A.units_daily(data["sales"])
        if not ud.empty and ud["units"].sum() > 0:
            st.line_chart(ud.set_index("date")["units"], height=240)
        else:
            st.info("Нет данных по количеству (см. «Отладка»).")
    with c_right:
        st.caption("Структура оплат")
        labels, values = ["Наличные", "Безнал"], [summary["cash"], summary["cashless"]]
        if summary["unknown"] > 0:
            labels.append("Не определено")
            values.append(summary["unknown"])
        if sum(values) > 0:
            fig = go.Figure(data=[go.Pie(labels=labels, values=values, hole=0.55)])
            fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300,
                              legend=dict(orientation="h", y=-0.1))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Нет оплат за период.")
        st.metric("Возвраты", money(refund_sum))
        st.metric("Чистая выручка", money(summary["total"] - refund_sum))

with tab_items:
    st.caption("Реализованные товары за период")
    top = A.top_products(data["sales"], data["items"])
    if top.empty:
        st.info("Нет позиций (item_sale пуст — см. «Отладка»).")
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.write("**Топ по выручке**")
            st.bar_chart(top.head(10).set_index("Товар")["Выручка"], height=320)
        with c2:
            st.write("**Топ по количеству**")
            st.bar_chart(top.sort_values("Продано, шт", ascending=False).head(10)
                         .set_index("Товар")["Продано, шт"], height=320)
        st.dataframe(top, use_container_width=True, height=340)

with tab_cover:
    st.caption("Продано за 14/30 дней против текущего остатка")
    sales_30 = data.get("sales_30", [])
    sales_14 = filter_recent(sales_30, 14, today)
    cov = A.stock_coverage(sales_30, sales_14, data["items"])
    if cov.empty:
        st.info("Недостаточно данных для расчёта.")
    else:
        low_n = int((cov["Заканчивается"] == "⚠️").sum())
        cc1, cc2 = st.columns(2)
        cc1.metric("Товаров заканчивается", low_n)
        cc2.metric("Позиций всего", len(cov))
        only_low = st.checkbox("Показать только заканчивающиеся", value=False)
        view = cov[cov["Заканчивается"] == "⚠️"] if only_low else cov
        st.dataframe(view, use_container_width=True, height=420)
        st.caption("«Хватит на, дн» = остаток ÷ средние продажи в день за 30 дней.")

with tab_stock:
    stock_df, total_cost = A.stock_table(data["items"])
    st.metric("Сумма остатков по цене прихода", money(total_cost))
    st.dataframe(stock_df, use_container_width=True, height=440)

with tab_debug:
    st.write("**Способы оплаты (payment-method):**")
    st.json(data["methods"][:5])
    st.write("**Первая продажа целиком** — сверить payments / item_sale:")
    st.json(data["sales"][0] if data["sales"] else {})
    st.write("**Первый товар** — сверить previous_purchase_price / arrival_balance:")
    st.json(data["items"][0] if data["items"] else {})
