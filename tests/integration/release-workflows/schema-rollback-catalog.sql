-- Read-only catalog snapshot; safe before creation and after guarded cleanup.
SELECT jsonb_build_object(
 'observedAt',clock_timestamp(),
 'databaseOID',(SELECT oid::text FROM pg_database WHERE datname=current_database()),
 'schema',(SELECT jsonb_build_object('oid',oid::text,'owner',pg_get_userbyid(nspowner),
   'comment',obj_description(oid,'pg_namespace')) FROM pg_namespace WHERE nspname='perfeng_control'),
 'relations',(SELECT coalesce(jsonb_agg(jsonb_build_object('oid',c.oid::text,'name',c.relname,
   'kind',c.relkind,'owner',pg_get_userbyid(c.relowner),'comment',obj_description(c.oid,'pg_class'),
   'toastOID',c.reltoastrelid::text) ORDER BY c.relname),'[]'::jsonb)
   FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='perfeng_control'),
 'columns',(SELECT coalesce(jsonb_agg(jsonb_build_object('name',a.attname,
   'type',format_type(a.atttypid,a.atttypmod),'notNull',a.attnotnull,
   'default',pg_get_expr(d.adbin,d.adrelid),'identity',a.attidentity,'generated',a.attgenerated)
   ORDER BY a.attnum),'[]'::jsonb) FROM pg_attribute a
   LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
   WHERE a.attrelid=to_regclass('perfeng_control.artifacts') AND a.attnum>0 AND NOT a.attisdropped),
 'migrationSessions',(SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()
   AND application_name='perfeng-schema-migration-v1'),
 'migrationLocks',(SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND objsubid=1
   AND ((classid::bigint << 32) | objid::bigint)=70657266656
   AND database=(SELECT oid FROM pg_database WHERE datname=current_database()))) AS snapshot;
