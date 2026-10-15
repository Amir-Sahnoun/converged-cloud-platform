\# brain\_senses — AIOps Layer



Autonomous monitoring agent for the converged Kubernetes + KubeVirt platform.



\## What it does

\- Scans Kubernetes events, pod states, and VirtualMachineInstances

\- Filters stale and noisy events

\- Queries a local LLM (llama3:8b via Ollama) for root cause analysis

\- Suggests namespace-aware remediation commands

\- Human-in-the-Loop confirmation before executing any fix



\## Requirements

\- Python 3.10+

\- Ollama running locally with llama3:8b pulled

\- kubectl configured with cluster access

\- pip install kubernetes requests



\## Usage

python brain\_senses.py



\## Severity levels

\- CRITICAL: CrashLoopBackOff, OOMKilled, Failed, FailedMount

\- WARN: Pending, ImagePullBackOff, NetworkNotReady

\- INFO: informational only



\## Safety

\- Blocks all OS-level commands (systemctl, apt, etc.)

\- No shell=True — prevents shell injection

\- virtctl commands routed via kubectl exec automatically

\- All fixes require human confirmation (y/n/s)

