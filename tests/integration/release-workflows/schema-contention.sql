-- Read-only catalog evidence. The holder value is a psql quoted variable.
SELECT json_build_object(
 'observedAt',clock_timestamp(),
 'databaseOID',(SELECT oid::text FROM pg_database WHERE datname=current_database()),
 'schemaAbsent',to_regnamespace('perfeng_control') IS NULL,
 'applicationRelations',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname IN ('public','perfeng_control') AND c.relkind IN ('r','i','S')),
 'sessions',(SELECT coalesce(json_agg(json_build_object(
   'pid',pid,'backendStart',backend_start,'user',usename,'database',datname,
   'application',application_name,'clientAddress',host(client_addr),'backendType',backend_type,
   'state',state,'waitType',wait_event_type,'waitEvent',wait_event,'query',query,
   'blockers',pg_blocking_pids(pid)) ORDER BY pid),'[]'::json)
   FROM pg_stat_activity WHERE datname=current_database()
   AND application_name IN (:'holder','perfeng-schema-migration-v1')),
 'locks',(SELECT coalesce(json_agg(json_build_object(
   'pid',pid,'databaseOID',database::text,'classid',classid::text,'objid',objid::text,
   'objsubid',objsubid,'mode',mode,'granted',granted) ORDER BY pid),'[]'::json)
   FROM pg_locks WHERE locktype='advisory' AND objsubid=1
   AND ((classid::bigint << 32) | objid::bigint)=70657266656
   AND database=(SELECT oid FROM pg_database WHERE datname=current_database())));
