from __future__ import annotations

import unittest
from typing import Any

from mira_etl.config import SourceConfig
from mira_etl.extract_html import HtmlSessionSpec, scrape_html_source


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeSession:
    def __init__(self, html: str) -> None:
        self.html = html
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("get", url, kwargs))
        return FakeResponse(self.html)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("post", url, kwargs))
        return FakeResponse(self.html)


class GenericHtmlExtractorTest(unittest.TestCase):
    def test_configuration_can_describe_an_equivalent_portal(self) -> None:
        config = equivalent_portal_config()

        spec = HtmlSessionSpec.from_config(config)

        self.assertEqual(spec.base_url, "https://compras.example.test/active")
        self.assertEqual(spec.dataset_name, "procesos_activos")
        self.assertEqual(spec.form_name_prefix, "sv:resultsForm")
        self.assertEqual(spec.portlet_prefix, "ElSalvadorPortlet")

    def test_scraper_uses_configured_form_fields_without_platform_code(self) -> None:
        html = """
        <html><body>
          <form action="https://compras.example.test/search">
            <input name="sv:resultsForm:viewState" value="abc">
            <input name="sv:resultsForm:_link_hidden_" value="">
            <select name="sv:resultsForm:resultadosItems">
              <option value="DIEZ" selected>10</option>
            </select>
          </form>
          <span>Página 1 / 1</span>
        </body></html>
        """
        session = FakeSession(html)

        result = scrape_html_source(
            equivalent_portal_config(), "202608", session=session  # type: ignore[arg-type]
        )

        self.assertEqual(result, {"procesos_activos": []})
        self.assertEqual(session.calls[0][0:2], ("get", "https://compras.example.test/active"))
        post_data = session.calls[1][2]["data"]
        self.assertEqual(post_data["sv:resultsForm:resultadosItems"], "CIEN")
        self.assertEqual(post_data["sv:resultsForm:_link_hidden_"], "")

    def test_missing_webforms_contract_fails_with_source_name(self) -> None:
        config = SourceConfig(
            source="missing_html_config",
            country_code="SV",
            source_system="Example",
            connector_version="test",
            download={"type": "html_session_scrape", "base_url": "https://example.test"},
        )

        with self.assertRaisesRegex(ValueError, "missing_html_config"):
            HtmlSessionSpec.from_config(config)


def equivalent_portal_config() -> SourceConfig:
    return SourceConfig(
        source="el_salvador_example",
        country_code="SV",
        source_system="Example WebForms",
        connector_version="test",
        download={
            "type": "html_session_scrape",
            "base_url": "https://compras.example.test/active",
            "webforms": {
                "dataset_name": "procesos_activos",
                "parser": "active_procedures",
                "form_name_prefix": "sv:resultsForm",
                "portlet_prefix": "ElSalvadorPortlet",
            },
        },
    )


if __name__ == "__main__":
    unittest.main()
