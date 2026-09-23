-- Refresh the constraint for existing databases as well as new installations.
alter table analytics.query_log
    drop constraint if exists query_log_outcome_check,
    add constraint query_log_outcome_check check (outcome in (
        'OK', 'OK_ZERO_ROWS', 'OK_DEGRADED_NARRATIVE',
        'OUT_OF_SCOPE', 'REJECTED_ENTITY_NOT_FOUND', 'REJECTED_ENTITY_AMBIGUOUS',
        'REJECTED_QUESTION_TOO_BROAD', 'REJECTED_INTENT_UNCLEAR',
        'REJECTED_SQL_PARSE', 'REJECTED_SQL_NOT_SELECT', 'REJECTED_SQL_RELATION',
        'REJECTED_SQL_FUNCTION', 'REJECTED_SQL_COST', 'REJECTED_SQL_COUNTRY_SCOPE',
        'FAILED_DB_TIMEOUT', 'FAILED_DB_ERROR', 'FAILED_LLM_ERROR',
        'THROTTLED_QUOTA', 'THROTTLED_BUDGET'
    ));

-- Refresh the constraint for existing databases as well as new installations.
alter table analytics.query_attempt
    drop constraint if exists query_attempt_outcome_check,
    add constraint query_attempt_outcome_check check (outcome in (
        'OK', 'OK_ZERO_ROWS', 'OK_DEGRADED_NARRATIVE',
        'OUT_OF_SCOPE', 'REJECTED_ENTITY_NOT_FOUND', 'REJECTED_ENTITY_AMBIGUOUS',
        'REJECTED_QUESTION_TOO_BROAD', 'REJECTED_INTENT_UNCLEAR',
        'REJECTED_SQL_PARSE', 'REJECTED_SQL_NOT_SELECT', 'REJECTED_SQL_RELATION',
        'REJECTED_SQL_FUNCTION', 'REJECTED_SQL_COST', 'REJECTED_SQL_COUNTRY_SCOPE',
        'FAILED_DB_TIMEOUT', 'FAILED_DB_ERROR', 'FAILED_LLM_ERROR',
        'THROTTLED_QUOTA', 'THROTTLED_BUDGET'
    ));

-- Indexes used by ETL relationship lookups and citizen-facing query shapes.
-- Analytics log tables deliberately have no secondary indexes; quota_counters
-- already has the primary-key index required by its runtime read/update path.
create index if not exists idx_processes_country
    on mart.processes (country_code);

-- Public catalog: retrieve the requested page without sorting every process,
-- and count normalized statuses using a narrow index.
create index if not exists idx_processes_publication_page
    on mart.processes (publication_date desc nulls last, process_id);

create index if not exists idx_processes_status
    on mart.processes (process_status);

-- Fuzzy entity search (MIRA-API resolves a typed name to a supplier/buyer
-- candidate list). name_normalised keeps the published casing/accents, so
-- matching has to fold case and accents at query time -- it is never folded
-- when the row is written. unaccent() is STABLE, not IMMUTABLE, so it cannot
-- be used directly in an index expression; this wrapper pins the dictionary
-- by name to make it safely IMMUTABLE.
--
-- Lives in `query`, not `mart`: mira_query only has USAGE on `query` (see
-- docs/database_security.md), so MIRA-API's own runtime queries have to call
-- this same function to match the index expression below. A copy in `mart`
-- would build the index fine but be unreachable at query time -- mira_query
-- would get "permission denied for schema mart" the moment it tried to call
-- it directly (querying *through* a view doesn't need this, but constructing
-- a WHERE/ORDER BY with this function by name does).
create extension if not exists pg_trgm;
create extension if not exists unaccent;

-- The catalog searches each field independently with literal ILIKE substrings.
create index if not exists idx_processes_number_trgm
    on mart.processes using gin (process_number gin_trgm_ops);

create index if not exists idx_processes_title_trgm
    on mart.processes using gin (title gin_trgm_ops);

create index if not exists idx_processes_description_trgm
    on mart.processes using gin (description gin_trgm_ops);

create or replace function query.f_unaccent(text)
returns text
language sql
immutable
parallel safe
as $$
    select public.unaccent('public.unaccent', coalesce($1, ''))
$$;

create index if not exists idx_suppliers_name_trgm
    on mart.suppliers using gin (lower(query.f_unaccent(name_normalised)) gin_trgm_ops);

create index if not exists idx_buyers_name_trgm
    on mart.buyers using gin (lower(query.f_unaccent(name_normalised)) gin_trgm_ops);

create index if not exists idx_items_process
    on mart.items (process_id);

create index if not exists idx_awards_process
    on mart.awards (process_id);

create index if not exists idx_awards_award_date
    on mart.awards (award_date);

create index if not exists idx_award_suppliers_supplier
    on mart.award_suppliers (supplier_id);

-- FK checks when replacing mart.items must find references by item_id.
-- The (award_id, item_id) primary key does not serve this lookup efficiently.
create index if not exists idx_award_items_item
    on mart.award_items (item_id);

create index if not exists idx_process_buyers_buyer_id
    on mart.process_buyers (buyer_id);

create index if not exists idx_buyers_country
    on mart.buyers (country_code);

create index if not exists idx_suppliers_country
    on mart.suppliers (country_code);

create index if not exists idx_audit_validation_results_run
    on audit.validation_results (run_id, severity, rule_code);

-- One row per procurement process. Items, awards, buyers, and suppliers are
-- exposed separately so joins never multiply monetary values accidentally.
create or replace view query.v_process as
select
    process_id, country_code, source_system, data_quality_status, source_url,
    extracted_at, source_last_modified_at, normalised_at, missing_fields,
    process_number, title, description, procurement_method, process_status,
    source_status, publication_date, closing_date, estimated_amount, currency_code
from mart.processes;

create or replace view query.v_buyers as
select
    buyer_id,
    country_code,
    source_system,
    name_normalised,
    buyer_tax_id
from mart.buyers;

create or replace view query.v_suppliers as
select
    supplier_id,
    country_code,
    source_system,
    name_normalised,
    supplier_tax_id,
    supplier_type
from mart.suppliers;

create or replace view query.v_process_buyers as
select process_id, buyer_id
from mart.process_buyers;

create or replace view query.v_items as
select
    item_id,
    process_id,
    source_item_id,
    line_number,
    item_description,
    category_source,
    category_normalised
from mart.items;

-- Keep this upgrade in the existing schema/index/view files. Sources which
-- do not publish an award status leave it NULL; the process status is then
-- the available evidence. Never assign an invented "active" status.
alter table mart.awards add column if not exists award_status text;

-- Recover statuses for already loaded OCDS awards from their retained source
-- payload, without downloading or rebuilding the procurement data. Some
-- sources publish contracts without an awards section, as the adapter supports.
with source_awards as (
    select p.process_id, entry.value,
           nullif(lower(btrim(entry.value->>'status')), '') as award_status
    from mart.processes p
    cross join lateral (
        select coalesce(p.raw_payload->'compiledRelease', p.raw_payload) as release
    ) source
    cross join lateral jsonb_array_elements(
        case
            when jsonb_typeof(source.release->'awards') = 'array'
                 and source.release->'awards' <> '[]'::jsonb
                then source.release->'awards'
            when jsonb_typeof(source.release->'contracts') = 'array'
                then source.release->'contracts'
            else '[]'::jsonb
        end
    ) entry
)
update mart.awards a
set award_status = s.award_status
from source_awards s
where s.process_id = a.process_id
  and coalesce(s.value->>'id', s.value->>'awardID') = a.source_award_id
  and a.award_status is null
  and s.award_status is not null;

-- Full source records, only for explicit requests about excluded states.
create or replace view query.v_awards_all as
select
    a.award_id,
    a.process_id,
    a.source_award_id,
    a.award_date,
    a.awarded_amount,
    a.currency_code,
    a.award_status,
    p.process_status,
    p.data_quality_status,
    p.normalisation_status,
    coalesce(
        p.process_status in ('AWARDED', 'CONTRACTED', 'COMPLETED')
        and p.data_quality_status in ('COMPLETE', 'PARTIAL')
        and p.normalisation_status = 'PROCESSED'
        and (a.award_status is null or lower(btrim(a.award_status)) in ('active', 'complete'))
        and (a.awarded_amount is null or a.awarded_amount >= 0),
        false
    ) as is_valid_award
from mart.awards a
join mart.processes p on p.process_id = a.process_id;

-- Filter BEFORE ORDER BY/LIMIT/COUNT: a cancelled highest amount must never
-- win an ordinary ranking, even if generated SQL forgets the status filter.
-- This represents valid awards according to the source, not proof of payment
-- or completed execution of a contract.
create or replace view query.v_awards as
select * from query.v_awards_all where is_valid_award;

create or replace view query.v_award_items as
select award_id, item_id
from mart.award_items;

create or replace view query.v_award_suppliers as
select award_id, supplier_id
from mart.award_suppliers;

-- What source/period the ETL actually loaded, and how the run finished.
-- MIRA-API needs this to tell "zero rows because nothing was awarded" apart
-- from "zero rows because this period was never loaded" -- a bare count over
-- v_process cannot make that distinction on its own. audit.etl_runs.source
-- holds the source system identifier (e.g. "costa_rica_sicop"), the same
-- value as v_process.source_system -- not an ISO country code, so this joins
-- on source_system, not country_code.
create or replace view query.v_coverage as
select
    r.source as source_system,
    r.period,
    r.status,
    r.finished_at as loaded_at,
    rc.table_name,
    rc.row_count
from audit.etl_runs r
left join audit.etl_row_counts rc on rc.run_id = r.id
where r.status = 'SUCCESS';
