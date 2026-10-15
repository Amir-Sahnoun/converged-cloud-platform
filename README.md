# Converged Cloud Platform — PFE Orange Tunisie

An open-source private cloud **proof of concept** built on a 3-node Kubernetes cluster,
validated as an alternative to the VMware ecosystem: virtual machines (KubeVirt) and
containers run side by side on one platform, with multi-tenancy, SDN, distributed storage,
observability, and an AI extension layer.

Final-year engineering project (PFE) — Cloud & DevOps Engineering, Esprit — carried out at
**Orange Tunisie** (B2B Cloud & Infrastructure), 2025/2026.

> **Scope note:** this was validated in a VMware Workstation lab (nested virtualization,
> no GPU, single physical host). Results demonstrate technical viability, not production
> performance. See [Known limitations](#known-limitations).

---

## What the platform does

- Runs VMs (KubeVirt) and containers on the same Kubernetes cluster
- Multi-network VMs (mgmt / frontend / backend) via Multus + Kube-OVN
- Multi-tenant isolation: namespaces, RBAC, default-deny NetworkPolicies, per-tenant vector collections
- Distributed block storage with replication and automatic detach/reattach (Longhorn)
- Bare-metal LoadBalancer services with real LAN IPs (MetalLB, L2 mode)
- Operator authentication through Keycloak (OIDC) integrated with Kubernetes RBAC
- Observability with Prometheus and Grafana
- **AI extension layer** (built beyond the original scope):
  - **AIOps agent** — scans cluster events, detects anomalies, performs Level 0 ticket triage
    (suggests, does not execute remediation)
  - **LLMaaS RAG pipeline** — multi-tenant retrieval-augmented generation over a local LLM
  - **Cluster Weather Forecaster** — trend-based load forecasting shown as a weather dashboard

---

## Architecture

### Topology

| Node | IP | Role | Resources (lab) |
|---|---|---|---|
| node-01 | 192.168.1.10 | Control plane + worker | 4 vCPU, 4 GB RAM |
| node-02 | 192.168.1.11 | Worker | 2 vCPU, 4 GB RAM |
| node-03 | 192.168.1.12 | Worker | 2 vCPU, 4 GB RAM |

- Each node: Ubuntu 22.04.5 LTS, 35 GB OS disk + 25 GB dedicated Longhorn disk
- Kubernetes v1.29 (kubeadm), containerd 2.2
- Control-plane VIP `192.168.1.20` via keepalived + HAProxy
- MetalLB pool `192.168.1.240–250`

### Component stack

| Layer | Component | Purpose |
|---|---|---|
| Orchestration | Kubernetes v1.29 (kubeadm) | Cluster control plane |
| Virtualization | KubeVirt v1.8.1 | VMs as Kubernetes resources (software emulation in the lab) |
| Networking (SDN) | Kube-OVN v1.12.35 | CNI, Geneve overlay, logical subnets |
| Multi-NIC | Multus | Multiple network attachments per pod/VM |
| Load balancing | MetalLB (L2) | LoadBalancer services on bare metal |
| Storage | Longhorn | Distributed block storage via CSI (3-replica StorageClass) |
| Identity | Keycloak | OIDC provider for the Kubernetes API server |
| Monitoring | Prometheus + Grafana (kube-prometheus-stack) | Metrics and dashboards |
| LLM runtime | Ollama | Local inference (`qwen2.5:7b-instruct`, `nomic-embed-text`) |
| Vector store | Qdrant | Per-tenant collections for RAG |
| AIOps | `brain_senses` (Python) | Cluster scanning, trend analysis, ticket triage |

---

## Repository structure

```
converged-cloud-platform/
├── ansible/            OS preparation and kubeadm bootstrap playbooks
├── manifests/
│   ├── kubevirt/       KubeVirt operator and CR
│   ├── longhorn/       Storage classes
│   ├── metallb/        IP pool and L2 advertisement
│   ├── kube-ovn/       Subnets, VLAN / provider network objects
│   ├── keycloak/       Identity provider deployment
│   ├── monitoring/     Prometheus + Grafana stack
│   ├── llmaas/         Qdrant, RAG ingestor, query API
│   ├── aiops/          AIOps agent deployment
│   ├── tenants/        Tenant namespaces and NetworkPolicies
│   └── rbac/           Roles, ClusterRoles and bindings
├── brain_senses/       AIOps Python agent
├── docs/
│   ├── grafana-dashboards/   Exported Grafana dashboards (JSON)
│   └── cluster-validated-state.png
└── helm-values/        Custom Helm values used for deployments
```

---

## Validation results

Ten metrics were measured on the lab cluster.

| ID | Metric | Target | Result | Status |
|---|---|---|---|---|
| M1 | VM boot time | < 60 s | 7.98 s avg (5 runs) | ✅ Passed |
| M2 | Container reschedule | Measured | 5,354 ms (forced eviction) / ~351 s (default eviction timeout) | ✅ Measured |
| M3 | Node failure RTO | Measured | ~432 s (includes eviction grace period) | ✅ Measured |
| M4 | Storage failover | Measured | Volume detached/reattached on another node, data continuity preserved | ✅ Measured |
| M5 | API server availability | > 99.5 % | 100 % over 1 h (3,600 probes) | ✅ Passed |
| M6 | etcd p99 latency | < 100 ms | WAL fsync 24.2 ms / backend commit 40.8 ms | ✅ Passed |
| M7 | VM recovery | < 5 min | 23.81 s | ✅ Passed |
| M8 | VM memory overhead | < 512 MiB | 247 MiB (virt-launcher) | ✅ Passed |
| M9 | PVC provisioning | Measured | 2,554 ms avg (5 runs) | ✅ Measured |
| M10 | Network throughput | > 1 Gbit/s | 14.2 Gbit/s (Kube-OVN overlay) | ✅ Passed |

M10 was measured on the Kube-OVN overlay. Underlay VLAN pod attachment did not converge in the
VMware lab (see limitations).

### Comparison with VMware vSphere

| Capability | VMware vSphere | This platform |
|---|---|---|
| VM orchestration | vCenter | KubeVirt + kubectl |
| Storage | vSAN | Longhorn |
| Networking | NSX | Kube-OVN |
| Load balancing | NSX-T / F5 | MetalLB |
| Identity | vCenter SSO | Keycloak (OIDC) |
| Licensing | Proprietary, high cost | Open source |
| Kubernetes-native | No | Yes |

---

## Multi-tenancy and security

Four isolation layers: namespace + RBAC, network segmentation, storage (namespace-scoped PVCs),
and LLMaaS data isolation (per-tenant Qdrant collections; tenant-b cannot retrieve tenant-a data).

East-west traffic is controlled by a namespace-wide `default-deny` NetworkPolicy plus per-tier
allow-lists (mgmt / frontend / backend). Verified behaviour: mgmt→frontend and frontend→backend
allowed; backend→mgmt and frontend→mgmt denied.

Not production-ready: the LLMaaS API has no API-key auth yet and there is no perimeter firewall layer.

---

## Known limitations

1. **Nested virtualization** — KubeVirt runs in software emulation mode in the VMware Workstation lab.
2. **Underlay VLAN attachment** — Kube-OVN provider-network objects were created, but pod-level
   realization did not converge in the lab; it likely needs a dedicated physical NIC on bare metal.
3. **Single control plane** — the VIP can move, but a true multi-master (3 etcd) setup was not built.
4. **LLM runs on the host**, not in-cluster (no GPU node). Orange's internal LLM was not available.
5. **Security gaps** — LLMaaS API key authentication and a perimeter firewall/ACL layer are future work.
6. Kubernetes v1.29 is used; upgrade via `kubeadm upgrade` is a documented next step.

---

## How to rebuild

Lab sizing used for validation: 3 Ubuntu 22.04.5 LTS VMs (4/2/2 vCPU, 4 GB RAM each), one extra
25 GB disk per node for Longhorn. Plan for more resources if you want headroom for the AI layer
and an Ollama host with enough RAM for a 7B model.

1. Configure static IPs and SSH access on all nodes
2. Run the playbooks in `ansible/` (OS preparation, kubeadm prerequisites)
3. Deploy keepalived + HAProxy for the control-plane VIP
4. `kubeadm init` on node-01, then join node-02 and node-03
5. Apply manifests in this order:
   1. `kube-ovn` (CNI first) and Multus
   2. `longhorn`
   3. `metallb`
   4. `kubevirt`
   5. `monitoring`
   6. `keycloak`
   7. `llmaas`
   8. `aiops`
   9. `tenants` and `rbac`
6. Import dashboards from `docs/grafana-dashboards/`

Each subdirectory documents its own component notes. **Replace all placeholder credentials
and secrets before deploying** (see Security below).

---

## AIOps layer

See `brain_senses/README.md` for setup. Requires an Ollama instance reachable from the cluster with
`qwen2.5:7b-instruct` and `nomic-embed-text` pulled.

---

## Security notice

This repository contains lab configuration only. Credentials are never stored here: manifests
reference Kubernetes Secrets via `secretKeyRef`. You must create those Secrets yourself before
deploying (see `docs/secrets-required.md`). No kubeconfigs, tokens, or private keys are committed.

---

*Cloud & DevOps Engineering — Esprit · Final year project at Orange Tunisie — 2026*
