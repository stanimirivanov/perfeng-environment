-- CI only: authenticate as the runtime role, verify effective authority, then
-- exercise real reads and forbidden operations inside a rolled-back transaction.
BEGIN;
SET LOCAL statement_timeout = '10s';
DO $policy$
DECLARE
  relation record;
  privilege text;
  allowed boolean;
  denied boolean;
  column_name text;
BEGIN
  IF current_user <> 'perfeng_runtime'
     OR NOT has_schema_privilege(current_user, 'perfeng_control', 'USAGE')
     OR has_schema_privilege(current_user, 'perfeng_control', 'CREATE')
     OR has_database_privilege(current_user, current_database(), 'CREATE') THEN
    RAISE EXCEPTION 'runtime privilege matrix mismatch';
  END IF;
  FOR relation IN
    SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'perfeng_control' AND c.relkind IN ('r', 'p')
  LOOP
    FOREACH privilege IN ARRAY ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] LOOP
      allowed := relation.relname IN ('runs','create_bindings','baselines','artifacts',
        'kubernetes_executions','reconciliation_leases','benchmark_target_reservations')
        AND (privilege IN ('SELECT','INSERT') OR (privilege = 'UPDATE'
          AND relation.relname NOT IN ('artifacts','kubernetes_executions')));
      IF has_table_privilege(current_user, format('perfeng_control.%I', relation.relname), privilege) <> allowed THEN
        RAISE EXCEPTION 'runtime privilege matrix mismatch';
      END IF;
    END LOOP;
    IF relation.relname NOT IN ('runs','create_bindings','baselines','artifacts',
      'kubernetes_executions','reconciliation_leases','benchmark_target_reservations',
      'schema_migrations','native_worker_presence','native_execution_leases',
      'native_upload_grants','native_artifacts','native_terminal_reports','native_worker_presence_v2') THEN
      RAISE EXCEPTION 'unexpected runtime application table';
    END IF;
    IF relation.relname IN ('runs','create_bindings','baselines','artifacts',
      'kubernetes_executions','reconciliation_leases','benchmark_target_reservations') THEN
      EXECUTE format('SELECT 1 FROM perfeng_control.%I LIMIT 1', relation.relname);
      EXECUTE format('INSERT INTO perfeng_control.%I SELECT * FROM perfeng_control.%I WHERE false',
        relation.relname, relation.relname);
      IF relation.relname NOT IN ('artifacts','kubernetes_executions') THEN
        SELECT a.attname INTO STRICT column_name FROM pg_attribute a
        WHERE a.attrelid = format('perfeng_control.%I', relation.relname)::regclass
          AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum LIMIT 1;
        EXECUTE format('UPDATE perfeng_control.%I SET %I = %I WHERE false',
          relation.relname, column_name, column_name);
      END IF;
    END IF;
  END LOOP;
  IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = 'perfeng_control' AND c.relkind IN ('r','p')) <> 14 THEN
    RAISE EXCEPTION 'unexpected runtime application table';
  END IF;
  denied := false;
  BEGIN
    EXECUTE 'CREATE TABLE perfeng_control.runtime_ci_forbidden(id integer)';
  EXCEPTION WHEN insufficient_privilege THEN denied := true;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'runtime DDL unexpectedly allowed'; END IF;
  denied := false;
  BEGIN
    -- Even a zero-row DELETE must be refused; no retained application data is needed.
    EXECUTE 'DELETE FROM perfeng_control.runs WHERE false';
  EXCEPTION WHEN insufficient_privilege THEN denied := true;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'runtime DELETE unexpectedly allowed'; END IF;
  denied := false;
  BEGIN
    PERFORM 1 FROM perfeng_control.schema_migrations LIMIT 1;
  EXCEPTION WHEN insufficient_privilege THEN denied := true;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'runtime ledger read unexpectedly allowed'; END IF;
END;
$policy$;
ROLLBACK;
SELECT 'runtime privilege policy verified';
