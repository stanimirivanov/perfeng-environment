{{/*
Common labels for all resources
*/}}
{{- define "perfeng-infra.labels" -}}
perfeng.io/managed-by: {{ .Values.global.managedBy | default "perfeng" }}
perfeng.io/environment: {{ .Values.global.environment | default "local" }}
perfeng.io/cluster: {{ .Values.global.clusterName | default "perfeng-local" }}
{{- end -}}