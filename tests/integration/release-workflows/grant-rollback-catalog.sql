-- Full fixture definitions and quiescence; exclude only this read/cleanup session.
SELECT jsonb_build_object(
 'observedAt',clock_timestamp(),
 'databaseOID',(SELECT oid::text FROM pg_database WHERE datname=current_database()),
 'schemaOID',to_regnamespace('perfeng_control')::oid::text,
 'schemaUsage',has_schema_privilege('perfeng_runtime','perfeng_control','USAGE'),
 'functions',(SELECT coalesce(jsonb_agg(to_jsonb(p) || jsonb_build_object(
    'oid',p.oid::text,'owner',pg_get_userbyid(p.proowner),'schema',n.nspname,'language',l.lanname,
    'returnType',p.prorettype::regtype::text,'comment',obj_description(p.oid,'pg_proc')) ORDER BY p.oid),'[]'::jsonb)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
    WHERE n.nspname='public' AND p.proname='perfeng_ci_grant_rollback'),
 'triggers',(SELECT coalesce(jsonb_agg(to_jsonb(e) || jsonb_build_object(
    'oid',e.oid::text,'evtfoid',e.evtfoid::text,'owner',pg_get_userbyid(e.evtowner),'comment',obj_description(e.oid,'pg_event_trigger')) ORDER BY e.oid),'[]'::jsonb)
    FROM pg_event_trigger e),
 'grantSessions',(SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()
    AND application_name='perfeng-runtime-grants-v1' AND pid<>pg_backend_pid()),
 'grantLocks',(SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND objsubid=1
    AND ((classid::bigint << 32) | objid::bigint)=70657266656
    AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) AND pid<>pg_backend_pid())
) AS snapshot;
