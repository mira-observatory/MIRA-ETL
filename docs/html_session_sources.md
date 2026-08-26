# Fuentes HTML con sesión

`src/mira_etl/extract_html.py` contiene un motor reutilizable para portales
JSF/portlet que comparten la misma estructura de resultados y paginación. El
extractor no contiene URLs, nombres de formulario ni prefijos propios de un
país; esos valores viven en `config/sources/<fuente>.json`.

Ejemplo de configuración para otra plataforma equivalente:

```json
{
  "source": "el_salvador_ejemplo",
  "country_code": "SV",
  "source_system": "Portal de compras El Salvador",
  "connector_version": "sv-html-0.1.0",
  "download": {
    "type": "html_session_scrape",
    "base_url": "https://portal.example/procesos-vigentes",
    "webforms": {
      "dataset_name": "procesos_vigentes",
      "parser": "active_procedures",
      "form_name_prefix": "resultadoView:listadoProcedimientosForm",
      "portlet_prefix": "Pluto__ExamplePortlet",
      "page_size_field_suffix": "resultadosItems",
      "page_size_value": "CIEN",
      "link_hidden_field_suffix": "_link_hidden_",
      "max_pages": 10,
      "request_timeout_seconds": 45,
      "max_request_retries": 3,
      "retry_backoff_seconds": 5
    }
  },
  "files": {
    "required": ["procesos_vigentes"],
    "optional": []
  }
}
```

También pueden configurarse `next_link_template`, `page_text_pattern` y
`user_agent` cuando la plataforma equivalente usa identificadores distintos.

El parser `active_procedures` espera la misma tabla y etiquetas en español:
estado, código, publicación, cierre, última actualización, institución,
categorías y descripción. Si otro portal usa HTML o etiquetas diferentes,
debe agregarse un parser nuevo al registro `PARSERS`; el motor de sesión,
reintentos y paginación sigue siendo el mismo.

Si la salida conserva la forma `active_procedures`, la nueva fuente selecciona
`transform.adapter: active_procedures` y no necesita cambios de routing en
Python. Si el HTML produce otra semántica, debe agregarse un parser o adaptador
reutilizable para ese nuevo formato.
