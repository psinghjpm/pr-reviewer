# PRGenie — EKS Architecture Diagram

> This Mermaid diagram renders natively on GitHub, GitLab, Notion, and any Mermaid-compatible viewer.
> For a fully editable version, import [`eks-architecture.drawio`](./eks-architecture.drawio)
> into Lucidchart (File → Import → draw.io) or draw.io.

---

```mermaid
flowchart LR

    %% ── External Systems ──────────────────────────────────────────────
    subgraph EXT["🌐  External Systems"]
        direction TB
        GH_SRC["🐙  GitHub.com\n─────────────\nPR opened · updated · reopened\nsends webhook event"]
        ANT["🤖  Anthropic API\n─────────────\nclaude-sonnet-4-6\n5-pass AI analysis"]
        GH_API["🐙  GitHub API\n─────────────\nPOST /pulls/{pr}/reviews\nPOST /issues/{pr}/comments"]
    end

    %% ── AWS Cloud ─────────────────────────────────────────────────────
    subgraph AWS["☁️  AWS Cloud"]
        direction TB

        ECR["📦  Amazon ECR\n──────────────────────\npr-reviewer : latest\npr-reviewer-webhook : latest"]

        ALB["🔀  ALB Ingress\n──────────────────────\nHTTPS :443  →  /webhook\nTLS termination"]

        %% ── EKS Cluster ───────────────────────────────────────────────
        subgraph EKS["  EKS Cluster"]
            direction TB

            %% ── namespace: pr-reviewer ────────────────────────────────
            subgraph NS["  namespace: pr-reviewer"]
                direction LR

                SVC["⚡  Service\n──────────────────\nwebhook-handler\nClusterIP : 8080"]

                subgraph CTRL["Webhook Handler"]
                    direction TB
                    DEPLOY["🚀  Deployment  (2 replicas)\n────────────────────────────────\nImage: pr-reviewer-webhook:latest\n\n① Verify HMAC signature\n② Filter: opened / sync / reopened\n③ Build DNS-safe Job name\n④ kubernetes-client → create Job"]
                    SA["👤  ServiceAccount\n────────────────────────────────\nwebhook-handler\nRole → batch/v1 jobs\n  create · get · list\n  (pr-reviewer ns only)"]
                end

                subgraph SECRETS["Secrets"]
                    direction TB
                    SEC_R["🔐  pr-reviewer-secrets\n────────────────────\nANTHROPIC_API_KEY\nGITHUB_TOKEN\nPR_REVIEWER_MODEL (opt)"]
                    SEC_W["🔐  webhook-secrets\n────────────────────\nWEBHOOK_SECRET"]
                end

                JOB["⚙️  K8s Job  (ephemeral per PR event)\n──────────────────────────────────────────────────\nName: pr-review-{repo}-{pr-number}-{unix-ts}\nImage: pr-reviewer:latest\nCommand: pr-reviewer review --url \$PR_URL\n\nResources:  CPU 100 m → 500 m  ·  RAM 256 Mi → 512 Mi\nrestart: Never  ·  backoffLimit: 1  ·  TTL: 3 600 s"]
            end
        end
    end

    %% ── Data flow ─────────────────────────────────────────────────────
    GH_SRC  -->|"POST /webhook\nX-Hub-Signature-256\nX-GitHub-Event: pull_request"| ALB
    ALB     -->|"HTTPS → HTTP\nport 8080"| SVC
    SVC     --> DEPLOY
    SEC_W   -. "WEBHOOK_SECRET\n(env)" .-> DEPLOY
    DEPLOY  -->|"BatchV1Api\ncreate_namespaced_job()"| JOB
    SA      -. "RBAC\nbinds to Deployment" .-> DEPLOY
    SEC_R   -. "ANTHROPIC_API_KEY\nGITHUB_TOKEN\n(env injection)" .-> JOB
    ECR     -. "imagePull\npr-reviewer:latest" .-> JOB
    JOB     -->|"LLM calls\n(multi-turn agentic loop)"| ANT
    JOB     -->|"4 API calls:\n① HEAD SHA\n② diff → position map\n③ batch inline comments\n④ summary comment"| GH_API

    %% ── Styling ───────────────────────────────────────────────────────
    classDef external  fill:#fff2cc,stroke:#d6b656,color:#000,rx:8
    classDef aws       fill:#f5f5f5,stroke:#232f3e,color:#000
    classDef svc       fill:#dae8fc,stroke:#6c8ebf,color:#000,rx:6
    classDef secret    fill:#ffe6cc,stroke:#d79b00,color:#000,rx:6
    classDef job       fill:#f8cecc,stroke:#b85450,color:#000,rx:6
    classDef ecr       fill:#e1d5e7,stroke:#9673a6,color:#000,rx:6
    classDef deploy    fill:#d5e8d4,stroke:#82b366,color:#000,rx:6
    classDef sa        fill:#e6f3ff,stroke:#6c8ebf,color:#000,rx:6

    class GH_SRC,ANT,GH_API  external
    class ECR                 ecr
    class ALB                 svc
    class SVC                 svc
    class DEPLOY              deploy
    class SA                  sa
    class SEC_R,SEC_W         secret
    class JOB                 job
```

---

## Component Glossary

| Component | Type | Purpose |
|---|---|---|
| **ALB Ingress** | AWS Load Balancer | TLS termination; routes `POST /webhook` to the webhook handler service |
| **Service `webhook-handler`** | K8s ClusterIP | Stable in-cluster DNS for the Deployment pods |
| **Deployment `webhook-handler`** | K8s Deployment (2 replicas) | FastAPI app: verifies GitHub HMAC signature, filters PR events, spawns K8s Jobs |
| **ServiceAccount `webhook-handler`** | K8s RBAC | Grants the Deployment permission to `create/get/list` Jobs in the `pr-reviewer` namespace — nothing else |
| **Secret `pr-reviewer-secrets`** | K8s Secret | Holds `ANTHROPIC_API_KEY` + `GITHUB_TOKEN`; injected as env vars into every Job pod |
| **Secret `webhook-secrets`** | K8s Secret | Holds `WEBHOOK_SECRET` (GitHub HMAC key); used by the Deployment for signature verification |
| **K8s Job (ephemeral)** | K8s batch/v1 Job | One Job per PR event; runs `pr-reviewer review --url $PR_URL`; auto-deleted 1 h after completion (`ttlSecondsAfterFinished: 3600`) |
| **Amazon ECR** | Container Registry | Stores the two Docker images pulled by each Job and Deployment pod |
| **Anthropic API** | External | Claude model endpoint; receives the 5-pass agentic review loop |
| **GitHub API** | External | Receives the batch review submission: all inline comments in 1 call + summary in 1 call |

---

## Event Filter Logic (Webhook Handler)

```
Incoming event
    │
    ├─ X-GitHub-Event != "pull_request"  →  202 Accepted (ignored)
    │
    └─ X-GitHub-Event == "pull_request"
            │
            ├─ action == "opened"       →  spawn Job ✅
            ├─ action == "synchronize"  →  spawn Job ✅  (new commits pushed)
            ├─ action == "reopened"     →  spawn Job ✅
            └─ any other action         →  202 Accepted (ignored)
```

## Security Boundaries

- The webhook HMAC signature is verified **before** any business logic runs — an unauthenticated request never reaches the Job-spawning code
- The ServiceAccount is scoped to `batch/v1 jobs` in `pr-reviewer` namespace only — it cannot read Secrets, modify Deployments, or act outside its namespace
- Secrets are never logged; the Job pod receives them as environment variables, not mounted files
- Jobs self-clean via `ttlSecondsAfterFinished: 3600` — no credential-bearing pods linger
