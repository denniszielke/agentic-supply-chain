"""Preflight checks for the simulated SAP Joule agent - a clear green/red before the demo.

Verifies the prerequisites needed to register the externally-hosted Joule A2A agent
in Azure AI Foundry with an agent identity blueprint:

  * Foundry project endpoint + auth (Entra) are usable.
  * The Joule A2A endpoint is reachable and serves its Agent Card + /health.
  * The agent identity blueprint id (JOULE_BLUEPRINT_ID) is set (and, best-effort,
    resolvable in Microsoft Entra via Graph).
  * A RemoteA2A project connection exists (when JOULE_A2A_CONNECTION_NAME/ID is set).
  * (Opt-in) the A2A preview is accepted - probed by creating then deleting a
    throwaway agent version.

Usage::

    python -m scripts.preflight_joule_agent            # read-only checks
    python -m scripts.preflight_joule_agent --probe     # also probe A2A preview
                                                        # (creates + deletes a
                                                        #  throwaway agent version)

Exit code is 0 when there are no failures (warnings are allowed), 1 otherwise - so
it can gate a deployment step in CI or a demo runbook.

Reads the same environment variables as ``scripts.register_joule_agent`` and
``scripts.deploy_joule_agent`` (see those modules / the README).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

from scripts.register_joule_agent import (
    AGENT_NAME,
    CARD_PATH,
    MODEL,
    _PREVIEW_HEADERS,
    _build_definition,
    _resolve_base_url,
    _resolve_connection_id,
)

OK, FAIL, WARN, SKIP = "OK", "FAIL", "WARN", "SKIP"
_GLYPH = {OK: "[ OK ]", FAIL: "[FAIL]", WARN: "[WARN]", SKIP: "[SKIP]"}


class Report:
    """Collects check results and prints an aligned green/red summary."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, level: str, title: str, detail: str = "") -> None:
        self.rows.append((level, title, detail))

    @property
    def failures(self) -> int:
        return sum(1 for level, _, _ in self.rows if level == FAIL)

    @property
    def warnings(self) -> int:
        return sum(1 for level, _, _ in self.rows if level == WARN)

    def render(self) -> int:
        print("\nJoule agent - preflight checks")
        print("=" * 32)
        for level, title, detail in self.rows:
            line = f"{_GLYPH[level]} {title}"
            if detail:
                line += f" - {detail}"
            print(line)
        print("-" * 32)
        verdict = "READY" if self.failures == 0 else "NOT READY"
        print(
            f"Result: {self.failures} failure(s), {self.warnings} warning(s) - {verdict}"
        )
        return 0 if self.failures == 0 else 1


def _http_get_json(url: str, timeout: int = 10) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted URL)
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _as_dict(obj) -> dict:
    fn = getattr(obj, "as_dict", None)
    if callable(fn):
        try:
            return fn()
        except Exception:  # pragma: no cover - defensive
            pass
    try:
        return dict(obj)
    except Exception:  # pragma: no cover - defensive
        return {}


# --------------------------------------------------------------------------- checks


def check_project_and_auth(report: Report):
    """Foundry endpoint set + Entra auth usable. Returns a client or None."""
    endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "").strip()
    if not endpoint:
        report.add(FAIL, "Foundry project endpoint", "set AZURE_AI_PROJECT_ENDPOINT")
        return None
    report.add(OK, "Foundry project endpoint", endpoint)

    try:
        from scripts.register_joule_agent import get_client  # imported lazily

        client = get_client()
        # A cheap call that exercises auth + project access.
        list(client.connections.list())  # may be empty; just must not raise
        report.add(OK, "Foundry auth + project access", "credential accepted")
        return client
    except Exception as exc:  # pragma: no cover - environment dependent
        report.add(FAIL, "Foundry auth + project access", str(exc).splitlines()[0][:160])
        return None


def check_endpoint_reachable(report: Report) -> str:
    base_url = _resolve_base_url()
    if not base_url:
        report.add(
            WARN,
            "Joule A2A endpoint",
            "URL not resolved - set JOULE_AGENT_URL or AZURE_RESOURCE_GROUP",
        )
        return ""

    card_url = f"{base_url.rstrip('/')}{CARD_PATH}"
    try:
        status, card = _http_get_json(card_url)
        if status == 200 and isinstance(card, dict) and card.get("name"):
            skills = card.get("skills") or []
            report.add(
                OK,
                "Joule Agent Card",
                f"{card.get('name')} ({len(skills)} skill(s)) @ {card_url}",
            )
        else:
            report.add(FAIL, "Joule Agent Card", f"unexpected response from {card_url}")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        report.add(FAIL, "Joule Agent Card", f"{card_url}: {exc}")

    health_url = f"{base_url.rstrip('/')}/health"
    try:
        status, body = _http_get_json(health_url)
        ok = status == 200 and isinstance(body, dict) and body.get("status") == "ok"
        report.add(OK if ok else WARN, "Joule /health", health_url if ok else f"{status} @ {health_url}")
    except Exception as exc:  # health is a nicety, not a hard gate
        report.add(WARN, "Joule /health", f"{health_url}: {str(exc)[:80]}")
    return base_url


def check_blueprint(report: Report) -> None:
    blueprint_id = os.getenv("JOULE_BLUEPRINT_ID", "").strip()
    if not blueprint_id:
        report.add(
            FAIL,
            "Agent identity blueprint",
            "set JOULE_BLUEPRINT_ID (create via Entra ID -> Agents -> Agent blueprints)",
        )
        return
    report.add(OK, "Agent identity blueprint id set", blueprint_id)

    # Best-effort validation against Microsoft Graph (needs directory read perms).
    try:
        from azure.identity import DefaultAzureCredential

        token = DefaultAzureCredential().get_token("https://graph.microsoft.com/.default").token
        from urllib.parse import quote, urlencode

        query = urlencode(
            {"$filter": f"appId eq '{blueprint_id}'", "$select": "id,appId,displayName"},
            quote_via=quote,
        )
        url = f"https://graph.microsoft.com/v1.0/applications?{query}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        apps = data.get("value", [])
        if apps:
            report.add(OK, "Blueprint resolvable in Entra", apps[0].get("displayName", blueprint_id))
        else:
            report.add(
                WARN,
                "Blueprint resolvable in Entra",
                "no application found with that appId - double-check JOULE_BLUEPRINT_ID",
            )
    except Exception as exc:  # permissions vary; don't hard-fail
        report.add(WARN, "Blueprint Entra check skipped", str(exc).splitlines()[0][:120])


def check_connection(report: Report, client) -> None:
    name = os.getenv("JOULE_A2A_CONNECTION_NAME", "").strip()
    conn_id = os.getenv("JOULE_CONNECTION_ID", "").strip()
    if not name and not conn_id:
        report.add(
            WARN,
            "RemoteA2A connection",
            "none set - recommended (JOULE_A2A_CONNECTION_NAME) to carry endpoint + auth",
        )
        return
    if conn_id and not name:
        report.add(OK, "RemoteA2A connection id set", conn_id)
        return
    if client is None:
        report.add(WARN, "RemoteA2A connection", f"'{name}' not verified (no Foundry client)")
        return
    try:
        conn = client.connections.get(name)
        d = _as_dict(conn)
        category = d.get("category") or d.get("type") or getattr(conn, "type", "")
        target = d.get("target") or getattr(conn, "target", "")
        detail = f"'{name}' found"
        if category:
            detail += f" (category {category})"
        if target:
            detail += f" -> {target}"
        level = OK if str(category).lower() == "remotea2a" or not category else WARN
        if level == WARN:
            detail += " - expected category RemoteA2A"
        report.add(level, "RemoteA2A connection", detail)
    except Exception as exc:
        report.add(FAIL, "RemoteA2A connection", f"'{name}' not found: {str(exc).splitlines()[0][:120]}")


def probe_preview(report: Report, client, base_url: str) -> None:
    """Create then delete a throwaway agent version to confirm the A2A preview works."""
    if client is None:
        report.add(SKIP, "A2A preview probe", "no Foundry client")
        return
    conn_id = ""
    try:
        conn_id = _resolve_connection_id(client)
    except Exception:
        conn_id = ""
    probe_name = f"joule-preflight-{int(time.time())}"
    try:
        definition = _build_definition(base_url, conn_id)
        version = client.agents.create_version(
            agent_name=probe_name,
            definition=definition,
            description="Joule preflight probe (auto-deleted).",
            headers=_PREVIEW_HEADERS,
        )
        report.add(OK, "A2A preview accepted", f"created+deleting throwaway '{probe_name}'")
        try:
            client.agents.delete_version(agent_name=probe_name, agent_version=version.version)
        except Exception as del_exc:  # cleanup best-effort
            report.add(
                WARN,
                "A2A preview cleanup",
                f"could not delete '{probe_name}': {str(del_exc).splitlines()[0][:100]}",
            )
    except Exception as exc:
        report.add(
            FAIL,
            "A2A preview accepted",
            f"{str(exc).splitlines()[0][:160]} (check {list(_PREVIEW_HEADERS.values())})",
        )


def main() -> int:
    do_probe = "--probe" in sys.argv
    report = Report()

    client = check_project_and_auth(report)
    base_url = check_endpoint_reachable(report)
    check_blueprint(report)
    check_connection(report, client)
    if do_probe:
        probe_preview(report, client, base_url)
    else:
        report.add(SKIP, "A2A preview probe", "pass --probe to create+delete a throwaway version")

    return report.render()


if __name__ == "__main__":
    raise SystemExit(main())
