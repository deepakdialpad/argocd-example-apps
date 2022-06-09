{{/*
FST Dev IP
*/}}
{{- define "fst.fstDevIp" -}}
{{ if .Values.detachFst -}}
{{ .Values.localDevHostIp -}}
{{ else -}}
{{ .Values.fstIp -}}
{{ end }}
{{- end }}

{{- define "fst.fstDevHost" -}}
{{ if .Values.detachFst -}}
http://{{ .Values.localDevHostIp -}}:8086
{{- else -}}
http://devfst
{{- end }}
{{- end }}

{{- define "fst.devHostAliases" -}}
{{ if eq .Values.global.fstEnv "dev" -}}
hostAliases:
- ip: "{{ include "fst.fstDevIp" . -}}"
  hostnames:
  - "devfst"
- ip: "{{ include "fst.fstDevIp" . -}}"
  hostnames:
  - "devbox"
{{ end -}}
{{- end }}


{{/*
FST URL
*/}}
{{- define "fst.fstURL" -}}
{{ if eq .Values.global.fstEnv "dev" -}}
"{{ include "fst.fstDevHost" . }}"
{{ else -}}
"https://{{ .Values.fstHost }}"
{{ end }}
{{- end }}

{{/*
Common environment vars
*/}}
{{- define "fst.commonEnv" -}}
- name: FST_ENV
  value: {{ .Values.global.fstEnv }}
- name: FST_MACHINE_TYPE
  value: {{ .Values.machineType }}
- name: TYPE_ABBR
  value: {{ .Values.fsmType }}
- name: FST_URL
  value: {{ include "fst.fstURL" . }}
- name: PROJECT_ID
  value: {{ .Values.global.projectId }}
- name: FST_INFRA_TYPE
  value: "containerized"
- name: RELEASE_NAME
  value: {{ .Values.releaseName }}
- name: SPINNAKER_REQUEST_ID
  value: {{ .Values.spinnakerRequestId }}
- name: FST_VERSION
  value: {{ .Values.fstVersion }}
- name: FST_PHASE
  value: {{ .Values.phase }}
- name: FST_DRAINING_REASONS
  value: "Created-Drained"
- name: FST_NODE_NAME
  valueFrom:
    fieldRef:
      fieldPath: spec.nodeName
- name: FST_NODE_IP
  valueFrom:
    fieldRef:
      fieldPath: status.hostIP
- name: FST_POD_IP
  valueFrom:
    fieldRef:
      fieldPath: status.podIP
- name: FST_POD_NAME
  valueFrom:
    fieldRef:
      fieldPath: metadata.name
- name: FST_POD_NAMESPACE
  valueFrom:
    fieldRef:
      fieldPath: metadata.namespace
- name: FST_POD_UID
  valueFrom:
    fieldRef:
      fieldPath: metadata.name
- name: 'FST_TELEPHONY_BACKEND_KEY'
  value: '{{ .Values.fstTelephonyBackendKey }}'
- name: 'FST_SECRET_KEY'
  value: '{{ .Values.fstSecretKey }}'
{{ if eq .Values.global.fstEnv "dev" -}}
- name: FST_LOCAL_SVC_DISCOVERY_KEY
  value: {{ .Values.machineName }}
{{ end -}}
{{- end }}


{{/*
Common labels
*/}}
{{- define "fst.commonLabels" -}}
    helm_release_name: {{ .Values.releaseName }}
{{ end -}}


{{/*
Rackman container
*/}}
{{- define "fst.rackmanContainer" -}}
- name: fst-rackman
  image: {{ .Values.global.containerRegistry }}/fst-rackman:{{ .Values.fstVersion }}
  resources:
    limits:
      memory: {{ .Values.rackman.limits.memory }}
      cpu: {{ .Values.rackman.limits.cpu }}
    requests:
      memory: {{ .Values.rackman.requests.memory }}
      cpu: {{ .Values.rackman.requests.cpu }}
  env:
    {{- include "fst.commonEnv" . | nindent 2 }}
    {{ if eq .Values.machineType "SmsGateway" }}
    {{- include "fst.smsGatewayEnv" . | nindent 2 }}
    {{ end }}
    {{ if eq .Values.machineType "TelephonyEngine" }}
    {{- include "fst.telephonyEngineEnv" . | nindent 2 }}
    {{ end }}
    {{ if eq .Values.machineType "Prober" }}
    {{- include "fst.proberEnv" . | nindent 2 }}
    {{ end }}
    {{ if eq .Values.machineType "BorderController_inbound" }}
    {{- include "fst.sbcEnv" . | nindent 2 }}
    {{ end }}

  workingDir: /usr/local/pod/rackman
  ports:
  - containerPort: 8082
  volumeMounts:
  - mountPath: /var/run/docker.sock
    name: dockersocket
  {{ if eq .Values.global.fstEnv "dev" -}}
  - mountPath: /usr/local/envfiles/
    name: rackman-secrets
    readOnly: true
  {{ end -}}
  - mountPath: /usr/bin/docker
    name: dockerexecutable
  {{- include "fst.commonMounts" . | nindent 2 }}
  {{- include "fst.containerLogMount" . | nindent 2 }}
  {{ if eq .Values.global.fstEnv "dev" -}}
  {{ if eq .Values.global.mountRackman true -}}
  - mountPath: /usr/local/pod/rackman/app
    name: rackman-source
  - mountPath: /usr/local/pod/common/lib
    name: common-lib-source
  {{ end -}}
  - mountPath: /etc/kubernetes
    name: kubeconfig
  {{ end -}}
  {{ if eq .Values.machineType "TelephonyEngine" }}
  {{- include "fst.fsRecordingMounts" . | nindent 2 }}
  {{ end }}
  {{ if ne .Values.global.fstEnv "dev" -}}
  {{ if eq .Values.machineType "BorderController_inbound" -}}
  - mountPath: {{ .Values.fs_flatfile_path }}
    name: fs-flatfile
    subPathExpr: $(FST_POD_NAME)/freeswitch/flatfile
  {{ end -}}
  {{ end -}}
  {{ if eq .Values.app "BorderController" -}}
  - mountPath: {{ .Values.fs_failover_gateways_path }}
    name: fs-failover-gateways
    subPathExpr: $(FST_POD_NAME)/freeswitch/failover_gateways
  {{ end -}}
{{- end }}

{{/*
Siplogger container
*/}}
{{- define "fst.siploggerContainer" }}
{{ if ne .Values.global.fstEnv "dev" -}}
- name: fst-siplogger
  image: {{ .Values.global.containerRegistry }}/fst-siplogger:{{ .Values.fstVersion }}
  resources:
    limits:
      memory: {{ .Values.siplogger.limits.memory }}
      cpu: {{ .Values.siplogger.limits.cpu }}
    requests:
      memory: {{ .Values.siplogger.requests.memory }}
      cpu: {{ .Values.siplogger.requests.cpu }}
  env:
    - name: GOMAXPROCS
      value: "{{ .Values.siplogger.GOMAXPROCS }}"
    {{- include "fst.commonEnv" . | nindent 4 }}
  workingDir: /usr/local/siplogger
  volumeMounts:
  {{- include "fst.commonMounts" . | nindent 2 }}
  {{- include "fst.containerLogMount" . | nindent 2 }}
{{ end -}}
{{- end }}

{{/*
Envoy container
*/}}
{{- define "fst.envoyContainer" }}
{{ if ne .Values.global.fstEnv "dev" -}}
- name: fst-envoy
  image: {{ .Values.global.containerRegistry }}/fst-envoy:{{ .Values.fstVersion }}
  resources:
    limits:
      memory: {{ .Values.envoy.limits.memory }}
      cpu: {{ .Values.envoy.limits.cpu }}
    requests:
      memory: {{ .Values.envoy.requests.memory }}
      cpu: {{ .Values.envoy.requests.cpu }}
  env:
    {{- include "fst.commonEnv" . | nindent 4 }}
  volumeMounts:
  {{- include "fst.commonMounts" . | nindent 2 }}
  - mountPath: /var/log/envoy
    name: container-log-dir
    subPathExpr: $(FST_POD_NAME)/envoy
{{ end -}}
{{- end }}

{{/*
Common Volumes
*/}}
{{- define "fst.commonVolumes" -}}
- name: container-log-dir
  hostPath:
    path: /var/log/pod_logs
    type: DirectoryOrCreate
- name: dockerexecutable
  hostPath:
    path: /usr/bin/docker
- name: dockersocket
  hostPath:
    path: /var/run/docker.sock
- name: container-share-data
  emptyDir: {}
{{ include "fst.vectorVolumes" . -}}
{{ if ne .Values.global.fstEnv "dev" -}}
- name: dsdsocket
  hostPath:
    path: /var/run/datadog/
- name: corefiles
  hostPath:
    path: /var/corefiles/
{{ end -}}
{{ if eq .Values.global.fstEnv "dev" -}}
- name: rackman-secrets
  secret:
    secretName: rackman-secrets
    items:
    - key: gcp-p12
      path: project_key_file.p12
    - key: gcp-json
      path: gcs_private_key.json
{{ end -}}
{{- end }}

{{- define "fst.commonMounts" }}
{{ if ne .Values.global.fstEnv "dev" -}}
- mountPath: /var/run/datadog
  name: dsdsocket
- mountPath: /var/corefiles/
  name: corefiles
  subPathExpr: $(FST_POD_NAME)
{{ end -}}
{{- include "fst.vectorConfigMount" .}}
- mountPath: /usr/local/share_data
  name: container-share-data
{{- end}}

{{- define "fst.containerLogMount" }}
- mountPath: /var/log/
  name: container-log-dir
  subPathExpr: $(FST_POD_NAME)
{{- end}}

{{/*
Local Dev Volumes
*/}}
{{- define "fst.localDevVolumes" -}}
{{ if eq .Values.global.fstEnv "dev" -}}
{{ if eq .Values.global.mountRackman true -}}
- name: rackman-source
  hostPath:
    path: {{ .Values.sourceMount }}/servers/rackman/app
- name: common-lib-source
  hostPath:
    path: {{ .Values.sourceMount }}/servers/common/lib
{{ end -}}
- name: kubeconfig
  hostPath:
    path: /source/servers/localdev/kubeconfig
{{ end -}}
{{- end }}


{{/*
NoOP Command
*/}}
{{- define "fst.noOpCommand" -}}
command: [ "/bin/bash", "-c", "--" ]
args: [ "while true; do echo 'ansible container wait forever' && sleep 30; done;" ]
{{- end }}


{{/*
common node selectors
*/}}
{{- define "fst.nodeSelectors" -}}
dc: {{ .Values.dc }}
phase: {{ .Values.phase }}
{{- end }}


{{/*
Common Sysctls
*/}}
{{- define "fst.commonSysctls" }}
{{ if ne .Values.global.fstEnv "dev" -}}
- name: net.ipv4.icmp_ratelimit
  value: "60"
- name: net.ipv6.conf.all.disable_ipv6
  value: "1"
{{ end -}}
{{- end}}


{{/*
Environment vars specific to sbc machine
*/}}
{{- define "fst.sbcEnv" -}}
- name: FS_CONF
  value: {{ .Values.fsConf }}
- name: POP_NAME
  value: {{ .Values.dc }}
{{- end }}


{{/*
Common deployment name
*/}}
{{- define "fst.deploymentName" -}}
{{- if ne .Values.global.fstEnv "dev" -}}
name: {{ .Values.releaseName }}
{{else}}
name: {{ .Values.machineName }}
{{- end -}}
{{- end -}}

{{/*
Common deployment strategy
*/}}
{{- define "fst.commonDeploymentStrategy" -}}
{{ if ne .Values.global.fstEnv "dev" -}}
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxUnavailable: 10%
    maxSurge: 0
{{ end -}}
{{- end}}

{{/*
Vector container
*/}}
{{- define "fst.vectorContainer" }}
{{ if has .Values.app .Values.vector.apps -}}
- name: fst-vector
  image: {{ .Values.global.containerRegistry }}/fst-vector:{{ .Values.fstVersion }}
  env:
    - name: LOG
      value: info
    {{- include "fst.commonEnv" . | nindent 4 }}
    
    # temp fix until https://github.com/vectordotdev/vector/issues/9952
    - name: TYPE_ABBR
      value: {{ .Values.fsmType }}

    - name: POP_NAME
      value: {{ .Values.dc }}
    - name: DD_API_KEY
      value: {{ .Values.datadogagent.apiKey }}
  volumeMounts:
    {{- include "fst.commonMounts" . | nindent 4 }}
    {{- include "fst.containerLogMount" . | nindent 4 }}
    # Vector will store it's data (eg, checkpoints) here.
    - name: vector-data-dir
      mountPath: "/var/lib/vector"
{{- end }}
{{ end -}}

{{- define "fst.vectorVolumes" -}}
{{ if has .Values.app .Values.vector.apps -}}
- name: vector-data-dir
  emptyDir: {}
- name: vector-configs
  emptyDir: {}
{{- end }}
{{ end -}}

{{- define "fst.vectorConfigMount" }}
{{ if has .Values.app .Values.vector.apps -}}
- name: vector-configs
  mountPath: /mnt/vector-configs
{{- end }}
{{ end -}}
