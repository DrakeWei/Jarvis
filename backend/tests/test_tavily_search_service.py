from __future__ import annotations

import json
import os
import urllib.error
from unittest import TestCase
from unittest.mock import patch

import app.services.tavily_search_service as tavily_search_service


class _DummyHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class TavilySearchServiceTests(TestCase):
    def test_search_web_raises_when_disabled(self) -> None:
        with patch.object(tavily_search_service.settings, "jarvis_tavily_enabled", False):
            with self.assertRaises(tavily_search_service.TavilySearchError):
                tavily_search_service.search_web("today cavaliers knicks final score")

    def test_search_web_parses_results_and_assigns_evidence_quality(self) -> None:
        payload = {
            "results": [
                {
                    "title": "Knicks vs Cavaliers Box Score",
                    "url": "https://www.nba.com/game/1",
                    "score": 0.93,
                    "content": "Final: Knicks 108, Cavaliers 101",
                    "raw_content": "Final: Knicks 108, Cavaliers 101 on the official game page.",
                },
                {
                    "title": "Game Recap",
                    "url": "https://www.nba.com/game/recap/1",
                    "score": 0.81,
                    "content": "New York closed out the game late.",
                    "raw_content": "New York closed out the game late and won 108-101.",
                },
            ]
        }
        with patch.object(tavily_search_service.settings, "jarvis_tavily_enabled", True), patch.object(
            tavily_search_service.settings, "jarvis_tavily_api_key", "tvly-test"
        ), patch.object(
            tavily_search_service.settings, "jarvis_tavily_timeout_ms", 10000
        ), patch.object(
            tavily_search_service.settings, "jarvis_tavily_max_results_default", 5
        ), patch(
            "app.services.tavily_search_service.urllib.request.urlopen",
            return_value=_DummyHTTPResponse(payload),
        ):
            response = tavily_search_service.search_web("today cavaliers knicks final score")
        self.assertEqual(response.evidence_quality, "strong")
        self.assertEqual(response.results[0].url, "https://www.nba.com/game/1")
        self.assertEqual(response.include_domains, ["nba.com"])

    def test_search_web_truncates_large_raw_content(self) -> None:
        payload = {
            "results": [
                {
                    "title": "Example",
                    "url": "https://example.com/article",
                    "score": 0.72,
                    "content": "Useful snippet",
                    "raw_content": "x" * 1000,
                }
            ]
        }
        with patch.object(tavily_search_service.settings, "jarvis_tavily_enabled", True), patch.object(
            tavily_search_service.settings, "jarvis_tavily_api_key", "tvly-test"
        ), patch.object(
            tavily_search_service.settings, "jarvis_tavily_timeout_ms", 10000
        ), patch.object(
            tavily_search_service.settings, "jarvis_tavily_max_results_default", 5
        ), patch(
            "app.services.tavily_search_service.urllib.request.urlopen",
            return_value=_DummyHTTPResponse(payload),
        ):
            response = tavily_search_service.search_web("latest example article")
        self.assertLessEqual(len(response.results[0].raw_excerpt), 420)
        self.assertEqual(response.evidence_quality, "medium")

    def test_search_web_retries_without_domains_when_first_result_set_is_empty(self) -> None:
        with patch.object(tavily_search_service.settings, "jarvis_tavily_enabled", True), patch.object(
            tavily_search_service.settings, "jarvis_tavily_api_key", "tvly-test"
        ), patch.object(
            tavily_search_service.settings, "jarvis_tavily_timeout_ms", 10000
        ), patch.object(
            tavily_search_service.settings, "jarvis_tavily_max_results_default", 5
        ), patch(
            "app.services.tavily_search_service.urllib.request.urlopen",
            side_effect=[
                _DummyHTTPResponse({"results": []}),
                _DummyHTTPResponse(
                    {
                        "results": [
                            {
                                "title": "Fallback Result",
                                "url": "https://example.com/fallback",
                                "score": 0.7,
                                "content": "Fallback answer",
                                "raw_content": "Fallback answer",
                            }
                        ]
                    }
                ),
            ],
        ):
            response = tavily_search_service.search_web("today cavaliers knicks final score")
        self.assertTrue(response.retried_without_domains)
        self.assertEqual(response.include_domains, [])

    def test_search_web_surfaces_transport_errors(self) -> None:
        with patch.object(tavily_search_service.settings, "jarvis_tavily_enabled", True), patch.object(
            tavily_search_service.settings, "jarvis_tavily_api_key", "tvly-test"
        ), patch.object(
            tavily_search_service.settings, "jarvis_tavily_timeout_ms", 10000
        ), patch.object(
            tavily_search_service.settings, "jarvis_tavily_max_results_default", 5
        ), patch(
            "app.services.tavily_search_service.urllib.request.urlopen",
            side_effect=urllib.error.URLError("timeout"),
        ):
            with self.assertRaises(tavily_search_service.TavilySearchError):
                tavily_search_service.search_web("latest weather in san francisco")

    def test_search_web_normalizes_time_range_alias_before_request(self) -> None:
        captured_body: dict[str, object] = {}

        def fake_urlopen(request, timeout=None, context=None):
            nonlocal captured_body
            captured_body = json.loads(request.data.decode("utf-8"))
            return _DummyHTTPResponse({"results": []})

        with patch.object(tavily_search_service.settings, "jarvis_tavily_enabled", True), patch.object(
            tavily_search_service.settings, "jarvis_tavily_api_key", "tvly-test"
        ), patch.object(
            tavily_search_service.settings, "jarvis_tavily_timeout_ms", 10000
        ), patch.object(
            tavily_search_service.settings, "jarvis_tavily_max_results_default", 5
        ), patch(
            "app.services.tavily_search_service.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            tavily_search_service.search_web("latest weather in san francisco", time_range="today")
        self.assertEqual(captured_body["time_range"], "day")

    def test_search_web_drops_invalid_time_range_before_request(self) -> None:
        captured_body: dict[str, object] = {}

        def fake_urlopen(request, timeout=None, context=None):
            nonlocal captured_body
            captured_body = json.loads(request.data.decode("utf-8"))
            return _DummyHTTPResponse({"results": []})

        with patch.object(tavily_search_service.settings, "jarvis_tavily_enabled", True), patch.object(
            tavily_search_service.settings, "jarvis_tavily_api_key", "tvly-test"
        ), patch.object(
            tavily_search_service.settings, "jarvis_tavily_timeout_ms", 10000
        ), patch.object(
            tavily_search_service.settings, "jarvis_tavily_max_results_default", 5
        ), patch(
            "app.services.tavily_search_service.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            tavily_search_service.search_web("latest weather in san francisco", time_range="recent")
        self.assertNotIn("time_range", captured_body)

    def test_build_ssl_context_prefers_explicit_tavily_ca_bundle(self) -> None:
        with patch.object(
            tavily_search_service.settings, "jarvis_tavily_ca_bundle", "/tmp/tavily-ca.pem"
        ), patch(
            "app.services.tavily_search_service.ssl.create_default_context",
            return_value=object(),
        ) as context_mock:
            tavily_search_service._build_ssl_context()
        context_mock.assert_called_once_with(cafile="/tmp/tavily-ca.pem")

    def test_build_ssl_context_falls_back_to_ssl_cert_file(self) -> None:
        with patch.object(
            tavily_search_service.settings, "jarvis_tavily_ca_bundle", ""
        ), patch.dict(os.environ, {"SSL_CERT_FILE": "/tmp/global-ca.pem"}, clear=False), patch(
            "app.services.tavily_search_service.ssl.create_default_context",
            return_value=object(),
        ) as context_mock:
            tavily_search_service._build_ssl_context()
        context_mock.assert_called_once_with(cafile="/tmp/global-ca.pem")
