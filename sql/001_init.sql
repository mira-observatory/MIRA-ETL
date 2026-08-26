create schema if not exists raw;
create schema if not exists staging;
create schema if not exists mart;
create schema if not exists audit;
create schema if not exists query;
create schema if not exists analytics;
create schema if not exists web;

create table if not exists audit.etl_runs (
    id bigserial primary key,
    pipeline_name text,
    source text not null,
    period text not null,
    connector_version text not null,
    status text not null check (status in ('RUNNING', 'SUCCESS', 'ERROR')),
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    error_message text
);

create table if not exists audit.etl_row_counts (
    row_count_id bigserial primary key,
    run_id bigint not null references audit.etl_runs(id),
    layer_name text not null,
    table_name text not null,
    row_count bigint not null,
    created_at timestamptz not null default now()
);

create table if not exists audit.etl_errors (
    error_id bigserial primary key,
    run_id bigint references audit.etl_runs(id),
    source text,
    period text,
    filename text,
    row_number bigint,
    error_message text not null,
    payload jsonb,
    created_at timestamptz not null default now()
);

create table if not exists raw.source_files (
    source_file_id bigserial primary key,
    run_id bigint not null references audit.etl_runs(id),
    source text not null,
    period text not null,
    filename text not null,
    file_hash text not null,
    row_count bigint not null,
    loaded_at timestamptz not null default now()
);

create table if not exists raw.source_rows (
    source_row_id bigserial primary key,
    run_id bigint not null references audit.etl_runs(id),
    source_file_id bigint not null references raw.source_files(source_file_id),
    row_number bigint not null,
    payload jsonb not null,
    loaded_at timestamptz not null default now()
);

create table if not exists staging.normalized_candidates (
    candidate_id bigserial primary key,
    run_id bigint not null references audit.etl_runs(id),
    source text not null,
    period text not null,
    source_record_id text not null,
    raw_payload_hash text not null,
    payload jsonb not null,
    created_at timestamptz not null default now()
);

create table if not exists mart.processes (
    process_id text primary key,
    process_number text,
    title text,
    description text,
    procurement_method text,
    process_status text check (
        process_status is null or process_status in (
            'PLANNED', 'PUBLISHED', 'OPEN', 'EVALUATION', 'AWARDED',
            'CONTRACTED', 'COMPLETED', 'CANCELLED', 'DESERTED', 'SUSPENDED'
        )
    ),
    source_status text,
    publication_date timestamptz,
    closing_date timestamptz,
    estimated_amount numeric,
    currency_code text,
    country_code text not null,
    source_system text not null,
    source_record_id text not null,
    source_url text,
    extracted_at timestamptz not null,
    source_last_modified_at timestamptz,
    connector_version text not null,
    raw_payload jsonb not null,
    raw_payload_hash text not null,
    normalisation_status text not null check (
        normalisation_status in ('PENDING', 'PROCESSED', 'ERROR', 'REVIEW_REQUIRED')
    ),
    normalised_at timestamptz not null,
    data_quality_status text not null check (
        data_quality_status in ('COMPLETE', 'PARTIAL', 'INVALID', 'DUPLICATE')
    ),
    missing_fields jsonb not null default '[]'::jsonb,

    unique (source_system, source_record_id, raw_payload_hash)
);

create table if not exists mart.items (
    item_id text primary key,
    process_id text not null references mart.processes(process_id),
    source_item_id text,
    line_number text,
    item_description text,
    category_source text,
    category_normalised text,
    unique (process_id, source_item_id, line_number)
);

create table if not exists audit.validation_results (
    validation_id bigserial primary key,
    run_id bigint not null references audit.etl_runs(id),
    source text not null,
    period text not null,
    source_record_id text,
    raw_payload_hash text,
    rule_code text not null,
    severity text not null check (severity in ('ERROR', 'WARNING', 'INFO')),
    field_name text,
    raw_value text,
    normalised_value text,
    message text not null,
    payload jsonb,
    created_at timestamptz not null default now()
);

-- Dimension tables resolve the same supplier or buyer across procurement
-- processes to one stable entity. See docs/entity_matching.md.

create table if not exists mart.suppliers (
    supplier_id bigserial primary key,
    country_code text not null,
    source_system text not null,
    supplier_tax_id text,
    supplier_id_source text,
    name_normalised text,
    supplier_type text check (
        supplier_type is null or supplier_type in (
            'PERSON', 'COMPANY', 'CONSORTIUM', 'NONPROFIT',
            'PUBLIC_ENTITY', 'FOREIGN_SUPPLIER', 'UNKNOWN'
        )
    )
);

create table if not exists mart.buyers (
    buyer_id bigserial primary key,
    country_code text not null,
    source_system text not null,
    buyer_tax_id text,
    buyer_id_source text,
    name_normalised text
);

-- A procurement process can be related to multiple buyers.
create table if not exists mart.process_buyers (
    process_id text not null references mart.processes(process_id),
    buyer_id bigint not null references mart.buyers(buyer_id),
    primary key (process_id, buyer_id)
);

-- Awards carry amounts once; suppliers and items attach through bridge tables
-- so multi-supplier or multi-item awards do not duplicate monetary values.
create table if not exists mart.awards (
    award_id text primary key,
    process_id text not null references mart.processes(process_id),
    source_award_id text,
    award_date timestamptz,
    awarded_amount numeric,
    currency_code text
);

create table if not exists mart.award_items (
    award_id text not null references mart.awards(award_id),
    item_id text not null references mart.items(item_id),
    primary key (award_id, item_id)
);

create table if not exists mart.award_suppliers (
    award_id text not null references mart.awards(award_id),
    supplier_id bigint not null references mart.suppliers(supplier_id),
    primary key (award_id, supplier_id)
);

-- Exact, source-grain summary consumed by fixed public API endpoints. This is
-- deliberately outside `query`: generated SQL has no access to the `web`
-- schema. Countries are catalogued separately so the public UI can add planned
-- coverage without shipping a frontend change. `flag_asset` is a public asset
-- path such as /flags/gt.svg or an absolute CDN URL.
create table if not exists web.countries (
    country_code text primary key,
    display_name text not null,
    flag_asset text,
    sort_order integer not null default 0
);

-- ACTIVE rows are upserted by the ETL after a connector has loaded data.
-- Countries without ACTIVE sources are considered PLANNED by the API when they
-- exist in web.countries.
create table if not exists web.coverage_sources (
    source_key text primary key,
    country_code text not null,
    source_system text not null,
    display_name text not null,
    status text not null check (status in ('ACTIVE', 'PLANNED', 'INACTIVE')),
    process_count bigint not null default 0 check (process_count >= 0),
    buyer_count bigint not null default 0 check (buyer_count >= 0),
    supplier_count bigint not null default 0 check (supplier_count >= 0),
    publication_date_min date,
    publication_date_max date,
    complete_process_count bigint not null default 0
        check (complete_process_count >= 0),
    partial_process_count bigint not null default 0
        check (partial_process_count >= 0),
    process_without_date_count bigint not null default 0
        check (process_without_date_count >= 0),
    last_successful_load_at timestamptz,
    refreshed_at timestamptz,
    sort_order integer not null default 0,
    unique (country_code, source_system),
    check (
        publication_date_min is null
        or publication_date_max is null
        or publication_date_min <= publication_date_max
    )
);

-- Column-level documentation that MIRA-API injects into the SQL-generation
-- prompt. The seed data lives in sql/003_seed_base_data.sql with the other
-- baseline database data.
create table if not exists query.semantic_dictionary (
    id bigserial primary key,
    view_name text not null,
    column_name text not null,
    description_es text not null,
    data_type text not null,
    enum_values text[],
    unit text,
    is_aggregable boolean not null default false,
    caveat text,
    unique (view_name, column_name)
);

-- MIRA-API interaction log. One parent row holds the question and final/raw
-- response; SQL generation retries are stored in query_attempt below.
create table if not exists analytics.query_log (
    id bigserial primary key,
    created_at timestamptz not null default now(),
    subject_key text not null,
    question_text text not null,
    response_text text,
    model_response_raw jsonb,
    outcome text not null check (outcome in (
        'OK', 'OK_ZERO_ROWS', 'OK_DEGRADED_NARRATIVE',
        'OUT_OF_SCOPE', 'REJECTED_ENTITY_NOT_FOUND', 'REJECTED_ENTITY_AMBIGUOUS',
        'REJECTED_SQL_PARSE', 'REJECTED_SQL_NOT_SELECT', 'REJECTED_SQL_RELATION',
        'REJECTED_SQL_FUNCTION', 'REJECTED_SQL_COST', 'REJECTED_SQL_COUNTRY_SCOPE',
        'FAILED_DB_TIMEOUT', 'FAILED_DB_ERROR', 'FAILED_LLM_ERROR',
        'THROTTLED_QUOTA', 'THROTTLED_BUDGET'
    )),
    attempt_count int not null default 1,
    total_latency_ms int,
    prompt_version text,
    app_version text,
    model_used text
);

create table if not exists analytics.query_attempt (
    id bigserial primary key,
    query_log_id bigint not null references analytics.query_log(id),
    attempt_number int not null,
    generated_sql text,
    outcome text not null check (outcome in (
        'OK', 'OK_ZERO_ROWS', 'OK_DEGRADED_NARRATIVE',
        'OUT_OF_SCOPE', 'REJECTED_ENTITY_NOT_FOUND', 'REJECTED_ENTITY_AMBIGUOUS',
        'REJECTED_SQL_PARSE', 'REJECTED_SQL_NOT_SELECT', 'REJECTED_SQL_RELATION',
        'REJECTED_SQL_FUNCTION', 'REJECTED_SQL_COST', 'REJECTED_SQL_COUNTRY_SCOPE',
        'FAILED_DB_TIMEOUT', 'FAILED_DB_ERROR', 'FAILED_LLM_ERROR',
        'THROTTLED_QUOTA', 'THROTTLED_BUDGET'
    )),
    rejection_rule text,
    rejection_detail text,
    row_count int,
    latency_ms int,
    created_at timestamptz not null default now(),
    unique (query_log_id, attempt_number)
);

-- Runtime quota state. Its primary key is the only index needed by the API's
-- read/update path; analytics log tables intentionally have no extra indexes.
create table if not exists analytics.quota_counters (
    subject_key text not null,
    period_type text not null check (period_type in ('DAY', 'MONTH')),
    period_key text not null,
    query_count int not null default 0,
    spent_usd numeric not null default 0,
    updated_at timestamptz not null default now(),
    primary key (subject_key, period_type, period_key)
);
