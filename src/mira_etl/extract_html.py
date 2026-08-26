from __future__ import annotations

import re
import time
from typing import Any

import requests
from bs4 import BeautifulSoup


BASE_URL = (
    "https://www.gestion.nicaraguacompra.gob.ni/siscae/portal/"
    "adquisiciones-gestion/busquedaProcedimientosVigentes?proc_estado=VIGENTE"
)
#: "Todos los Procesos" -- the portal's own advanced search. Unlike BASE_URL
#: (which is hard-wired to VIGENTE), this one exposes the process state as a
#: checkbox, so it can list ADJUDICADO, CERRADO, EJECUCION and the rest.
#: That distinction is the whole reason Nicaragua had zero awards loaded:
#: BASE_URL can only ever return processes that, by definition, have not been
#: awarded yet.
SEARCH_ALL_URL = (
    "https://www.gestion.nicaraguacompra.gob.ni/siscae/portal/"
    "adquisiciones-gestion/busqueda?accion=todos"
)
FORM_NAME = "resultadoView:listadoProcedimientosForm"
SEARCH_FORM_NAME = "inicioBusqForm"
DETAIL_FORM_NAME = "datosProcedimiento"
PORTLET_PREFIX = "Pluto__adquisiciones_gestion_portlet_busquedaProcedimientosVigentesPortlet"
SEARCH_PORTLET_PREFIX = "Pluto__adquisiciones_gestion_portlet_busquedaProcedimientoPortlet"

#: Process states SISCAE accepts in the advanced search. A process is only
#: awarded (supplier + amount published) once it leaves VIGENTE.
STATE_AWARDED = "ADJUDICADO"
#: What "CIEN" means in the results-per-page selector.
ROWS_PER_PAGE = 100

REQUEST_TIMEOUT_SECONDS = 45
MAX_REQUEST_RETRIES = 3
MAX_PAGES = 10  # safety cap; the confirmed pagination pattern only covers one block of 10 pages
#: The awarded listing runs to 13 pages at a hundred rows each, so the ten-page
#: cap above (written for the numbered pagination block) would silently drop
#: the last ~300 processes. The ">" control has no such block limit.
MAX_SEARCH_PAGES = 40
#: SISCAE runs on a single unbalanced instance and drops connections under
#: sustained load (observed, repeatedly). Pace every navigation step.
POLITE_DELAY_SECONDS = 1.5
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def fetch_with_retries(session: requests.Session, method: str, url: str, **kwargs: Any) -> requests.Response:
    """SISCAE runs on a single, unbalanced instance and times out intermittently.
    Wrap every request with a small retry/backoff instead of failing the whole run."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_REQUEST_RETRIES + 1):
        try:
            if method == "get":
                return session.get(url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
            return session.post(url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network dependent
            last_error = exc
            time.sleep(5 * attempt)
    assert last_error is not None
    raise last_error


def parse_form(html: str, form_name_prefix: str) -> tuple[str | None, dict[str, str] | None, BeautifulSoup]:
    """Locate the JSF form matching a field-name prefix and build a submittable payload
    from its current input/select values. SISCAE is a stateful Java-portlet application:
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
    """Parse one page of the "Procesos Vigentes" results table.
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


def _decoded_soup(response: requests.Response) -> BeautifulSoup:
    """SISCAE serves Latin-1 without declaring it reliably.

    Without this, every accented buyer name and description lands in the
    database as mojibake -- the kind of damage nobody notices until the data
    is already published.
    """
    response.encoding = "iso-8859-1"
    return BeautifulSoup(response.text, "html.parser")


def _form_payload(form: Any) -> dict[str, str]:
    """Build a submittable payload from a form's current values."""
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
    """Find a submit input by a fragment of its visible label.

    Matching a fragment rather than the exact string is deliberate: the labels
    carry accents ("Adjudicacion"), so an exact compare breaks the moment the
    decoding changes.
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


def search_procedures_by_state(
    session: requests.Session, state: str, limit: int | None = None
) -> list[dict[str, str | None]]:
    """Every process in a given state, via the portal's own advanced search.

    Rows come back in exactly the same shape as the "Procesos Vigentes"
    listing, so `parse_active_procedures_page` parses them unchanged -- the
    only field that differs is Estado.
    """
    session.headers.setdefault("User-Agent", USER_AGENT)

    fetch_with_retries(session, "get", BASE_URL)  # establishes the jsessionid
    response = fetch_with_retries(session, "get", SEARCH_ALL_URL)
    action, payload, _ = parse_form(response.text, SEARCH_FORM_NAME)
    if action is None or payload is None:
        raise RuntimeError("Could not locate the advanced search form.")

    payload[f"{SEARCH_FORM_NAME}:estadoAdqPuId"] = state
    payload[f"{SEARCH_FORM_NAME}:resultadosItems"] = "CIEN"
    # Sorting is not cosmetic here: it is what makes pagination trustworthy.
    # Without an explicit order SISCAE pages over an unsorted result set, so
    # the rows shuffle between requests -- measured over 5 pages of 10, an
    # unsorted walk returned 32 distinct processes out of 50 collected (pages
    # advancing by 1, then 10, then 5). With this set, the same walk returned
    # 48 of 50. The duplicates are harmless (the mart upserts on
    # source_record_id); the silently skipped records were not.
    payload[f"{SEARCH_FORM_NAME}:ordenItems"] = "PorFechaPublicacion"
    payload[f"{SEARCH_FORM_NAME}:{SEARCH_PORTLET_PREFIX}__id75"] = "Buscar"
    response = fetch_with_retries(session, "post", action, data=payload)

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

        action, payload, _ = parse_form(response.text, FORM_NAME)
        if action is None or payload is None:
            break
        # The ">" control rather than the numbered links: the numbered block
        # only ever covers ten pages at a time, and this listing runs to 13
        # at a hundred rows per page.
        payload[f"{FORM_NAME}:_link_hidden_"] = f"{FORM_NAME}:{SEARCH_PORTLET_PREFIX}__id91"
        time.sleep(POLITE_DELAY_SECONDS)
        response = fetch_with_retries(session, "post", action, data=payload)

    return all_rows


#: A row of RESUMEN DE ADJUDICACIONES: "US$ 1,336,229.69" or "C$ 2,199,999.98".
AWARD_AMOUNT_PATTERN = re.compile(r"^(US\$|C\$)\s*([\d.,]+)$")
#: "ENERGIA ELECTRICA SOL Y VIENTO SOCIEDAD ANONIMA - J0310000350337"
SUPPLIER_RUC_PATTERN = re.compile(r"^(.*?)\s*-\s*([A-Z0-9]{8,})$")


def parse_award_summary(soup: BeautifulSoup) -> list[dict[str, str | None]]:
    """Rows of the RESUMEN DE ADJUDICACIONES table: supplier, RUC, amount.

    Anchored on the amount cell rather than on the table heading: the page
    nests the same content inside several wrapper tables, so matching by
    heading also returns the wrappers and would count every award repeatedly.
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
                # The source writes the currency as a symbol, never as a code.
                "moneda": "USD" if amount_match.group(1) == "US$" else "NIO",
                "monto": amount_match.group(2).replace(",", ""),
                "renglones": cells[3] or None,
            }
        )
    return awards


def _go_back(session: requests.Session, form: Any, fallback: BeautifulSoup) -> BeautifulSoup:
    """Press Volver to return to the results listing."""
    button = _submit_button(form, "Volver")
    if form is None or button is None:
        return fallback
    payload = _form_payload(form)
    payload[button["name"]] = button.get("value", "")
    time.sleep(POLITE_DELAY_SECONDS)
    return _decoded_soup(fetch_with_retries(session, "post", form["action"], data=payload))


def fetch_award_detail(
    session: requests.Session, listing: BeautifulSoup, index: int
) -> tuple[list[dict[str, str | None]], BeautifulSoup]:
    """Open one result's award tab; return (awards, listing to continue from).

    Navigation is listing -> [Mas Datos] -> [Adjudicacion] -> [Volver].
    "Mas Datos" is a link driven by the portlet's `_link_hidden_` field, but
    "Adjudicacion" is a plain submit button -- looking for it as a link is
    almost certainly what made it look like SISCAE rendered it
    "intermittently". Verified against the live portal: Volver does restore
    the listing (same page, same 100 rows), and a given process either exposes
    the tab or does not, consistently across runs.

    An empty award list means the process publishes no award detail. That is a
    property of the source, not a failure, so it is returned rather than
    raised.
    """
    form = _find_form(listing, FORM_NAME)
    if form is None:
        return [], listing

    payload = _form_payload(form)
    payload[f"{FORM_NAME}:_link_hidden_"] = (
        f"{FORM_NAME}:listadoProcedimientos_{index}:verDatosLink"
    )
    time.sleep(POLITE_DELAY_SECONDS)
    detail = _decoded_soup(fetch_with_retries(session, "post", form["action"], data=payload))

    detail_form = _find_form(detail, DETAIL_FORM_NAME)
    if detail_form is None:
        return [], listing

    award_button = _submit_button(detail_form, "djudicaci")
    if award_button is None:
        return [], _go_back(session, detail_form, listing)

    payload = _form_payload(detail_form)
    payload[award_button["name"]] = award_button.get("value", "")
    time.sleep(POLITE_DELAY_SECONDS)
    award_page = _decoded_soup(
        fetch_with_retries(session, "post", detail_form["action"], data=payload)
    )

    awards = parse_award_summary(award_page)
    return awards, _go_back(session, _find_form(award_page, DETAIL_FORM_NAME), listing)


def fetch_active_procedures(session: requests.Session, limit: int | None = None) -> list[dict[str, str | None]]:
    """Fetch every "Procesos Vigentes" record from SISCAE: raises the page size to
    100 (from the default 10) and follows the validated pagination pattern
    (portlet link id N corresponds to page N+1, within one block of 10 pages).

    If `limit` is set, stops paginating as soon as enough rows are collected
    instead of walking every page -- useful for quick smoke tests so they
    don't hammer the source server for a handful of records."""
    session.headers.setdefault("User-Agent", USER_AGENT)

    response = fetch_with_retries(session, "get", BASE_URL)
    action, payload, _ = parse_form(response.text, FORM_NAME)
    if action is None or payload is None:
        raise RuntimeError("Could not locate the results form on the Procesos Vigentes page.")

    payload[f"{FORM_NAME}:resultadosItems"] = "CIEN"
    payload[f"{FORM_NAME}:_link_hidden_"] = ""
    response = fetch_with_retries(session, "post", action, data=payload)

    all_rows: list[dict[str, str | None]] = []
    page_number = 1

    while page_number <= MAX_PAGES:
        action, payload, soup = parse_form(response.text, FORM_NAME)
        if action is None or payload is None:
            break

        page_rows = parse_active_procedures_page(soup)
        all_rows.extend(page_rows)

        if limit is not None and len(all_rows) >= limit:
            return all_rows[:limit]

        page_text = soup.get_text(" ", strip=True)
        match = re.search(r"P[aá]gina\s+(\d+)\s*/\s*(\d+)", page_text)
        if not match:
            break
        current_page, total_pages = int(match.group(1)), int(match.group(2))
        if current_page >= total_pages:
            break

        # Confirmed pattern: to request page (current_page + 1), N = current_page.
        next_link_value = f"{FORM_NAME}:{PORTLET_PREFIX}__id88_{current_page}:{PORTLET_PREFIX}__id89"
        payload[f"{FORM_NAME}:_link_hidden_"] = next_link_value
        response = fetch_with_retries(session, "post", action, data=payload)
        page_number += 1

    return all_rows


def scrape_awarded_procedures(
    session: requests.Session,
    limit: int | None = None,
    award_detail_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Awarded processes, each enriched with its award detail when published.

    Two phases with very different costs, kept separate on purpose:

    1. The listing (cheap): one search plus one request per page of 100.
       This alone is what Nicaragua was missing -- the old connector could
       only ever see VIGENTE processes, which by definition have no award.
    2. The award detail (expensive): three requests per process, because
       supplier, RUC and amount only exist behind Mas Datos -> Adjudicacion.
       `award_detail_limit` caps how many processes are enriched in one run,
       so the full corpus can be walked in batches instead of hammering a
       portal that drops connections under sustained load.

    A process whose detail is never fetched still yields its listing row, with
    `adjudicaciones` absent -- distinguishable from one that was checked and
    publishes nothing (`adjudicaciones: []`). Conflating those two would mean
    silently reporting "no award" for a process nobody ever looked at.
    """
    rows = search_procedures_by_state(session, STATE_AWARDED, limit=limit)
    if award_detail_limit == 0 or not rows:
        return rows

    # The listing indices the detail navigation uses are page-local (0..99),
    # so one run enriches at most the first page. Walking further pages is a
    # separate batch, not a longer loop.
    enrich_count = min(award_detail_limit or len(rows), len(rows), ROWS_PER_PAGE)

    listing = _search_listing(session, STATE_AWARDED)
    for index in range(enrich_count):
        try:
            awards, listing = fetch_award_detail(session, listing, index)
            rows[index]["adjudicaciones"] = awards
        except requests.exceptions.RequestException:
            # A dropped connection must not discard the rows already collected.
            break
    return rows


def _search_listing(session: requests.Session, state: str) -> BeautifulSoup:
    """Run the advanced search and return page 1 as a soup (not parsed rows)."""
    fetch_with_retries(session, "get", BASE_URL)
    response = fetch_with_retries(session, "get", SEARCH_ALL_URL)
    action, payload, _ = parse_form(response.text, SEARCH_FORM_NAME)
    if action is None or payload is None:
        raise RuntimeError("Could not locate the advanced search form.")
    payload[f"{SEARCH_FORM_NAME}:estadoAdqPuId"] = state
    payload[f"{SEARCH_FORM_NAME}:resultadosItems"] = "CIEN"
    payload[f"{SEARCH_FORM_NAME}:{SEARCH_PORTLET_PREFIX}__id75"] = "Buscar"
    return _decoded_soup(fetch_with_retries(session, "post", action, data=payload))


def scrape_siscae(
    period: str,
    limit: int | None = None,
    award_detail_limit: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Entry point used by the pipeline. Returns a mapping shaped like the CSV-based
    connectors' `source_rows` (logical dataset name -> list of row dicts), so the
    rest of the pipeline (raw storage, staging, mart upsert) needs no changes.

    `limit` caps the number of records fetched -- intended for quick smoke tests
    against a real database without loading the full corpus.
    """
    session = requests.Session()
    active_rows = fetch_active_procedures(session, limit=limit)
    awarded_rows = scrape_awarded_procedures(
        session, limit=limit, award_detail_limit=award_detail_limit
    )
    return {"procesos_vigentes": active_rows, "procesos_adjudicados": awarded_rows}
