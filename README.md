# MIRA ETL

ETL configurable para extraer, validar, transformar y cargar datos de contrataciones publicas al modelo minimo de MIRA.

## Conectores

Costa Rica SICOP (descarga ZIP periodica):

```text
https://dlsaobservatorioprod.blob.core.windows.net/fs-synapse-observatorio-produccion/Zip/{AAAAMM}.zip
```

Honduras ONCAE / HonduCompras (OCDS publicado en el OCP Data Registry, un
archivo JSON Lines comprimido por año -- ver `docs/honduras_oncae_mapping.md`):

```text
https://data.open-contracting.org/en/publication/122/download?name={AAAA}.jsonl.gz
```

Nicaragua SISCAE (scraping HTML con sesion, sin API; solo Procesos Vigentes por ahora -- ver `docs/nicaragua_siscae_mapping.md`):

```text
https://www.gestion.nicaraguacompra.gob.ni/siscae/portal/adquisiciones-gestion/busquedaProcedimientosVigentes
```

## Uso

Instalar Python 3.11+ y dependencias:

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .
```

## Scripts automatizados

Los scripts individuales están en `scripts/` para Bash (`.sh`) y PowerShell
(`.ps1`): instalación, pruebas, inicialización de BD y ejecución de un país.

En VS Code no es necesario escribir comandos: abre `Terminal → Run Task` y
selecciona una tarea `MIRA: ...`. VS Code mostrará menús para escoger el país y
solicitará los períodos o el límite cuando corresponda.

Para ejecutar toda la cadena con Guatemala y Costa Rica por período, y
Nicaragua con el estado vigente actual:

```bash
./scripts/run_chain.sh 202607 202607
```

```powershell
.\scripts\run_chain.ps1 -GuatemalaPeriod 202607 -CostaRicaPeriod 202607
```

El tercer parámetro de Bash, o `-Limit` en PowerShell, limita registros para
una prueba. Ejemplo:

```bash
./scripts/run_chain.sh 202607 202607 2
```

```powershell
.\scripts\run_chain.ps1 -GuatemalaPeriod 202607 -CostaRicaPeriod 202607 -Limit 2
```

Ejecutar con ZIP local (Costa Rica):

```powershell
$env:SUPABASE_DB_URL="postgresql://..."
.venv\Scripts\mira-etl run --source costa_rica_sicop --period 202001 --local-zip 202001.zip
```

Ejecutar descargando desde la fuente (Costa Rica):

```powershell
$env:SUPABASE_DB_URL="postgresql://..."
.venv\Scripts\mira-etl run --source costa_rica_sicop --period 202001
```

Ejecutar un rango mensual inclusivo para un país histórico:

```powershell
.venv\Scripts\mira-etl run --source costa_rica_sicop --period "202501 - 202512"
```

También se acepta el formato compacto `202501-202512`. Cada mes se procesa
como una corrida independiente y queda registrado por separado en auditoría.
Un rango no puede combinarse con `--local-zip`, porque cada período requiere
su propio archivo o descarga.

Ejecutar el conector de Nicaragua (no usa `--local-zip` ni `--period`; SISCAE
siempre trae el estado vigente al momento de ejecutar):

```powershell
$env:SUPABASE_DB_URL="postgresql://..."
.venv\Scripts\mira-etl run --source nicaragua_siscae
```

Ejecutar el conector de Honduras (descarga el `.jsonl.gz` del año y carga solo el mes indicado):

```powershell
$env:SUPABASE_DB_URL="postgresql://..."
.venv\Scripts\mira-etl run --source honduras_oncae --period 202410
```

Si ya descargaste el archivo anual a mano, `--local-zip` acepta ese `.jsonl.gz` y evita volver a bajarlo:

```powershell
.venv\Scripts\mira-etl run --source honduras_oncae --period 202410 --local-zip 2024.jsonl.gz
```

Las fuentes `http_zip_json` (Guatemala) se leen incrementalmente con `ijson`, y las `http_jsonl_gz` (Honduras) línea por línea sobre el gzip; ambas cargan en lotes. Otros países pueden reutilizar cualquiera de los dos mecanismos con su propio transformador. El tamaño del lote se configura por entorno:

```powershell
$env:MIRA_JSON_BATCH_SIZE="250"
```

Si no se define, el ETL usa `250`. En cualquier conector, `--limit N` limita
registros durante pruebas puntuales.

Cada país se ejecuta individualmente. Guatemala, Honduras y Costa Rica usan
`--period` para seleccionar la descarga histórica; en Honduras el archivo
publicado es anual, así que el ETL descarga el año y filtra el mes pedido.
Nicaragua es un flujo separado: SISCAE
solo expone el estado vigente al momento de ejecutar, por lo que el ETL etiqueta
la corrida automáticamente con el mes actual.

Crear esquemas/tablas:

```powershell
.venv\Scripts\mira-etl init-db
```

`init-db` crea o actualiza las tablas sin borrar datos y valida que el esquema
coincida con las tablas y columnas utilizadas por `db.py`. Cada corrida `run`
repite esa validación antes de insertar su primer registro.

## Flujo

```text
n8n / manual
  -> GitHub Actions workflow_dispatch
  -> Python ETL
  -> Supabase PostgreSQL
     raw -> staging -> mart -> audit
```
