{{/*
Generate run ID
*/}}
{{- define "k6-runner.runId" -}}
{{- if .Values.run.id -}}
{{- .Values.run.id -}}
{{- else -}}
{{- printf "perf-%s-%s" (now | date "20060102-150405") (randAlphaNum 8 | lower) -}}
{{- end -}}
{{- end -}}

{{/*
Generate job name
*/}}
{{- define "k6-runner.jobName" -}}
{{- printf "k6-test-%s-%s" .Values.test.name (randAlphaNum 8 | lower) -}}
{{- end -}}

{{/*
Common labels
*/}}
{{- define "k6-runner.labels" -}}
perfeng.io/managed-by: perfeng
perfeng.io/test: {{ .Values.test.name }}
perfeng.io/profile: {{ .Values.test.profile }}
perfeng.io/run-id: {{ include "k6-runner.runId" . }}
{{- end -}}