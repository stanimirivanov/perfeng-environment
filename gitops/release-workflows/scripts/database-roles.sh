host=postgres.perf-platform.svc.cluster.local
migration_password=${MIGRATION_URL#postgresql://perfeng_migration:}
migration_password=${migration_password%@postgres.perf-platform.svc.cluster.local:5432/perfeng?sslmode=disable}
runtime_password=${RUNTIME_URL#postgresql://perfeng_runtime:}
runtime_password=${runtime_password%@postgres.perf-platform.svc.cluster.local:5432/perfeng?sslmode=disable}
test "$MIGRATION_URL" = "postgresql://perfeng_migration:${migration_password}@${host}:5432/perfeng?sslmode=disable"
test "$RUNTIME_URL" = "postgresql://perfeng_runtime:${runtime_password}@${host}:5432/perfeng?sslmode=disable"
for password in "$migration_password" "$runtime_password"; do
  test "${#password}" -eq 43
  case "$password" in *[!A-Za-z0-9_-]*) exit 1;; esac
done
export PGHOST="$host" PGUSER=postgres PGDATABASE=perfeng
unsafe=$(psql -X -w -At -v ON_ERROR_STOP=1 -c \
  "SELECT count(*) FROM pg_roles WHERE rolname IN ('perfeng_migration','perfeng_runtime') AND (NOT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)")
test "$unsafe" = 0 || { echo 'Existing database role has unsafe attributes' >&2; exit 1; }
psql -X -w -q -v ON_ERROR_STOP=1 <<SQL
BEGIN;
SELECT format('CREATE ROLE perfeng_migration LOGIN PASSWORD %L', '${migration_password}')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'perfeng_migration') \gexec
SELECT format('CREATE ROLE perfeng_runtime LOGIN PASSWORD %L', '${runtime_password}')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'perfeng_runtime') \gexec
REVOKE CREATE ON DATABASE perfeng FROM PUBLIC;
GRANT CONNECT ON DATABASE perfeng TO perfeng_migration, perfeng_runtime;
GRANT CREATE ON DATABASE perfeng TO perfeng_migration;
REVOKE CREATE ON DATABASE perfeng FROM perfeng_runtime;
COMMIT;
SQL
roles=$(psql -X -w -At -v ON_ERROR_STOP=1 -c \
  "SELECT count(*) FROM pg_roles WHERE rolname IN ('perfeng_migration','perfeng_runtime') AND rolcanlogin AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)")
test "$roles" = 2
privileges=$(psql -X -w -At -v ON_ERROR_STOP=1 -c \
  "SELECT has_database_privilege('perfeng_migration','perfeng','CONNECT'), has_database_privilege('perfeng_migration','perfeng','CREATE'), has_database_privilege('perfeng_runtime','perfeng','CONNECT'), has_database_privilege('perfeng_runtime','perfeng','CREATE')")
test "$privileges" = 't|t|t|f'
unset PGPASSWORD
test "$(PGPASSWORD="$migration_password" psql -X -w -At -h "$host" -U perfeng_migration -d perfeng -v ON_ERROR_STOP=1 -c 'SELECT 1')" = 1
test "$(PGPASSWORD="$runtime_password" psql -X -w -At -h "$host" -U perfeng_runtime -d perfeng -v ON_ERROR_STOP=1 -c 'SELECT 1')" = 1
echo 'Database roles and logins verified without credential rotation'
