-- Base data required by the public app. Keep this file idempotent: it should
-- be safe to run repeatedly through `mira-etl init-db`.
--
-- Add stable catalog records here when they are part of the application's
-- baseline database state rather than data extracted by a connector. Countries
-- are the first key catalog because the UI reads them from `web.countries`.

insert into web.countries (
    country_code,
    display_name,
    flag_asset,
    sort_order
)
values
    ('GT', 'Guatemala', '/flags/gt.svg', 10),
    ('HN', 'Honduras', '/flags/hn.svg', 20),
    ('CR', 'Costa Rica', '/flags/cr.svg', 30),
    ('NI', 'Nicaragua', '/flags/ni.svg', 50)
on conflict (country_code) do update set
    display_name = excluded.display_name,
    flag_asset = excluded.flag_asset,
    sort_order = excluded.sort_order;

-- Semantic dictionary consumed by MIRA-API when building SQL-generation
-- prompts. This seed replaces the full dictionary so retired query-contract
-- columns do not linger.
delete from query.semantic_dictionary;

insert into query.semantic_dictionary
    (view_name, column_name, description_es, data_type, enum_values, unit, is_aggregable, caveat)
values
    ('query.v_process', 'process_id', 'Identificador estable del procedimiento de contratacion. Une con query.v_awards.process_id para ver adjudicaciones y montos adjudicados.', 'text', null, null, false, null),
    ('query.v_process', 'country_code', 'Pais de origen del proceso (codigo ISO de dos letras).', 'text', array['CR','GT','HN','NI'], null, false, null),
    ('query.v_process', 'source_system', 'Sistema de contrataciones de origen (por ejemplo costa_rica_sicop).', 'text', null, null, false, null),
    ('query.v_process', 'data_quality_status', 'Que tan completa quedo la fila despues de normalizar el dato fuente.', 'text', array['COMPLETE','PARTIAL','INVALID','DUPLICATE'], null, false, null),
    ('query.v_process', 'source_url', 'Enlace al registro publicado por la fuente cuando esta disponible.', 'text', null, null, false, null),
    ('query.v_process', 'extracted_at', 'Momento en que MIRA extrajo el registro de la fuente.', 'timestamptz', null, null, false, null),
    ('query.v_process', 'source_last_modified_at', 'Ultima modificacion informada por la fuente cuando esta disponible.', 'timestamptz', null, null, false, null),
    ('query.v_process', 'normalised_at', 'Momento en que el ETL proceso esta fila por ultima vez.', 'timestamptz', null, null, false, null),
    ('query.v_process', 'missing_fields', 'Campos esperados que no estaban disponibles en el registro fuente.', 'jsonb', null, null, false, null),
    ('query.v_process', 'process_number', 'Numero de expediente o procedimiento tal como lo publica la fuente.', 'text', null, null, false, null),
    ('query.v_process', 'title', 'Titulo del procedimiento.', 'text', null, null, false, null),
    ('query.v_process', 'description', 'Descripcion del procedimiento.', 'text', null, null, false, null),
    ('query.v_process', 'procurement_method', 'Modalidad de contratacion tal como la nombra la fuente (texto libre, no catalogado).', 'text', null, null, false, 'No es un catalogo cerrado: la misma modalidad puede aparecer escrita distinto entre paises.'),
    ('query.v_process', 'process_status', 'Estado normalizado del procedimiento.', 'text', array['PLANNED','PUBLISHED','OPEN','EVALUATION','AWARDED','CONTRACTED','COMPLETED','CANCELLED','DESERTED','SUSPENDED'], null, false, null),
    ('query.v_process', 'source_status', 'Estado tal como lo escribe la fuente, antes de normalizar.', 'text', null, null, false, null),
    ('query.v_process', 'publication_date', 'Fecha de publicacion del procedimiento.', 'timestamptz', null, null, false, null),
    ('query.v_process', 'closing_date', 'Fecha de cierre de recepcion de ofertas.', 'timestamptz', null, null, false, null),
    ('query.v_process', 'estimated_amount', 'Monto estimado antes de adjudicar, en la moneda de currency_code.', 'numeric', null, 'currency_code', true, 'No es gasto real ni monto adjudicado. Para compras, gasto o adjudicaciones usa query.v_awards.awarded_amount unido por process_id. Nunca sumar procesos con currency_code distinto sin convertir primero.'),
    ('query.v_process', 'currency_code', 'Moneda del monto estimado.', 'text', null, null, false, null),

    ('query.v_buyers', 'buyer_id', 'Identificador estable de la entidad compradora.', 'bigint', null, null, false, null),
    ('query.v_buyers', 'country_code', 'Pais de la entidad compradora.', 'text', array['CR','GT','HN','NI'], null, false, null),
    ('query.v_buyers', 'source_system', 'Sistema de contrataciones donde se registro esta entidad.', 'text', null, null, false, null),
    ('query.v_buyers', 'name_normalised', 'Nombre publicado de la entidad, normalizado unicamente en Unicode y espacios.', 'text', null, null, false, 'Conserva mayusculas, acentos, puntuacion y sufijos legales; puede mostrarse al usuario.'),
    ('query.v_buyers', 'buyer_tax_id', 'Identificador fiscal (cedula juridica u equivalente) cuando la fuente lo expone.', 'text', null, null, false, null),

    ('query.v_suppliers', 'supplier_id', 'Identificador estable de la entidad proveedora.', 'bigint', null, null, false, null),
    ('query.v_suppliers', 'country_code', 'Pais de la entidad proveedora.', 'text', array['CR','GT','HN','NI'], null, false, null),
    ('query.v_suppliers', 'source_system', 'Sistema de contrataciones donde se registro esta entidad.', 'text', null, null, false, null),
    ('query.v_suppliers', 'name_normalised', 'Nombre publicado de la entidad, normalizado unicamente en Unicode y espacios.', 'text', null, null, false, 'Conserva mayusculas, acentos, puntuacion y sufijos legales; puede mostrarse al usuario.'),
    ('query.v_suppliers', 'supplier_tax_id', 'Identificador fiscal (cedula fisica o juridica) cuando la fuente lo expone.', 'text', null, null, false, null),
    ('query.v_suppliers', 'supplier_type', 'Tipo de proveedor.', 'text', array['PERSON','COMPANY','CONSORTIUM','NONPROFIT','PUBLIC_ENTITY','FOREIGN_SUPPLIER','UNKNOWN'], null, false, null),

    ('query.v_process_buyers', 'process_id', 'Proceso relacionado con el comprador.', 'text', null, null, false, null),
    ('query.v_process_buyers', 'buyer_id', 'Comprador relacionado con el proceso.', 'bigint', null, null, false, null),

    ('query.v_items', 'item_id', 'Identificador estable de la linea o articulo.', 'text', null, null, false, null),
    ('query.v_items', 'process_id', 'Procedimiento al que pertenece la linea o articulo.', 'text', null, null, false, null),
    ('query.v_items', 'source_item_id', 'Identificador del articulo publicado por la fuente.', 'text', null, null, false, null),
    ('query.v_items', 'line_number', 'Numero de linea publicado por la fuente.', 'text', null, null, false, null),
    ('query.v_items', 'item_description', 'Descripcion del bien o servicio.', 'text', null, null, false, null),
    ('query.v_items', 'category_source', 'Categoria del bien o servicio publicada por la fuente.', 'text', null, null, false, null),
    ('query.v_items', 'category_normalised', 'Categoria regional normalizada cuando esta disponible.', 'text', null, null, false, null),

    ('query.v_awards', 'award_id', 'Identificador estable de la adjudicacion.', 'text', null, null, false, null),
    ('query.v_awards', 'process_id', 'Procedimiento al que pertenece la adjudicacion. Une con query.v_process.process_id para filtrar por pais, titulo, descripcion, fecha de publicacion o modalidad.', 'text', null, null, false, null),
    ('query.v_awards', 'source_award_id', 'Identificador de adjudicacion publicado por la fuente.', 'text', null, null, false, null),
    ('query.v_awards', 'award_date', 'Fecha de la adjudicacion.', 'timestamptz', null, null, false, null),
    ('query.v_awards', 'awarded_amount', 'Monto de esta adjudicacion en su moneda original. Es el monto correcto para preguntas sobre compras, gasto, montos adjudicados o rankings por valor.', 'numeric', null, 'currency_code', true, 'No sumar adjudicaciones con monedas diferentes sin convertirlas.'),
    ('query.v_awards', 'currency_code', 'Moneda del monto adjudicado.', 'text', null, null, false, null),

    ('query.v_award_items', 'award_id', 'Adjudicacion relacionada con el articulo.', 'text', null, null, false, null),
    ('query.v_award_items', 'item_id', 'Articulo relacionado con la adjudicacion.', 'text', null, null, false, null),

    ('query.v_award_suppliers', 'award_id', 'Adjudicacion relacionada con el proveedor.', 'text', null, null, false, null),
    ('query.v_award_suppliers', 'supplier_id', 'Proveedor relacionado con la adjudicacion.', 'bigint', null, null, false, null),

    ('query.v_coverage', 'source_system', 'Sistema de contrataciones de la corrida del ETL (igual valor que v_process.source_system; no es un codigo de pais).', 'text', null, null, false, null),
    ('query.v_coverage', 'period', 'Periodo que proceso esa corrida, tal como lo registro el ETL.', 'text', null, null, false, 'Texto libre, no un rango tipado -- no asumir un formato fijo sin confirmarlo.'),
    ('query.v_coverage', 'status', 'Resultado de la corrida. Esta vista solo incluye corridas SUCCESS.', 'text', array['SUCCESS'], null, false, null),
    ('query.v_coverage', 'loaded_at', 'Momento en que termino esa corrida del ETL.', 'timestamptz', null, null, false, null),
    ('query.v_coverage', 'table_name', 'Tabla que esa corrida cargo.', 'text', null, null, false, null),
    ('query.v_coverage', 'row_count', 'Cuantas filas escribio el ETL en esa tabla durante esa corrida.', 'bigint', null, null, true, 'Un pais/periodo ausente de esta vista no tiene datos cargados -- distinto de un conteo real en cero.')
on conflict (view_name, column_name) do update set
    description_es = excluded.description_es,
    data_type = excluded.data_type,
    enum_values = excluded.enum_values,
    unit = excluded.unit,
    is_aggregable = excluded.is_aggregable,
    caveat = excluded.caveat;
