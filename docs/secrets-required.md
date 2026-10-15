# Secrets required before deployment

Create these with your own values. Never commit real values.

## Keycloak (namespace: keycloak)
kubectl create namespace keycloak
kubectl create secret generic keycloak-secret -n keycloak `
  --from-literal=KC_DB_USERNAME=CHANGE_ME `
  --from-literal=KC_DB_PASSWORD=CHANGE_ME `
  --from-literal=KC_BOOTSTRAP_ADMIN_USERNAME=CHANGE_ME `
  --from-literal=KC_BOOTSTRAP_ADMIN_PASSWORD=CHANGE_ME

## Grafana (namespace: monitoring)
Keys expected: admin-user, admin-password. Created by the kube-prometheus-stack
Helm release (set grafana.adminPassword in helm-values/) or manually:
kubectl create secret generic kps-grafana -n monitoring `
  --from-literal=admin-user=admin `
  --from-literal=admin-password=CHANGE_ME

## Also needed
- PostgreSQL credentials for the Keycloak database (postgres-postgresql release)
- Ingress TLS secrets referenced by `secretName` in the ingress manifests

## Helm values and node configs
Set your own values for: kps adminPassword (helm-values/kps.yaml), postgres password and postgresPassword (helm-values/postgres.yaml), keepalived auth_pass (node-configs/keepalived.conf). Placeholders are CHANGE_ME / CHANGEME.
