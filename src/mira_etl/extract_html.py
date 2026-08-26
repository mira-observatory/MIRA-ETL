from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from mira_etl.config import SourceConfig

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass(frozen=True)
class HtmlSessionSpec:
    """Configuration for the shared stateful JSF/portlet scraper."""

    base_url: str
    dataset_name: str
    form_name_prefix: str
    portlet_prefix: str
    parser: str = "active_procedures"
    page_size_field_suffix: str = "resultadosItems"
    page_size_value: str = "CIEN"
    link_hidden_field_suffix: str = "_link_hidden_"
    next_link_template: str = (
        "{form_name}:{portlet_prefix}__id88_{current_page}:"
        "{portlet_prefix}__id89"
    )
    page_text_pattern: str = r"P[aá]gina\s+(\d+)\s*/\s*(\d+)"
    max_pages: int = 10
    request_timeout_seconds: int = 45
    max_request_retries: int = 3
    retry_backoff_seconds: int = 5
    user_agent: str = DEFAULT_USER_AGENT

    @classmethod
    def from_config(cls, config: SourceConfig) -> "HtmlSessionSpec":
        download = config.download
        webforms = download.get("webforms") or {}
        required = {
            "base_url": download.get("base_url"),
            "dataset_name": webforms.get("dataset_name"),
            "form_name_prefix": webforms.get("form_name_prefix"),
            "portlet_prefix": webforms.get("portlet_prefix"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(
                f"HTML source '{config.source}' is missing download configuration: "
                + ", ".join(missing)
            )
        return cls(
            **required,
            parser=webforms.get("parser", "active_procedures"),
            page_size_field_suffix=webforms.get("page_size_field_suffix", "resultadosItems"),
            page_size_value=webforms.get("page_size_value", "CIEN"),
            link_hidden_field_suffix=webforms.get("link_hidden_field_suffix", "_link_hidden_"),
            next_link_template=webforms.get("next_link_template", cls.next_link_template),
            page_text_pattern=webforms.get("page_text_pattern", cls.page_text_pattern),
            max_pages=int(webforms.get("max_pages", 10)),
            request_timeout_seconds=int(webforms.get("request_timeout_seconds", 45)),
            max_request_retries=int(webforms.get("max_request_retries", 3)),
            retry_backoff_seconds=int(webforms.get("retry_backoff_seconds", 5)),
            user_agent=webforms.get("user_agent", DEFAULT_USER_AGENT),
        )


def fetch_with_retries(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout_seconds: int,
    max_retries: int,
    retry_backoff_seconds: int,
    **kwargs: Any,
) -> requests.Response:
    """Execute a source request with configurable retry/backoff."""
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            if method == "get":
                return session.get(url, timeout=timeout_seconds, **kwargs)
            return session.post(url, timeout=timeout_seconds, **kwargs)
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network dependent
            last_error = exc
            time.sleep(retry_backoff_seconds * attempt)
    assert last_error is not None
    raise last_error


def parse_form(html: str, form_name_prefix: str) -> tuple[str | None, dict[str, str] | None, BeautifulSoup]:
    """Locate the JSF form matching a field-name prefix and build a submittable payload
    from its current input/select values. These are stateful Java-portlet applications:
    every POST must resend the form's own hidden fields, not just the ones being changed."""
    soup = BeautifulSoup(html, "html.parser")
    target_form = None
    for form in soup.find_all("form"):
        if any(field.get("name", "").startswith(form_name_prefix) for field in form.find_all(["input", "select"])):
            target_form = form
            break
    if target_form is None:
        return None, None, soup

    payload: dict[str, str] = {}
    for field in target_form.find_all("input"):
        name = field.get("name")
        if not name or field.get("type") in ("submit", "checkbox"):
            continue
        payload[name] = field.get("value", "")
    for select in target_form.find_all("select"):
        name = select.get("name")
        if not name:
            continue
        selected = select.find("option", selected=True)
        payload[name] = selected["value"] if selected else select.find("option")["value"]

    return target_form["action"], payload, soup


def parse_active_procedures_page(soup: BeautifulSoup) -> list[dict[str, str | None]]:
    """Parse one page rendered by the shared active-procedures template.
    Each result is a 3-cell <tr>: [tipo + numero, detail block, "Mas Datos" link].
    The detail block is free text with fixed labels (Estado, Codigo SIGAF,
    Publicacion, Cierre, Ultima Actualizacion) followed by institucion, then
    category codes ("nombre (12345678)") and finally a free-text description."""
    rows: list[dict[str, str | None]] = []
    detail_links = soup.find_all("a", string=re.compile("M[aá]s Datos"))

    for link in detail_links:
        node = link
        data_row = None
        for _ in range(12):
            node = node.parent
            if node is None:
                break
            if node.name == "tr":
                cells_here = node.find_all("td", recursive=False)
                if len(cells_here) > 1:
                    data_row = node
                    break
        if data_row is None:
            continue

        cells = data_row.find_all("td", recursive=False)
        procedure_and_number = cells[0].get_text(" ", strip=True) if cells else ""
        detail_text = cells[1].get_text(" ", strip=True) if len(cells) > 1 else ""

        def find(pattern: str, text: str = detail_text) -> str | None:
            m = re.search(pattern, text)
            return m.group(1).strip() if m else None

        status = find(r"Estado:\s*(.+?)\s*C[oó]digo SIGAF:")
        sigaf_code = find(r"C[oó]digo SIGAF:\s*(.+?)\s*Publicaci[oó]n:")
        if sigaf_code == "#":
            sigaf_code = None  # "#" is the source's placeholder for "no code assigned"

        publication_date = find(r"Publicaci[oó]n:\s*(\d{2}/\d{2}/\d{4})")
        closing_date = find(r"Cierre:\s*(\d{2}/\d{2}/\d{4}(?:\s+\d{1,2}:\d{2}:\d{2}\s*[AP]M)?)")
        last_updated = find(r"[UÚ]ltima Actualizaci[oó]n:\s*(\d{2}/\d{2}/\d{4}(?:\s+\d{1,2}:\d{2}:\d{2}\s*[AP]M)?)")

        remainder = find(
            r"[UÚ]ltima Actualizaci[oó]n:\s*\d{2}/\d{2}/\d{4}(?:\s+\d{1,2}:\d{2}:\d{2}\s*[AP]M)?\s*(.+)"
        )
        buyer_name, category, description = None, None, None
        if remainder:
            parts = remainder.split(" - ", 1)
            buyer_name = parts[0].strip() if parts else None
            block = parts[1].strip() if len(parts) > 1 else ""

            # Category codes anchor the split (never split on comma: some category
            # names contain their own commas, e.g. "Lubricantes, aceites, grasas...").
            code_pattern = re.compile(r"\((\d{8})\)")
            matches = list(code_pattern.finditer(block))
            if matches:
                segments, cursor = [], 0
                for match in matches:
                    name = block[cursor:match.start()].strip().lstrip(",").strip()
                    segments.append(f"{name} ({match.group(1)})")
                    cursor = match.end()
                category = "; ".join(segments)
                description = block[cursor:].strip().lstrip(",").strip() or None
            else:
                description = block.strip() or None

        procedure_type, procedure_number = None, None
        m = re.match(r"^(.*?)\s+(\d+/\d{4})$", procedure_and_number)
        if m:
            procedure_type, procedure_number = m.group(1), m.group(2)
        else:
            procedure_type = procedure_and_number or None

        rows.append({
            "tipo_procedimiento": procedure_type,
            "numero_proceso": procedure_number,
            "estado": status,
            "codigo_sigaf": sigaf_code,
            "institucion": buyer_name,
            "categoria": category,
            "descripcion": description,
            "fecha_publicacion": publication_date,
            "fecha_cierre": closing_date,
            "ultima_actualizacion": last_updated,
        })

    return rows


PARSERS = {
    "active_procedures": parse_active_procedures_page,
}


def fetch_html_dataset(
    session: requests.Session,
    spec: HtmlSessionSpec,
    limit: int | None = None,
) -> list[dict[str, str | None]]:
    """Fetch one configured JSF/portlet dataset, including pagination.

    If `limit` is set, stops paginating as soon as enough rows are collected
    instead of walking every page.
    """
    try:
        parse_page = PARSERS[spec.parser]
    except KeyError as exc:
        raise ValueError(f"Unsupported HTML parser: {spec.parser}") from exc

    session.headers.setdefault("User-Agent", spec.user_agent)

    request_options = {
        "timeout_seconds": spec.request_timeout_seconds,
        "max_retries": spec.max_request_retries,
        "retry_backoff_seconds": spec.retry_backoff_seconds,
    }

    response = fetch_with_retries(session, "get", spec.base_url, **request_options)
    action, payload, _ = parse_form(response.text, spec.form_name_prefix)
    if action is None or payload is None:
        raise RuntimeError(
            f"Could not locate form '{spec.form_name_prefix}' at {spec.base_url}."
        )

    payload[f"{spec.form_name_prefix}:{spec.page_size_field_suffix}"] = spec.page_size_value
    payload[f"{spec.form_name_prefix}:{spec.link_hidden_field_suffix}"] = ""
    response = fetch_with_retries(
        session, "post", urljoin(spec.base_url, action), data=payload, **request_options
    )

    all_rows: list[dict[str, str | None]] = []
    page_number = 1

    while page_number <= spec.max_pages:
        action, payload, soup = parse_form(response.text, spec.form_name_prefix)
        if action is None or payload is None:
            break

        page_rows = parse_page(soup)
        all_rows.extend(page_rows)

        if limit is not None and len(all_rows) >= limit:
            return all_rows[:limit]

        page_text = soup.get_text(" ", strip=True)
        match = re.search(spec.page_text_pattern, page_text)
        if not match:
            break
        current_page, total_pages = int(match.group(1)), int(match.group(2))
        if current_page >= total_pages:
            break

        # Confirmed pattern: to request page (current_page + 1), N = current_page.
        next_link_value = spec.next_link_template.format(
            form_name=spec.form_name_prefix,
            portlet_prefix=spec.portlet_prefix,
            current_page=current_page,
        )
        payload[f"{spec.form_name_prefix}:{spec.link_hidden_field_suffix}"] = next_link_value
        response = fetch_with_retries(
            session, "post", urljoin(spec.base_url, action), data=payload, **request_options
        )
        page_number += 1

    return all_rows


def scrape_html_source(
    config: SourceConfig,
    period: str,
    limit: int | None = None,
    session: requests.Session | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Generic pipeline entry point for configured HTML session sources.

    `limit` caps the number of records fetched -- intended for quick smoke tests
    against a real database. `period` is accepted for the common extractor
    contract; current active-procedure portals expose current state rather than
    a historical period.
    """
    del period
    spec = HtmlSessionSpec.from_config(config)
    active_session = session or requests.Session()
    rows = fetch_html_dataset(active_session, spec, limit=limit)
    return {spec.dataset_name: rows}


# ---------------------------------------------------------------------------
# Nicaragua SISCAE: adjudicaciones y procesos cerrados.
#
# Lo de arriba (HtmlSessionSpec / fetch_html_dataset) paginaria UN listado del
# estado que trae `download.base_url` -- que para Nicaragua esta fijo a
# VIGENTE. Verificado en produccion (2026-08-26): esa es la razon completa de
# por que Nicaragua cargaba con cero adjudicaciones. Un proceso VIGENTE, por
# definicion, todavia no tiene adjudicacion.
#
# El portal SISCAE tiene un segundo buscador, "Todos los Procesos"
# (`busqueda?accion=todos`), que expone el estado como checkbox: VIGENTE,
# ADJUDICADO, CERRADO, EJECUCION, DESIERTO, CANCELADO, SUSPENDIDO. Las filas
# salen con el mismo formato que "Procesos Vigentes", asi que
# parse_active_procedures_page las parsea sin cambios. Lo que sigue no encaja
# en HtmlSessionSpec (necesita mandar un checkbox de estado y navegar un
# formulario de detalle con botones, no solo paginar), asi que vive aparte en
# vez de forzarlo dentro de la abstraccion generica.
# ---------------------------------------------------------------------------

#: El buscador "Todos los Procesos". A diferencia de `download.base_url`
#: (fijo a VIGENTE), este acepta el estado como parametro.
SEARCH_ALL_URL = (
    "https://www.gestion.nicaraguacompra.gob.ni/siscae/portal/"
    "adquisiciones-gestion/busqueda?accion=todos"
)
SEARCH_FORM_NAME = "inicioBusqForm"
DETAIL_FORM_NAME = "datosProcedimiento"
SEARCH_PORTLET_PREFIX = "Pluto__adquisiciones_gestion_portlet_busquedaProcedimientoPortlet"

#: Adjudicado: proveedor, RUC y monto publicados (parcialmente -- ver abajo).
STATE_AWARDED = "ADJUDICADO"
#: El checkbox dice CERRADO pero las filas muestran "En Evaluacion": cerrado
#: a ofertas, adjudicacion aun no decidida. Verificado 2026-08-26: es el
#: bucket mas grande de Nicaragua, ~2,000 procesos contra ~1,300 adjudicados
#: y ~500 vigentes. Repetido 4 veces porque la primera lectura dio cero (el
#: portal es fragil bajo carga, no que el estado no exista).
STATE_CLOSED = "CERRADO"
#: EJECUCION, DESIERTO, CANCELADO y SUSPENDIDO devuelven cero en el portal
#: real (verificado 2026-08-26): no vale la pena gastarles una peticion.

#: El listado de adjudicados llega a 13 paginas de 100 filas. El "10" de
#: HtmlSessionSpec.max_pages esta pensado para el bloque numerado de
#: paginacion (que solo cubre 10 paginas); el control ">" no tiene ese techo.
MAX_SEARCH_PAGES = 40
#: SISCAE corre en una instancia unica sin balanceo y corta conexiones bajo
#: carga sostenida (observado, repetidamente). Pausa cada paso de navegacion.
POLITE_DELAY_SECONDS = 1.5


def _decoded_soup(response: requests.Response) -> BeautifulSoup:
    """SISCAE sirve Latin-1 sin declararlo de forma fiable.

    Sin esto, cada nombre de institucion o proveedor acentuado entra a la
    base como mojibake -- el tipo de dano que nadie nota hasta que el dato ya
    esta publicado.
    """
    response.encoding = "iso-8859-1"
    return BeautifulSoup(response.text, "html.parser")


def _form_payload(form: Any) -> dict[str, str]:
    """Arma un payload enviable con los valores actuales de un formulario."""
    payload: dict[str, str] = {}
    for field in form.find_all("input"):
        name = field.get("name")
        if name and field.get("type") not in ("submit", "checkbox"):
            payload[name] = field.get("value", "")
    for select in form.find_all("select"):
        name = select.get("name")
        if not name:
            continue
        selected = select.find("option", selected=True) or select.find("option")
        payload[name] = selected["value"]
    return payload


def _find_form(soup: BeautifulSoup, name_prefix: str) -> Any:
    for form in soup.find_all("form"):
        if any(f.get("name", "").startswith(name_prefix) for f in form.find_all(["input", "select"])):
            return form
    return None


def _submit_button(form: Any, label_fragment: str) -> Any:
    """Busca un <input type=submit> por un fragmento de su etiqueta visible.

    Un fragmento y no el texto exacto a proposito: las etiquetas llevan
    acentos ("Adjudicacion"), y una comparacion exacta se rompe apenas
    cambia la decodificacion.
    """
    if form is None:
        return None
    for field in form.find_all("input", {"type": "submit"}):
        if label_fragment in (field.get("value") or ""):
            return field
    return None


def _page_numbers(soup: BeautifulSoup) -> tuple[int, int] | None:
    match = re.search(r"P[aá]gina\s+(\d+)\s*/\s*(\d+)", soup.get_text(" ", strip=True))
    return (int(match.group(1)), int(match.group(2))) if match else None


def _fetch(
    session: requests.Session, spec: HtmlSessionSpec, method: str, url: str, **kwargs: Any
) -> requests.Response:
    """`fetch_with_retries` con los tiempos/reintentos ya resueltos del spec,
    para no repetirlos en cada llamada de las funciones de abajo."""
    return fetch_with_retries(
        session,
        method,
        url,
        timeout_seconds=spec.request_timeout_seconds,
        max_retries=spec.max_request_retries,
        retry_backoff_seconds=spec.retry_backoff_seconds,
        **kwargs,
    )


def search_procedures_by_state(
    session: requests.Session, spec: HtmlSessionSpec, state: str, limit: int | None = None
) -> list[dict[str, str | None]]:
    """Todo proceso en un estado dado, via el buscador avanzado del portal.

    Las filas salen con el mismo formato que "Procesos Vigentes", asi que
    parse_active_procedures_page las parsea sin cambios -- lo unico que
    cambia es el campo Estado.
    """
    session.headers.setdefault("User-Agent", spec.user_agent)

    _fetch(session, spec, "get", spec.base_url)  # establece el jsessionid
    response = _fetch(session, spec, "get", SEARCH_ALL_URL)
    action, payload, _ = parse_form(response.text, SEARCH_FORM_NAME)
    if action is None or payload is None:
        raise RuntimeError("No se encontro el formulario de busqueda avanzada.")

    payload[f"{SEARCH_FORM_NAME}:estadoAdqPuId"] = state
    payload[f"{SEARCH_FORM_NAME}:resultadosItems"] = "CIEN"
    # El orden es lo que hace confiable la paginacion, no un detalle cosmetico:
    # sin un ordenItems explicito SISCAE pagina sobre un resultado sin orden
    # estable y las filas se barajan entre peticiones. Medido sobre 5 paginas
    # de 10: sin orden, 32 procesos distintos de 50 recolectados (las paginas
    # avanzaban 1, luego 10, luego 5). Con este orden fijo, 48 de 50.
    payload[f"{SEARCH_FORM_NAME}:ordenItems"] = "PorFechaPublicacion"
    payload[f"{SEARCH_FORM_NAME}:{SEARCH_PORTLET_PREFIX}__id75"] = "Buscar"
    response = _fetch(session, spec, "post", action, data=payload)

    all_rows: list[dict[str, str | None]] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    for _ in range(MAX_SEARCH_PAGES):
        soup = _decoded_soup(response)
        for row in parse_active_procedures_page(soup):
            key = (row.get("numero_proceso"), row.get("institucion"), row.get("descripcion"))
            if key in seen:
                continue
            seen.add(key)
            all_rows.append(row)

        if limit is not None and len(all_rows) >= limit:
            return all_rows[:limit]

        pages = _page_numbers(soup)
        if pages is None or pages[0] >= pages[1]:
            break

        action, payload, _ = parse_form(response.text, "resultadoView:listadoProcedimientosForm")
        if action is None or payload is None:
            break
        # El control ">" en vez de los enlaces numerados: el bloque numerado
        # solo cubre 10 paginas, y este listado llega a 13.
        payload["resultadoView:listadoProcedimientosForm:_link_hidden_"] = (
            f"resultadoView:listadoProcedimientosForm:{SEARCH_PORTLET_PREFIX}__id91"
        )
        time.sleep(POLITE_DELAY_SECONDS)
        response = _fetch(session, spec, "post", action, data=payload)

    return all_rows


#: Una fila de RESUMEN DE ADJUDICACIONES: "US$ 1,336,229.69" o "C$ 2,199,999.98".
AWARD_AMOUNT_PATTERN = re.compile(r"^(US\$|C\$)\s*([\d.,]+)$")
#: "ENERGIA ELECTRICA SOL Y VIENTO SOCIEDAD ANONIMA - J0310000350337"
SUPPLIER_RUC_PATTERN = re.compile(r"^(.*?)\s*-\s*([A-Z0-9]{8,})$")


def parse_award_summary(soup: BeautifulSoup) -> list[dict[str, str | None]]:
    """Filas de RESUMEN DE ADJUDICACIONES: proveedor, RUC, monto, moneda.

    Se ancla en la celda del monto y no en el encabezado de la tabla: la
    pagina anida el mismo contenido dentro de varias tablas contenedoras, y
    buscar por encabezado devuelve tambien esas envolturas y cuenta cada
    adjudicacion varias veces.
    """
    awards: list[dict[str, str | None]] = []
    for row in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
        if len(cells) != 4:
            continue
        amount_match = AWARD_AMOUNT_PATTERN.match(cells[2] or "")
        if not amount_match:
            continue

        supplier: str | None = cells[0]
        ruc: str | None = None
        supplier_match = SUPPLIER_RUC_PATTERN.match(cells[0])
        if supplier_match:
            supplier = supplier_match.group(1).strip()
            ruc = supplier_match.group(2).strip()

        awards.append(
            {
                "proveedor": supplier or None,
                "ruc": ruc,
                # La fuente escribe la moneda como simbolo, nunca como codigo.
                "moneda": "USD" if amount_match.group(1) == "US$" else "NIO",
                "monto": amount_match.group(2).replace(",", ""),
                "renglones": cells[3] or None,
            }
        )
    return awards


def _go_back(session: requests.Session, spec: HtmlSessionSpec, form: Any, fallback: BeautifulSoup) -> BeautifulSoup:
    """Presiona Volver para regresar al listado de resultados."""
    button = _submit_button(form, "Volver")
    if form is None or button is None:
        return fallback
    payload = _form_payload(form)
    payload[button["name"]] = button.get("value", "")
    time.sleep(POLITE_DELAY_SECONDS)
    return _decoded_soup(_fetch(session, spec, "post", form["action"], data=payload))


def fetch_award_detail(
    session: requests.Session, spec: HtmlSessionSpec, listing: BeautifulSoup, index: int
) -> tuple[list[dict[str, str | None]], BeautifulSoup]:
    """Abre la pestana de adjudicacion de un resultado; devuelve (adjudicaciones, listado).

    La navegacion es listado -> [Mas Datos] -> [Adjudicacion] -> [Volver].
    "Mas Datos" es un enlace manejado por el campo `_link_hidden_` del
    portlet, pero "Adjudicacion" es un <input type=submit> comun -- buscarlo
    como enlace es casi seguramente lo que hizo parecer que SISCAE lo
    renderizaba "de forma intermitente". Verificado contra el portal real:
    Volver si restaura el listado (misma pagina, mismas 100 filas), y un
    proceso dado expone la pestana o no de forma consistente entre corridas.

    Una lista de adjudicaciones vacia significa que el proceso no publica ese
    detalle -- es una propiedad de la fuente, no una falla, asi que se
    devuelve en vez de levantar una excepcion.
    """
    form = _find_form(listing, "resultadoView:listadoProcedimientosForm")
    if form is None:
        return [], listing

    payload = _form_payload(form)
    payload["resultadoView:listadoProcedimientosForm:_link_hidden_"] = (
        f"resultadoView:listadoProcedimientosForm:listadoProcedimientos_{index}:verDatosLink"
    )
    time.sleep(POLITE_DELAY_SECONDS)
    detail = _decoded_soup(_fetch(session, spec, "post", form["action"], data=payload))

    detail_form = _find_form(detail, DETAIL_FORM_NAME)
    if detail_form is None:
        return [], listing

    award_button = _submit_button(detail_form, "djudicaci")
    if award_button is None:
        return [], _go_back(session, spec, detail_form, listing)

    payload = _form_payload(detail_form)
    payload[award_button["name"]] = award_button.get("value", "")
    time.sleep(POLITE_DELAY_SECONDS)
    award_page = _decoded_soup(_fetch(session, spec, "post", detail_form["action"], data=payload))

    awards = parse_award_summary(award_page)
    return awards, _go_back(session, spec, _find_form(award_page, DETAIL_FORM_NAME), listing)


def scrape_awarded_procedures(
    session: requests.Session,
    spec: HtmlSessionSpec,
    limit: int | None = None,
    award_detail_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Procesos adjudicados, cada uno enriquecido con su detalle cuando se publica.

    Dos fases de costo muy distinto, a proposito separadas:

    1. El listado (barato): una busqueda mas una peticion por pagina de 100.
       Esto solo ya es lo que le faltaba a Nicaragua -- el conector viejo
       solo podia ver procesos VIGENTE, que por definicion no tienen
       adjudicacion.
    2. El detalle de adjudicacion (caro): tres peticiones por proceso, porque
       proveedor, RUC y monto solo existen detras de Mas Datos -> Adjudicacion.
       `award_detail_limit` acota cuantos procesos se enriquecen en una
       corrida, para poder recorrer el corpus completo por lotes en vez de
       castigar un portal que corta conexiones bajo carga sostenida.

    Un proceso cuyo detalle nunca se consulta igual entrega su fila de
    listado, sin la clave `adjudicaciones` -- distinguible de uno que si se
    reviso y no publica nada (`adjudicaciones: []`). Confundir esos dos casos
    seria reportar en silencio "sin adjudicacion" para un proceso que nadie
    llego a mirar.
    """
    rows = search_procedures_by_state(session, spec, STATE_AWARDED, limit=limit)
    if award_detail_limit == 0 or not rows:
        return rows

    # Los indices que usa la navegacion de detalle son relativos a la pagina
    # (0..99), asi que una corrida enriquece como maximo la primera pagina.
    # Recorrer paginas siguientes es un lote aparte, no un bucle mas largo.
    enrich_count = min(award_detail_limit or len(rows), len(rows), 100)

    listing = _search_listing(session, spec, STATE_AWARDED)
    for index in range(enrich_count):
        try:
            awards, listing = fetch_award_detail(session, spec, listing, index)
            rows[index]["adjudicaciones"] = awards
        except requests.exceptions.RequestException:
            # Una conexion caida no puede descartar las filas ya recolectadas.
            break
    return rows


def _search_listing(session: requests.Session, spec: HtmlSessionSpec, state: str) -> BeautifulSoup:
    """Corre la busqueda avanzada y devuelve la pagina 1 como sopa (no filas)."""
    _fetch(session, spec, "get", spec.base_url)
    response = _fetch(session, spec, "get", SEARCH_ALL_URL)
    action, payload, _ = parse_form(response.text, SEARCH_FORM_NAME)
    if action is None or payload is None:
        raise RuntimeError("No se encontro el formulario de busqueda avanzada.")
    payload[f"{SEARCH_FORM_NAME}:estadoAdqPuId"] = state
    payload[f"{SEARCH_FORM_NAME}:resultadosItems"] = "CIEN"
    payload[f"{SEARCH_FORM_NAME}:ordenItems"] = "PorFechaPublicacion"
    payload[f"{SEARCH_FORM_NAME}:{SEARCH_PORTLET_PREFIX}__id75"] = "Buscar"
    return _decoded_soup(_fetch(session, spec, "post", action, data=payload))


def scrape_nicaragua_extras(
    config: SourceConfig,
    session: requests.Session,
    limit: int | None = None,
    award_detail_limit: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Los dos datasets que `scrape_html_source` no puede traer para Nicaragua:
    adjudicados (con su detalle cuando se publica) y cerrados ("En Evaluacion").

    Aparte de `scrape_html_source` en vez de dentro: ese es el camino generico
    para CUALQUIER fuente `html_session_scrape` futura, y forzar ahi una
    navegacion de varios pasos especifica de Nicaragua (buscador avanzado,
    checkbox de estado, boton de Adjudicacion) le pondria a esa abstraccion
    una forma que ninguna otra fuente necesita.
    """
    spec = HtmlSessionSpec.from_config(config)
    awarded = scrape_awarded_procedures(
        session, spec, limit=limit, award_detail_limit=award_detail_limit
    )
    closed = search_procedures_by_state(session, spec, STATE_CLOSED, limit=limit)
    return {"procesos_adjudicados": awarded, "procesos_cerrados": closed}
    return {spec.dataset_name: rows}
