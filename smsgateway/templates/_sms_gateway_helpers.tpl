{{/*
Environment vars specific to sms-gateway
*/}}
{{- define "fst.smsGatewayEnv" -}}
- name: PROJECT_EMAIL
  value: {{ .Values.smsGateway.projectEmail }}
- name: PROJECT_BUCKET
  value: {{ .Values.smsGateway.projectBucket }}
- name: MACHINE_GROUP
  value: {{.Values.smsGateway.machineGroup }}
- name: SMS_CARRIERS
  value: {{.Values.smsGateway.smsCarriers }}
{{- end }}
