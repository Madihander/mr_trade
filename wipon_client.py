"""
Клиент к API Wipon/Prosklad.

Поток использования:
    c = WiponClient()
    c.login("77XXXXXXXXX", "пароль")
    emp_id = c.resolve_employee_id()
    sales = c.get_sales(emp_id, date_from="2025-01-01", date_to="2025-12-31")
    items = c.get_items(emp_id)
    methods = c.get_payment_methods(emp_id)

Пагинация спрятана внутри _get_paginated: листаем по meta.last_page / links.next.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json

import requests


class WiponError(RuntimeError):
    pass


class WiponClient:
    def __init__(self, base_url: str = "https://api.wipon.kz", timeout: int = 30,
                 token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token: str | None = token
        self.company_id: int | None = None
        self._session = requests.Session()

    def set_token(self, token: str) -> None:
        """Использовать уже готовый Bearer-токен, без логина."""
        self.token = token.strip()

    def token_info(self) -> dict:
        """
        Читает срок годности из JWT (claim exp) без проверки подписи —
        только чтобы показать пользователю, когда токен истекает.
        """
        if not self.token:
            return {"ok": False, "reason": "нет токена"}
        try:
            payload_b64 = self.token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)  # добить padding
            claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        except (IndexError, ValueError, binascii.Error, json.JSONDecodeError):
            return {"ok": False, "reason": "не удалось разобрать токен"}
        exp = claims.get("exp")
        if not exp:
            return {"ok": True, "expires_at": None, "expired": False}
        expires_at = dt.datetime.fromtimestamp(exp)
        return {
            "ok": True,
            "expires_at": expires_at,
            "expired": expires_at < dt.datetime.now(),
            "days_left": (expires_at - dt.datetime.now()).days,
        }

    # ---------- авторизация ----------
    def login(self, username: str, password: str) -> dict:
        url = f"{self.base_url}/v1/oauth/token"
        r = self._session.post(
            url,
            json={"username": username, "password": password},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=self.timeout,
        )
        # понятная ошибка вместо голого 4xx
        if r.status_code >= 400:
            raise WiponError(f"Авторизация не удалась ({r.status_code}): {r.text[:300]}")
        data = r.json()
        self.token = data.get("access_token")
        self.company_id = data.get("company_id")
        if not self.token:
            raise WiponError(f"В ответе нет access_token: {data}")
        return data

    # ---------- низкоуровневые запросы ----------
    def _headers(self) -> dict:
        if not self.token:
            raise WiponError("Сначала вызови login().")
        return {"Accept": "application/json", "Authorization": f"Bearer {self.token}"}

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        r = self._session.get(url, headers=self._headers(), params=params or {}, timeout=self.timeout)
        if r.status_code >= 400:
            raise WiponError(f"GET {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def _get_paginated(self, path: str, params: dict | None = None,
                       per_page: int = 100, max_pages: int = 500) -> list:
        """Собирает data[] со всех страниц. Если data не список — возвращает как есть."""
        params = dict(params or {})
        params["per_page"] = per_page
        out: list = []
        page = 1
        while page <= max_pages:
            params["page"] = page
            payload = self._get(path, params)
            chunk = payload.get("data", [])
            if not isinstance(chunk, list):
                return chunk  # напр. /v1/employee отдаёт объект, а не список
            out.extend(chunk)

            meta = payload.get("meta") or {}
            last_page = meta.get("last_page")
            current = meta.get("current_page", page)
            if last_page is not None:
                if current >= last_page:
                    break
            else:
                # нет meta — ориентируемся на links.next и на непустой ответ
                links = payload.get("links") or {}
                if not links.get("next") or not chunk:
                    break
            page += 1
        return out

    # ---------- сущности ----------
    def get_company_and_employees(self) -> tuple[dict, list]:
        payload = self._get("/v1/employee")
        data = payload.get("data", {})
        return data.get("company", {}) or {}, data.get("employees", []) or []

    def resolve_employee_id(self, prefer_owner: bool = True) -> int:
        _, employees = self.get_company_and_employees()
        if not employees:
            raise WiponError("Список сотрудников пуст — нечего использовать как employee_id.")
        if prefer_owner:
            for e in employees:
                if e.get("is_owner"):
                    return e["id"]
        return employees[0]["id"]

    def get_payment_methods(self, employee_id: int) -> list:
        return self._get(f"/v1/employee/{employee_id}/payment-method").get("data", []) or []

    def get_sales(self, employee_id: int, date_from: str | None = None,
                  date_to: str | None = None, per_page: int = 100, extra: dict | None = None) -> list:
        params: dict = {}
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        if extra:
            params.update(extra)
        return self._get_paginated(f"/v1/employee/{employee_id}/sale", params, per_page=per_page)

    def get_items(self, employee_id: int, per_page: int = 100, extra: dict | None = None) -> list:
        """Товары/остатки. Здесь есть previous_purchase_price (цена прихода) и остаток."""
        return self._get_paginated(f"/v2/employee/{employee_id}/item", dict(extra or {}), per_page=per_page)

    def get_refunds(self, employee_id: int, date_from: str | None = None,
                    date_to: str | None = None, per_page: int = 100) -> list:
        params: dict = {}
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        return self._get_paginated(f"/v1/employee/{employee_id}/refund", params, per_page=per_page)
