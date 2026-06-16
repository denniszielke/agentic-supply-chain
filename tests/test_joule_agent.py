"""Tests for the simulated SAP Joule A2A supply agent (``src/joule_agent``)."""

from __future__ import annotations

import json
import unittest
import uuid

from src.joule_agent.server import (
    answer,
    assess_fulfilment,
    build_agent_card,
    build_app,
    _match_product,
    _products,
)


class SupplyLogicTests(unittest.TestCase):
    def test_match_by_sku_and_name(self):
        by_sku = _match_product("AS-FW-002")
        self.assertIsNotNone(by_sku)
        self.assertEqual(by_sku["sku"], "AS-FW-002")
        by_name = _match_product("rinderhack")
        self.assertIsNotNone(by_name)
        self.assertEqual(by_name["sku"], "AS-FW-002")
        self.assertIsNone(_match_product("does-not-exist-xyz"))

    def test_assess_fulfilment_can_when_volume_small(self):
        product = _match_product("AS-FW-002")
        a = assess_fulfilment(product, required_weekly_units=1)
        self.assertTrue(a["can_fulfil"])
        self.assertEqual(a["projected_shortfall_units"], 0)
        self.assertEqual(a["recommendation"], "fulfillable")

    def test_assess_fulfilment_cannot_when_volume_huge(self):
        product = _match_product("AS-FW-002")
        a = assess_fulfilment(product, required_weekly_units=10_000_000)
        self.assertFalse(a["can_fulfil"])
        self.assertGreater(a["projected_shortfall_units"], 0)
        self.assertEqual(a["recommendation"], "expedite_or_reduce_promo")

    def test_assess_without_required_volume_has_no_verdict(self):
        product = _match_product("AS-ME-001")
        a = assess_fulfilment(product, required_weekly_units=None)
        self.assertNotIn("can_fulfil", a)
        self.assertIn("available_to_promise_units", a)
        self.assertIn("supplier", a)

    def test_available_to_promise_excludes_safety_stock(self):
        product = _match_product("AS-ME-001")
        a = assess_fulfilment(product, required_weekly_units=None)
        self.assertEqual(
            a["available_to_promise_units"],
            max(0, product["stock_on_hand_units"] - product["safety_stock_units"]),
        )


class AnswerTextTests(unittest.TestCase):
    def test_answer_lists_catalog(self):
        text = answer("list catalog")
        self.assertIn("Supply catalog", text)
        self.assertIn("AS-FW-002", text)

    def test_answer_fulfilment_includes_structured_json(self):
        text = answer("Can we fulfil 5000 units of AS-FW-002 next week?")
        self.assertIn("AS-FW-002", text)
        self.assertIn("structured:", text)
        payload = json.loads(text.splitlines()[-1])
        self.assertEqual(payload["sku"], "AS-FW-002")
        self.assertIn("can_fulfil", payload)

    def test_answer_unknown_product(self):
        text = answer("supply for unobtanium 9000")
        self.assertIn("could not resolve", text.lower())

    def test_answer_empty_is_greeting(self):
        text = answer("")
        self.assertIn("SAP Joule", text)


class AgentCardTests(unittest.TestCase):
    def test_card_fields(self):
        card = build_agent_card()
        self.assertEqual(card.name, "SAP Joule Supply Agent")
        self.assertTrue(card.url.endswith("/"))
        self.assertEqual({s.id for s in card.skills}, {"fulfilment-check", "stock-lookup"})


class A2AEndpointTests(unittest.TestCase):
    """Drive the real A2A app through Starlette's TestClient."""

    @classmethod
    def setUpClass(cls):
        try:
            from starlette.testclient import TestClient
        except Exception as exc:  # pragma: no cover - env without test deps
            raise unittest.SkipTest(f"starlette TestClient unavailable: {exc}")
        cls.client = TestClient(build_app())

    def test_agent_card_served(self):
        resp = self.client.get("/.well-known/agent-card.json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["name"], "SAP Joule Supply Agent")

    def test_health(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_message_send_returns_supply_answer(self):
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "messageId": str(uuid.uuid4()),
                    "parts": [
                        {"kind": "text", "text": "stock for AS-FW-002"}
                    ],
                }
            },
        }
        resp = self.client.post("/", json=payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("result", body)
        result = body["result"]
        parts = result.get("parts")
        if parts is None:
            parts = (result.get("status", {}).get("message", {}) or {}).get("parts", [])
        text = " ".join(p.get("text", "") for p in parts)
        self.assertIn("AS-FW-002", text)


if __name__ == "__main__":
    unittest.main()
