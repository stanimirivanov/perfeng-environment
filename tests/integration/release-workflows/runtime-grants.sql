BEGIN;
REVOKE ALL ON SCHEMA perfeng_control FROM PUBLIC, perfeng_runtime;
GRANT USAGE ON SCHEMA perfeng_control TO perfeng_runtime;
REVOKE ALL ON ALL TABLES IN SCHEMA perfeng_control FROM PUBLIC, perfeng_runtime;
GRANT SELECT, INSERT, UPDATE ON perfeng_control.runs, perfeng_control.create_bindings, perfeng_control.baselines TO perfeng_runtime;
GRANT SELECT, INSERT ON perfeng_control.artifacts, perfeng_control.kubernetes_executions TO perfeng_runtime;
GRANT SELECT, INSERT, UPDATE ON perfeng_control.reconciliation_leases TO perfeng_runtime;
GRANT SELECT, INSERT, UPDATE ON perfeng_control.benchmark_target_reservations TO perfeng_runtime;
COMMIT;
