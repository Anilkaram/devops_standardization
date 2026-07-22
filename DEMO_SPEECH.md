# Demo Speech — DevOps Standardization Tool
*(~5–6 minutes for slides, then live demo. Short sentences on purpose — speak them as written.)*

---

## Slide 1 — Title

Hi everyone. Thanks for joining.

Today I want to show you something I've been building — a **DevOps Standardization Tool**. I want to thank Nanda here, because the original thought for this came from discussions with him.

The idea is simple to say: **you describe your architecture in one small YAML file — and the tool generates the complete, production-ready Terraform for it.** Every service, the networking, the security, the wiring between resources — all of it.

You can see the flow on the slide: a small `infra.yaml` goes in, one command runs, and a full Terraform workspace with forty-plus AWS resources comes out — ready for `terraform plan`.

Let me first show you *why* I built it.

---

## Slide 2 — The problem we all know

Think about what happens today when we need Terraform for a new project.

We don't start from zero — we go hunting. We open some old project, copy the `.tf` files, and start adapting them. And those old files come with problems. **Some are in modules, some are not. Some have security hardening, some don't. Some resources are private in one project and public in another.** There is no single standard.

Then comes the wiring. Connecting an ALB to an autoscaling group, the autoscaling group to RDS, IAM roles to all of them — it looks simple on the architecture diagram, but in Terraform it's many small pieces that must point at each other correctly.

So we lose days searching for templates, and the security review finds the gaps at the very end — when they're most expensive to fix.

These four cards on the slide — inconsistent code, slow bootstrap, security as an afterthought, and dev/prod drift — every one of us has faced them.

---

## Slide 3 — What it does

So here's the approach.

**Step one — describe.** You write `infra.yaml`. The rule is very simple: every box in your architecture diagram becomes a service entry, and every arrow becomes a connection entry. That's it — you're describing the picture, not writing code.

**Step two — generate.** One command, `scaffold init`. The tool picks hardened templates for each service, builds the modules, wires them together, and creates per-environment configuration.

**Step three — deploy.** Normal `terraform plan` and `apply`, per environment.

And look at the numbers: forty-plus resources from about fifteen lines of YAML. A **100% Checkov security score from the very first generate** — not after a week of fixing findings. And the full validation suite runs in about twenty seconds.

---

## Slide 4 — How the Terraform is generated

Quickly, how it works inside — three layers.

**First, the catalog.** Every service maps to a hand-written, security-hardened template. These templates are our standard — written once, reviewed once, used by everyone.

**Second, module assembly.** Each service becomes a proper module — `main.tf` with the resources, `variables.tf` with the declarations, `outputs.tf` for the wiring. Always the same clean structure.

**Third, the root wiring.** The root `main.tf` connects everything — modules get their subnets from the VPC, their keys from KMS, their security groups — automatically, from the connections you described.

And here is the most important design rule — you can see it on the right side: **modules never contain environment values. All actual values live in one place — the tfvars file.** Dev and prod run *identical* code. Only tfvars differ. That is how drift dies.

---

## Slide 5 — Five gates, one command

Now — how do you *trust* generated code? You don't have to. The tool proves it.

One command — `scaffold validate` — runs five gates. Terraform validate for correctness. **Checkov** for security policy — about a hundred and fifty checks. **tfsec** as a *second*, independent security scanner — because each engine catches things the other misses; tfsec actually found a wildcard IAM permission that Checkov passed. Then a **secret scan** that catches plaintext credentials in tfvars with the exact file and line. And a **placeholder check**, so an incomplete stack can never slip through.

If any gate fails, the command exits with an error code — so the **same command is our CI gate**. And one more thing: wherever a check is intentionally skipped, there's a written justification in the code. Nothing is silenced quietly.

---

## Slide 6 — Built for day 2

Generating code once is easy. The real questions come on day two.

*"What if I regenerate — do I lose my manual changes?"* No. Every generated file is fingerprinted. When you re-run the tool, files you edited are detected and **preserved**.

*"How do I know what's left to fill in?"* Run `scaffold doctor` — it lists every remaining value in plain English, with the file and line.

*"Is there AI in this? Can we trust it?"* AI is used only where it helps — describing architecture in plain English, and proposing template updates. But **AI only proposes. The five gates decide.** Nothing AI-generated is accepted without passing the same validation as everything else.

And guardrails are environment-aware — deletion protection and multi-AZ are ON in prod, relaxed in dev, from the same code.

---

## Slide 7 — The whole workflow

This is the entire lifecycle — five commands.

`scaffold init` generates the workspace and prints a checklist of what's left. `scaffold doctor` diagnoses. `init-backend` creates the state bucket, once. `scaffold validate` runs the five gates. Then normal Terraform plan and apply.

Install once with pip, and this is everything a new team member needs to learn.

---

## Slide 8 — What you'll see now

Okay — enough slides. Let me show you the real thing. Here's what I'll do, live:

I'll take an architecture diagram and its `infra.yaml`, run `scaffold init`, and we'll watch the whole workspace appear. I'll walk through one module end-to-end so you can judge the code quality yourselves. Then `scaffold validate` — five green gates. Then I'll deliberately plant a fake password in tfvars and let the gate catch it. And finally I'll edit a generated file, regenerate, and show you the edit survives.

One sentence before I start:

**"This tool doesn't replace our Terraform expertise — it encodes it once, so every project starts at our standard, instead of starting at zero."**

Let's go.

---

## Backup lines (if questions come mid-way)

- **"Why not just use registry modules?"** — We do, where they're best-in-class — the VPC is the community module. What this adds is the layer nobody's library gives: consistent assembly, wiring, naming, per-env config, and the validation gates.
- **"What about existing infrastructure?"** — It's a greenfield tool by design. Brownfield import is on the roadmap.
- **"Who updates the templates?"** — They're code, in git, tested. There's even `scaffold update` — AI proposes refreshes, the gates verify, a human approves.
- **"Does every stack get a VPC?"** — No. Only network-attached services (EC2, RDS, ALB…) trigger a VPC. A pure serverless or static-site stack skips it entirely — no idle NAT gateway cost.
