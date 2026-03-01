# PRGenie — Executive Overview

## What Is PRGenie?

PRGenie is an AI-powered code review system that acts as an always-available senior
engineer on every pull request. It reads the full code change, understands what the
developer was trying to do, and posts specific, actionable feedback — including the
corrected code — directly in the PR, before a human reviewer ever opens it.

It runs at two points in the development lifecycle:

| When | Mode | Cost |
|---|---|---|
| **Before the commit** | Local MCP reviewer in the IDE | Free (uses IDE's existing AI) |
| **After the PR opens** | `/pr-review` Claude Code skill | Free (uses Claude Pro subscription) |
| **Automatically, at scale** | EKS event-driven on every PR | API cost only |

---

## The Problem It Solves

### Code review is expensive, inconsistent, and slow

- Senior engineers spend **4–8 hours per week** on code reviews — time taken away from
  building.
- Review quality varies by reviewer, time of day, and how many reviews are queued.
- The same class of bug (null dereference, missing auth check, SQL injection) gets caught
  in some PRs and missed in others.
- Feedback loops are long: a developer pushes → waits for review → fixes → waits again.

### Catching bugs late is catastrophically more expensive

Based on IBM and NIST research on cost of software defects:

| Stage defect found | Cost to fix |
|---|---|
| During code review | ~$150 |
| In QA / testing | ~$1,500 |
| In production | ~$10,000–$50,000 |

PRGenie shifts detection to the earliest possible point — before the developer even commits.

---

## The Claude Code Native Advantage

PRGenie's flagship delivery mode — the `/pr-review` Claude Code skill — is fundamentally
different from every other AI code review tool on the market. Here is why.

### 1. Full context, not just the diff

Every other tool reviews only the changed lines. PRGenie fetches:

- **Full file content** at the PR's HEAD commit — sees the entire function, not just the patch
- **Related test files** — can tell when a new function has no tests
- **Recent git history** for each changed file — understands why the code was written
- **Existing PR comments** — never posts a duplicate finding

This is the difference between a reviewer who reads "line 42 changed from X to Y" and one
who reads the whole class, checks the tests, and looks at the last five commits.

### 2. Semantic understanding, not pattern matching

Traditional static analysis tools (SonarQube, CodeClimate, ESLint) match code against
fixed rule libraries. They cannot reason about intent.

PRGenie runs five structured reasoning passes:

| Pass | What it asks |
|---|---|
| **INTENT** | Does the code actually do what the PR description says? |
| **LOGIC & BUGS** | Are there null dereferences, off-by-ones, race conditions, wrong types? |
| **SECURITY** | Injection, path traversal, auth bypass, sensitive data in logs? |
| **QUALITY** | Does the code follow this team's conventions and patterns? |
| **TESTS** | Are new public functions covered? Are existing tests still valid? |

This catches logic bugs that no static analyser can find — a missing `await`, a wrong
conditional, a function that silently returns `undefined` in an error branch.

### 3. Gets smarter about your codebase over time

PRGenie learns your team's specific conventions via `/repo-onboard`:

- Run once per repo
- Generates a `repo_context.json` capturing naming conventions, architecture patterns,
  security-sensitive files, known pitfalls, and test conventions
- Every subsequent review applies this knowledge — PRGenie knows that in *this* repo,
  semicolons are banned, Bun's `$` tag is used instead of `child_process.spawn`, and
  any PR touching `auth/index.ts` warrants extra scrutiny

### 4. Team feedback loop — suppressions

PRGenie doesn't just post findings. It learns from the team's responses:

- When a reviewer marks a finding as "not an issue" or resolves the thread, `/pr-feedback`
  harvests that signal and writes a suppression rule to `suppressions.json`
- Future reviews skip that class of finding in that scope — no more false-positive fatigue
- Suppression rules are stored in the repo (committable, team-shared) with an optional
  expiry date
- **Hard invariant:** CRITICAL bugs and SECURITY findings are never suppressed, ever

### 5. Integrates with your existing security toolchain

PRGenie uses a file-based drop protocol to ingest findings from Snyk, SonarQube, and JIRA
without any wiring changes:

- A CI step runs Snyk and drops a JSON file into `.pr-reviewer/context/snyk.json`
- PRGenie reads it at review time, merges the findings, and attributes them: "source: snyk"
- One unified review comment includes both Claude's reasoning and the Snyk vulnerability ID

Adding a new tool requires writing one adapter script. PRGenie itself never changes.

### 6. Zero API overhead — always 4 calls, never more

Posting a review with 20 inline comments costs exactly the same as posting one with 0.
PRGenie batches all inline comments into a single GitHub API call using the batch review
endpoint and a diff position map. This is not how other tools work — most loop over
findings and make one API call per comment, creating rate-limit risk and slow posting.

---

## Competitive Comparison

| Capability | PRGenie (Claude Code) | SonarQube / CodeClimate | GitHub Copilot Review | DeepCode / Snyk Code |
|---|---|---|---|---|
| Reviews full file, not just diff | ✅ | ❌ | ❌ | ❌ |
| Understands PR intent | ✅ | ❌ | Partial | ❌ |
| Logic & semantic bug detection | ✅ | ❌ | Partial | ❌ |
| Security finding | ✅ | Partial (rules only) | Partial | ✅ (security only) |
| Test coverage gap detection | ✅ | Partial | ❌ | ❌ |
| Repo-specific conventions | ✅ (repo_context.json) | ❌ | ❌ | ❌ |
| Team feedback loop / suppressions | ✅ | ❌ | ❌ | ❌ |
| Ingests Snyk / Sonar / JIRA data | ✅ | N/A | ❌ | N/A |
| Pre-commit review (before push) | ✅ (MCP) | ❌ | ❌ | ❌ |
| Auto-review every PR (no trigger) | ✅ (EKS) | Partial (CI plugin) | ❌ | Partial (CI plugin) |
| Corrected code in every comment | ✅ | ❌ | Partial | ❌ |
| Inline comments — batch API | ✅ (1 call) | N/A | N/A | N/A |
| No extra API cost (Claude Pro) | ✅ | ❌ (paid tier) | ❌ (paid add-on) | ❌ (paid tier) |
| Open source / self-hosted | ✅ | ✅ (Community) | ❌ | ❌ |

---

## Key Talking Points for Senior Management

### "This runs on our existing Claude Pro subscription — there is no new line item."

The Claude Code skill mode uses Claude Pro/Max, which your developers likely already have.
Installing PRGenie is two file copies. There is no new vendor, no new contract, no new
SaaS dashboard to manage.

### "It catches the bugs that escape human review."

Human reviewers are great at architecture feedback and design discussion. They are
inconsistent at catching null dereferences, missing awaits, incorrect auth logic, and
edge cases — especially under time pressure. PRGenie is consistent. It checks every line
of every PR with the same thoroughness at 2am on a Friday as it does at 10am on a Tuesday.

### "It left-shifts detection all the way to the developer's laptop."

The MCP local reviewer means a developer can get a full security and logic review of staged
changes before they even commit — no PR, no push, no waiting. This is the cheapest point in
the lifecycle to find and fix a defect.

### "It gets smarter about our codebase, not just code in general."

After a one-time `/repo-onboard` run, PRGenie knows your naming conventions, your
architecture patterns, which files are security-sensitive, and what the known pitfalls in
your codebase are. A generic AI tool gives generic feedback. PRGenie gives feedback calibrated
to how *your* team writes code.

### "The false-positive problem is solved."

Every AI review tool eventually becomes noise if it keeps flagging the same things the team
doesn't care about. PRGenie's suppression system means the team's judgment is captured once
and applied forever — with a full audit trail of who suppressed what, when, and why.
Security findings can never be suppressed.

### "It scales to zero effort — every PR is reviewed, automatically."

The EKS deployment means no developer needs to remember to run a review. Every PR opened
on GitHub triggers a webhook, which spawns a Kubernetes Job, which posts the review. It
is fully hands-off once deployed.

### "It unifies the output of all our existing security tools."

Snyk found a vulnerability. SonarQube flagged a code smell. JIRA says the ticket is
already closed. Today, a developer gets three separate emails/comments from three separate
systems. PRGenie aggregates all of this into one unified review comment with consistent
severity labelling and a single place to respond.

---

## Estimated Value Delivered

PRGenie's summary comment includes a **Review Value Metrics** section on every PR, based
on IBM/NIST cost-of-defect research:

```
| Metric                        | Value                                  |
|-------------------------------|----------------------------------------|
| Files reviewed                | 4                                      |
| Lines of diff                 | 180 added / 12 removed                 |
| Findings                      | 8 (1 critical · 2 high · 3 med · 2 low)|
| Avg confidence                | 91%                                    |
| Est. defect prevention value  | ~$52,500                               |
| Est. reviewer time saved      | ~1.5 hrs (~$300)                       |
```

These are conservative estimates. A single prevented production incident — one SQL
injection, one authentication bypass, one data corruption bug — typically justifies the
entire toolchain cost for the year.

---

## Deployment Options Summary

| Option | Setup effort | Infrastructure | Best for |
|---|---|---|---|
| **Claude Code skill** | 2 file copies, 5 min | None | Individual devs, small teams |
| **MCP local reviewer** | `pip install` + 1 command | None | Pre-commit safety net |
| **Standalone CLI** | `pip install` + 2 env vars | None / CI runner | CI/CD pipeline integration |
| **EKS event-driven** | 1–2 days (Helm/kubectl) | EKS cluster | Enterprise, full automation |

Start with the Claude Code skill — zero infrastructure, zero cost, results in 5 minutes.
Add EKS when you need every PR reviewed automatically without any developer action.

---

## Getting Started

```bash
# 1. Install the skill (2 minutes)
mkdir -p ~/.claude/skills/pr-review
cp skill/SKILL.md ~/.claude/skills/pr-review/SKILL.md
cp skill/post_review.py ~/.claude/skills/pr-review/post_review.py

# 2. Onboard your repo (once per repo, ~3 minutes)
/repo-onboard

# 3. Review any PR
/pr-review https://github.com/your-org/your-repo/pull/123
```

Full EKS deployment guide: [`deploy/README.md`](../deploy/README.md)
Full architecture reference: [`docs/architecture.md`](./architecture.md)
