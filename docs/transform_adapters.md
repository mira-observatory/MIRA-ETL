# Adaptadores de transformación

Las transformaciones se seleccionan por formato de fuente, no por país. El
pipeline lee `transform.adapter` del archivo `config/sources/<fuente>.json` y
no contiene una lista de nombres como `costa_rica_sicop` o
`guatemala_guatecompras`.

Adaptadores disponibles:

| Adapter | Entrada esperada |
|---|---|
| `relational_awards_csv` | CSV relacionados de procesos, adjudicaciones, compradores y proveedores |
| `ocds` | Registros OCDS JSON (`compiledRelease`, tender, awards, contracts y parties) |
| `active_procedures` | Filas normalizadas del extractor HTML de procesos activos |

Ejemplo:

```json
{
  "source": "otra_fuente_ocds",
  "country_code": "SV",
  "source_system": "Portal OCDS El Salvador",
  "connector_version": "sv-ocds-0.1.0",
  "transform": {
    "adapter": "ocds",
    "id_prefix": "MIRA-SV-OCDS-"
  }
}
```

Una fuente nueva que respete uno de estos contratos solo necesita su archivo
JSON. Puede coexistir con otras fuentes del mismo país porque `source` y
`source_system` identifican el origen, mientras `country_code` identifica el
país.

Las opciones que realmente cambian por fuente —prefijos de IDs, nombres de
datasets y mapas de estados— deben permanecer en el JSON. Los algoritmos que
requieren joins, agrupación de líneas o recorrido de OCDS permanecen en Python
dentro de `src/mira_etl/adapters/`.

Si aparece un formato de entrada nuevo, se agrega un adaptador reutilizable al
registro de `adapters/__init__.py`. A partir de entonces, todas las fuentes con
ese formato vuelven a requerir únicamente configuración.
