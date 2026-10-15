"""
brain_senses.py  v2.1
AIOps Layer — Converged Kubernetes + KubeVirt Platform
PFE — Cloud & DevOps Engineering — Orange

Run: python brain_senses.py
Requires: pip install kubernetes requests
Requires: Ollama running locally with llama3:8b pulled
"""

import requests
import re
import subprocess
import sys
from datetime import datetime, timezone
from kubernetes import client, config

# ==============================================================================
# CONFIGURATION — edit here only
# ==============================================================================

OLLAMA_URL     = "http://localhost:11434/api/generate"
MODEL_NAME     = "llama3:8b"
OLLAMA_TIMEOUT = 120

# Events older than this are considered stale and ignored.
EVENT_MAX_AGE_MINUTES = 30

EVENT_NOISE_FILTER = {
    "AdvancedFeatureUse", "Started", "Created", "Pulled", "Scheduled",
    "SuccessfulCreate", "ScalingReplicaSet", "LeaderElection", "Killing",
    "Preempting", "NodeNotReady", "BackoffLimitExceeded",
    "SuccessfulDelete", "EnsuringLoadBalancer", "EnsuredLoadBalancer",
    "SandboxChanged",
}

ALLOWED_COMMAND_PREFIXES = ("kubectl", "virtctl")

BLOCKED_PREFIXES = (
    "systemctl", "apt", "apt-get", "yum", "dnf",
    "ip link", "ip addr", "ip route",
    "xargs", "service ", "df -h", "tail -f",
    "rm -rf", "shutdown", "reboot", "poweroff",
    "mkfs", "fdisk", "parted",
)

SEVERITY_MAP = {
    "CRITICAL": {
        "BackOff", "CrashLoopBackOff", "OOMKilled", "FailedMount",
        "FailedCreate", "FailedScheduling", "FailedCreatePodSandBox",
        "Unhealthy", "Evicted", "FailedAttachVolume", "FailedDetachVolume",
        "Failed",
    },
    "WARN": {
        "Pulling", "ImagePullBackOff", "ErrImagePull", "NetworkNotReady",
        "ProviderNetworkNotReady", "Rebooted", "NodeHasDiskPressure",
        "NodeHasMemoryPressure", "ProbeWarning", "DNSConfigForming",
        "Pending",
    },
}

# ==============================================================================
# DISPLAY HELPERS
# ==============================================================================

RESET  = "\033[0m"
BOLD   = "\033[1m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
DIM    = "\033[2m"
WHITE  = "\033[97m"

def severity_color(level):
    return {"CRITICAL": RED, "WARN": YELLOW, "INFO": CYAN}.get(level, WHITE)

def print_banner():
    print(f"\n{BOLD}{CYAN}{'═'*62}{RESET}")
    print(f"{BOLD}{CYAN}  brain_senses v2.1  —  AIOps Layer  —  PFE Converged Platform{RESET}")
    print(f"{BOLD}{CYAN}{'═'*62}{RESET}")
    print(f"{DIM}  Model : {MODEL_NAME}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}\n")

def print_finding(severity, kind, name, namespace, reason, message="", age_min=0):
    color  = severity_color(severity)
    ns_str = f"ns/{namespace}" if namespace else "cluster-scoped"
    age_str = f"  {DIM}({age_min} min ago){RESET}" if age_min else ""
    print(f"{color}{BOLD}[{severity}]{RESET} {BOLD}{kind}/{name}{RESET}  {DIM}({ns_str}){RESET}{age_str}")
    print(f"  {DIM}Reason :{RESET} {reason}")
    if message:
        short = message[:140] + "..." if len(message) > 140 else message
        print(f"  {DIM}Detail :{RESET} {short}")

def print_ai_block(root_cause, cmd):
    print(f"\n  {CYAN}{BOLD}AI Insight:{RESET}")
    print(f"    Root cause : {root_cause}")
    print(f"    Suggested  : {cmd if cmd else 'MANUAL_REVIEW — no safe command generated'}")

def print_separator():
    print(f"\n{DIM}{'─'*62}{RESET}\n")

# ==============================================================================
# CLUSTER CONNECTION
# ==============================================================================

def connect():
    try:
        config.load_kube_config()
        v1         = client.CoreV1Api()
        custom_api = client.CustomObjectsApi()
        apps_v1    = client.AppsV1Api()
        v1.list_namespace(limit=1)
        print(f"{GREEN}✔  Connected to cluster{RESET}")
        return v1, custom_api, apps_v1
    except Exception as e:
        print(f"{RED}✘  Cannot connect to cluster: {e}{RESET}")
        sys.exit(1)

# ==============================================================================
# LIVE STATE CHECKERS
# ==============================================================================

def is_pod_healthy(v1, name, namespace):
    """Returns True only if pod is Running AND all containers are Ready."""
    try:
        pod   = v1.read_namespaced_pod(name=name, namespace=namespace)
        phase = pod.status.phase or "Unknown"
        if phase not in ("Running", "Succeeded"):
            return False
        for cs in (pod.status.container_statuses or []):
            if not cs.ready:
                return False
        return True
    except Exception:
        return False

def is_node_healthy(v1, name):
    try:
        node = v1.read_node(name=name)
        for cond in (node.status.conditions or []):
            if cond.type == "Ready":
                return cond.status == "True"
        return False
    except Exception:
        return False

def get_pod_owner(v1, apps_v1, name, namespace):
    """
    Walks the owner reference chain to find the real controller.
    Returns dict: {kind, owner_name}
    For bare pods returns {kind: Pod, owner_name: <pod-name>}
    """
    try:
        pod  = v1.read_namespaced_pod(name=name, namespace=namespace)
        refs = pod.metadata.owner_references or []
        for ref in refs:
            if ref.kind == "ReplicaSet":
                try:
                    rs      = apps_v1.read_namespaced_replica_set(ref.name, namespace)
                    rs_refs = rs.metadata.owner_references or []
                    for rr in rs_refs:
                        if rr.kind == "Deployment":
                            return {"kind": "Deployment", "owner_name": rr.name}
                except Exception:
                    pass
                return {"kind": "ReplicaSet", "owner_name": ref.name}
            if ref.kind in ("StatefulSet", "DaemonSet", "Job"):
                return {"kind": ref.kind, "owner_name": ref.name}
    except Exception:
        pass
    return {"kind": "Pod", "owner_name": name}

# ==============================================================================
# RESOURCE EXISTENCE CHECK
# ==============================================================================

def resource_exists(v1, custom_api, apps_v1, kind, name, namespace):
    try:
        k = kind.lower()
        if k == "pod":
            v1.read_namespaced_pod(name=name, namespace=namespace)
        elif k in ("deployment", "deploy"):
            apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
        elif k in ("replicaset", "rs"):
            apps_v1.read_namespaced_replica_set(name=name, namespace=namespace)
        elif k in ("daemonset", "ds"):
            apps_v1.read_namespaced_daemon_set(name=name, namespace=namespace)
        elif k in ("statefulset", "sts"):
            apps_v1.read_namespaced_stateful_set(name=name, namespace=namespace)
        elif k in ("virtualmachineinstance", "vmi"):
            custom_api.get_namespaced_custom_object(
                "kubevirt.io", "v1", namespace, "virtualmachineinstances", name)
        elif k in ("virtualmachine", "vm"):
            custom_api.get_namespaced_custom_object(
                "kubevirt.io", "v1", namespace, "virtualmachines", name)
        return True
    except Exception:
        return False

# ==============================================================================
# SCANNERS
# ==============================================================================

def get_severity(reason):
    for level, reasons in SEVERITY_MAP.items():
        if reason in reasons:
            return level
    return "INFO"

def event_age_minutes(ev):
    try:
        ts = ev.last_timestamp or ev.event_time
        if ts is None:
            return 0
        now = datetime.now(timezone.utc)
        if hasattr(ts, 'tzinfo') and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (now - ts).total_seconds() / 60
    except Exception:
        return 0

def scan_events(v1):
    try:
        events = v1.list_event_for_all_namespaces(watch=False)
    except Exception as e:
        print(f"{YELLOW}⚠  Could not scan events: {e}{RESET}")
        return []

    seen     = set()
    findings = []

    for ev in events.items:
        if ev.type != "Warning":
            continue
        reason = ev.reason or "Unknown"
        if reason in EVENT_NOISE_FILTER:
            continue

        age = event_age_minutes(ev)
        if age > EVENT_MAX_AGE_MINUTES:
            continue  # stale — skip

        name = ev.involved_object.name or "unknown"
        ns   = ev.involved_object.namespace or "default"
        kind = ev.involved_object.kind or "Object"

        # Cross-reference live state — healthy resource = stale event
        if kind == "Pod" and is_pod_healthy(v1, name, ns):
            continue
        if kind == "Node" and is_node_healthy(v1, name):
            continue

        key = (reason, name, ns)
        if key in seen:
            continue
        seen.add(key)

        findings.append({
            "source":    "EVENT",
            "severity":  get_severity(reason),
            "kind":      kind,
            "name":      name,
            "namespace": ns,
            "reason":    reason,
            "message":   ev.message or "",
            "age_min":   round(age, 1),
        })

    order = {"CRITICAL": 0, "WARN": 1, "INFO": 2}
    findings.sort(key=lambda x: order.get(x["severity"], 3))
    return findings[:10]

def scan_pods(v1):
    TRANSIENT = {
        "ContainerCreating", "Terminating", "PodInitializing",
        "Init:0/1", "Init:0/2", "Completed",
    }
    SYSTEM_PREFIXES = ("virt-launcher-", "virt-handler-", "virt-api-", "virt-controller-", "virt-operator-")
    try:
        pods = v1.list_pod_for_all_namespaces(watch=False)
    except Exception as e:
        print(f"{YELLOW}⚠  Could not scan pods: {e}{RESET}")
        return []

    findings = []
    for pod in pods.items:
        ns    = pod.metadata.namespace
        name  = pod.metadata.name
        phase = pod.status.phase or "Unknown"

        if any(name.startswith(p) for p in SYSTEM_PREFIXES):
            continue

        if phase == "Succeeded":
            continue

        if phase == "Running":
            for cs in (pod.status.container_statuses or []):
                if cs.state and cs.state.waiting:
                    reason = cs.state.waiting.reason or ""
                    if reason and reason not in TRANSIENT:
                        findings.append({
                            "source":    "POD",
                            "severity":  get_severity(reason),
                            "kind":      "Pod",
                            "name":      name,
                            "namespace": ns,
                            "reason":    reason,
                            "message":   cs.state.waiting.message or "",
                            "age_min":   0,
                        })
            continue

        if phase in ("Failed", "Unknown", "Pending"):
            findings.append({
                "source":    "POD",
                "severity":  "CRITICAL" if phase in ("Failed", "Unknown") else "WARN",
                "kind":      "Pod",
                "name":      name,
                "namespace": ns,
                "reason":    phase,
                "message":   "",
                "age_min":   0,
            })

    return findings

def scan_vms(custom_api):
    try:
        vmis = custom_api.list_cluster_custom_object(
            "kubevirt.io", "v1", "virtualmachineinstances")
    except Exception as e:
        print(f"{YELLOW}⚠  Could not scan VMIs: {e}{RESET}")
        return []

    findings = []
    for vmi in vmis.get("items", []):
        phase  = vmi.get("status", {}).get("phase", "Unknown")
        reason = vmi.get("status", {}).get("reason", "")
        if phase in ("Running", "Succeeded"):
            continue
        if phase == "Stopped" and reason in ("", "Stopped", "Halted"):
            continue
        name = vmi["metadata"]["name"]
        ns   = vmi["metadata"]["namespace"]
        findings.append({
            "source":    "VM",
            "severity":  "CRITICAL" if phase in ("Failed", "Unknown", "Paused") else "WARN",
            "kind":      "VirtualMachineInstance",
            "name":      name,
            "namespace": ns,
            "reason":    phase,
            "message":   reason,
            "age_min":   0,
        })
    return findings

# ==============================================================================
# LLM BRAIN
# ==============================================================================

def build_prompt(finding, owner_info, evidence=""):
    kind       = finding["kind"]
    name       = finding["name"]
    ns         = finding["namespace"]
    reason     = finding["reason"]
    message    = finding["message"]
    owner_kind = owner_info.get("kind", kind)
    owner_name = owner_info.get("owner_name", name)

    if "virtualmachine" in kind.lower() or kind.lower() == "vmi":
        remediation_hint = (
            f"This is a KubeVirt VM. "
            f"Correct FIX: `virtctl restart {name} -n {ns}`"
        )
    elif owner_kind == "Pod":
        remediation_hint = (
            f"This is a BARE POD with no controller. "
            f"Correct FIX: `kubectl delete pod {name} -n {ns}`"
        )
    else:
        remediation_hint = (
            f"This pod is owned by {owner_kind} '{owner_name}'. "
            f"Target the OWNER not the pod. "
            f"Correct FIX: `kubectl rollout restart {owner_kind.lower()}/{owner_name} -n {ns}`"
        )

    return f"""You are a Senior SRE managing a Kubernetes + KubeVirt cluster.

INCIDENT:
  Kind      : {kind}
  Name      : {name}
  Namespace : {ns}
  Reason    : {reason}
  Detail    : {message}
  Hint      : {remediation_hint}
{f"  Evidence  : {evidence}" if evidence else ""}

RULES — follow exactly, no exceptions:
1. Output ONLY these two lines. Nothing else.
2. ROOT CAUSE must be 1-2 sentences.
3. FIX must be ONE kubectl or virtctl command.
4. Include -n {ns} in the command.
5. Use real names — NO placeholders.
6. NEVER use kubectl describe / kubectl get / kubectl logs as FIX.
7. Follow the Hint above for the correct command pattern.
8. If unknown: write  FIX: MANUAL_REVIEW

ROOT CAUSE: <your explanation>
FIX: `<your command>`"""

def ask_llm(prompt):
    payload = {
        "model":  MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "top_p": 0.9, "num_predict": 120},
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except requests.exceptions.Timeout:
        return "ERROR: LLM timeout"
    except Exception as e:
        return f"ERROR: {e}"

def extract_command(text):
    if "MANUAL_REVIEW" in text:
        return None
    m = re.search(r"FIX:\s*`?([^\n`]+)`?", text, re.IGNORECASE)
    if m:
        cmd = m.group(1).strip().strip("`")
        if cmd.startswith(ALLOWED_COMMAND_PREFIXES):
            return cmd
    for cmd in re.findall(r"`([^`]+)`", text):
        if cmd.strip().startswith(ALLOWED_COMMAND_PREFIXES):
            return cmd.strip()
    return None

def extract_root_cause(text):
    m = re.search(r"ROOT CAUSE:\s*(.+?)(?:\n|FIX:|$)", text, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else text[:200].strip()

# ==============================================================================
# EVIDENCE COLLECTOR
# ==============================================================================

def collect_evidence(v1, custom_api, finding):
    kind = finding["kind"].lower()
    name = finding["name"]
    ns   = finding["namespace"]
    try:
        if kind == "pod":
            logs = v1.read_namespaced_pod_log(
                name=name, namespace=ns, tail_lines=8, _preload_content=True)
            return f"Logs: {logs[:280]}" if logs.strip() else "No logs."
        elif "vmi" in kind or "virtualmachine" in kind:
            vmi = custom_api.get_namespaced_custom_object(
                "kubevirt.io", "v1", ns, "virtualmachineinstances", name)
            conds = vmi.get("status", {}).get("conditions", [])
            s = "; ".join(
                f"{c.get('type')}={c.get('status')}({c.get('reason','')})"
                for c in conds[:3])
            return f"VMI conditions: {s}" if s else "No conditions."
    except Exception as e:
        return f"Evidence unavailable: {e}"
    return ""

# ==============================================================================
# EXECUTOR
# ==============================================================================

def is_safe_command(cmd):
    low = cmd.lower().strip()
    if not low.startswith(ALLOWED_COMMAND_PREFIXES):
        return False, "Must start with kubectl or virtctl"
    for b in BLOCKED_PREFIXES:
        if b in low:
            return False, f"Blocked: '{b}'"
    for ch in ("&&", "||", ";", "|", ">", "<"):
        if ch in cmd:
            return False, f"Shell chaining '{ch}' not allowed"
    return True, ""

def route_command(cmd):
    """
    virtctl lives on node-01, not on the Windows host.
    Route virtctl commands through the virt-api pod via kubectl exec.
    """
    if cmd.startswith("virtctl "):
        args = cmd[len("virtctl "):].strip()
        return f"kubectl exec -n kubevirt deploy/virt-api -- virtctl {args}"
    return cmd

def execute_command(cmd):
    safe, reason = is_safe_command(cmd)
    if not safe:
        print(f"  {RED}\u2718  BLOCKED: {reason}{RESET}")
        return
    routed = route_command(cmd)
    if routed != cmd:
        print(f"  {DIM}virtctl routed via kubectl exec (not on Windows PATH){RESET}")
    print(f"  {CYAN}\u25b6  Executing:{RESET} {BOLD}{routed}{RESET}")
    try:
        result = subprocess.run(
            routed.split(), capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines()[:20]:
                print(f"  {DIM}{line}{RESET}")
            print(f"  {GREEN}\u2714  Success{RESET}")
        else:
            print(f"  {RED}\u2718  Failed:{RESET} {result.stderr.strip()[:300]}")
    except subprocess.TimeoutExpired:
        print(f"  {RED}\u2718  Timed out{RESET}")
    except Exception as e:
        print(f"  {RED}\u2718  Error: {e}{RESET}")


# ==============================================================================
# MAIN
# ==============================================================================

def process_finding(finding, v1, custom_api, apps_v1):
    print_separator()
    print_finding(**{k: finding[k] for k in
                     ("severity","kind","name","namespace","reason","message","age_min")})

    owner_info = {"kind": finding["kind"], "owner_name": finding["name"]}
    if finding["kind"] == "Pod":
        owner_info = get_pod_owner(v1, apps_v1, finding["name"], finding["namespace"])

    evidence = collect_evidence(v1, custom_api, finding)

    print(f"\n  {DIM}Consulting {MODEL_NAME}...{RESET}", end="", flush=True)
    response = ask_llm(build_prompt(finding, owner_info, evidence))
    print(f"\r  {' '*50}\r", end="")

    if response.startswith("ERROR:"):
        print(f"  {RED}✘  LLM error: {response}{RESET}")
        return

    root_cause = extract_root_cause(response)
    cmd        = extract_command(response)
    print_ai_block(root_cause, cmd)

    if not cmd:
        print(f"\n  {YELLOW}⚠  No actionable command. Manual review required.{RESET}")
        return

    target_kind = owner_info.get("kind", finding["kind"])
    target_name = owner_info.get("owner_name", finding["name"])
    target_ns   = finding["namespace"]

    if not resource_exists(v1, custom_api, apps_v1, target_kind, target_name, target_ns):
        print(f"\n  {YELLOW}⚠  '{target_name}' ({target_kind}) not found — skipping.{RESET}")
        return

    print(f"\n  {BOLD}Command  :{RESET} {CYAN}{cmd}{RESET}")
    try:
        ans = input(f"  {BOLD}Apply fix?{RESET} (y/n/s=skip all): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print(f"\n  {YELLOW}Interrupted.{RESET}")
        return

    if ans == "y":
        execute_command(cmd)
    elif ans == "s":
        raise StopIteration
    else:
        print(f"  {DIM}Skipped.{RESET}")

def main():
    print_banner()
    v1, custom_api, apps_v1 = connect()

    print(f"\n{BOLD}Scanning cluster...{RESET}")
    events = scan_events(v1)
    pods   = scan_pods(v1)
    vms    = scan_vms(custom_api)

    seen   = set()
    unique = []
    for f in events + pods + vms:
        if f["kind"] == "Pod":
            key = ("Pod", f["name"], f["namespace"])
        else:
            key = (f["kind"], f["name"], f["namespace"])
        if key not in seen:
            seen.add(key)
            unique.append(f)

    order = {"CRITICAL": 0, "WARN": 1, "INFO": 2}
    unique.sort(key=lambda x: order.get(x["severity"], 3))

    if not unique:
        print(f"\n{GREEN}✔  Cluster is healthy — no actionable findings.{RESET}\n")
        return

    c = sum(1 for f in unique if f["severity"] == "CRITICAL")
    w = sum(1 for f in unique if f["severity"] == "WARN")
    i = sum(1 for f in unique if f["severity"] == "INFO")

    print(f"\n{BOLD}Findings:{RESET}  "
          f"{RED}{c} CRITICAL{RESET}  {YELLOW}{w} WARN{RESET}  {CYAN}{i} INFO{RESET}\n")

    try:
        for finding in unique:
            process_finding(finding, v1, custom_api, apps_v1)
    except StopIteration:
        print(f"\n{YELLOW}Session ended by user.{RESET}")

    print_separator()
    print(f"{BOLD}{GREEN}Scan complete.{RESET}\n")

if __name__ == "__main__":
    main()