# Tag Vocabulary

> Curated list of every tag an agent is allowed to declare.
> Source of truth: [`tags.json`](tags.json).
> Linter rejects any tag not in this list.

## Why a fixed vocabulary

Auto-generated tags (`json`, `JSON`, `Json`, `json-parsing`) fragment the
routing index. A curated list makes the orchestrator's job tractable:
when it looks up `by_tag.security`, it gets every agent that should be
there, not half of them plus a scatter of `secure`, `sec`, `security-audit`.

New tags aren't forbidden — they require one thing: **add the tag to
`tags.json` in the same MR as the agent that uses it**. That discipline
is the vocabulary's immune system.

## Layers

Every tag lives in one of six layers. An agent typically carries 5–10
tags spread across `skill`, `domain`, `tech`, and `concern`.

| Layer | What it answers | Typical count per agent |
|-------|-----------------|-------------------------|
| `skill` | What the agent does functionally (backend, review, growth, qa) | 1–3 |
| `domain` | Subject matter (fintech, blockchain, gamedev, xr) | 1–2 |
| `tech` | Concrete stack / platform (react, postgres, unity, tiktok) | 0–3 |
| `concern` | Non-functional property (security, performance, a11y) | 0–3 |
| `workflow` | Output mode (implementation, audit, reporting) | 0–1 |
| `marker` | Pack infrastructure tags (template, bootstrap) | 0–1 |

## Layer summaries

### `skill` — what the agent does

Engineering roles: `backend`, `frontend`, `fullstack`, `devops`, `sre`,
`infra`, `data-engineering`, `data-science`, `ai`, `ml`, `llm`,
`mobile`, `embedded`.

Design: `ui-design`, `ux-design`, `ux-research`, `brand-design`,
`visual-storytelling`, `whimsy`, `prompt-design`.

Testing: `qa`, `e2e-testing`, `api-testing`, `performance-testing`,
`accessibility-testing`, `evidence-collection`, `reality-check`.

Protocol-bound: `review`, `audit`, `scout`, `orchestration`.

Writing: `technical-writing`, `content-creation`, `book-authoring`.

Marketing / growth: `growth`, `seo`, `aso`, `aeo`, `social-media`,
`community-building`, `livestream-commerce`, `localization`.

Paid media: `ppc`, `paid-social`, `programmatic`, `tracking`,
`paid-audit`, `search-analysis`.

Sales: `outbound`, `discovery-selling`, `deal-strategy`,
`sales-engineering`, `proposal-writing`, `pipeline-analysis`,
`account-expansion`, `sales-coaching`.

Product / PM: `product-management`, `prioritization`,
`feedback-analysis`, `trend-research`, `behavioral-design`,
`project-planning`, `experiment-tracking`, `jira-workflow`,
`studio-ops`.

Finance: `bookkeeping`, `fpa`, `tax-strategy`, `investment-research`.

Support: `customer-support`, `legal-compliance`, `analytics-reporting`,
`executive-summary`, `finance-tracking`, `infra-maintenance`.

Academic: `historical-analysis`, `anthropology`, `psychology`,
`narratology`, `geography`.

Game craft: `game-design`, `level-design`, `technical-art`,
`game-audio`, `narrative-design`.

Security / ops specializations: `threat-modeling`, `threat-detection`,
`smart-contract-audit`, `compliance-audit`, `incident-response`,
`identity-engineering`, `workflow-design`, `document-generation`,
`automation-governance`, `knowledge-management`, `recruiting`,
`training-design`, `supply-chain`.

### `domain` — subject matter

Platforms: `web`, `api`, `microservices`, `event-driven`, `serverless`,
`cli-tools`.

Finance: `fintech`, `banking`, `payments`, `trading`, `escrow`,
`wallet`, `ledger`.

Blockchain: `blockchain`, `defi`, `nft`, `smart-contracts`, `zk-proofs`,
`mev`.

Games: `gamedev`, `multiplayer`, `open-world`.

Verticals: `healthcare`, `gov-tech`, `education`.

B2B: `saas`, `multi-tenant`.

Reality: `xr`, `vr`, `ar`, `webxr`, `visionos`, `cockpit`.

Academic: `academic`.

Regional: `china-market`, `cross-border`, `korean-market`,
`french-market`.

Commerce / community: `marketplace`, `ecommerce`, `devrel`.

### `tech` — concrete stack

Languages: `python`, `java`, `kotlin`, `scala`, `go`, `rust`, `swift`,
`typescript`, `javascript`, `ruby`, `php`, `c-cpp`, `solidity`,
`gdscript`, `luau`, `hlsl`, `glsl`, `metal`.

Web / backend frameworks: `react`, `react-native`, `vue`, `angular`,
`next`, `svelte`, `node`, `spring`, `django`, `rails`.

Game engines: `unity`, `unreal`, `godot`, `roblox`, `blender`.

Data systems: `postgres`, `mysql`, `mongodb`, `redis`, `elasticsearch`,
`kafka`.

Infra: `docker`, `kubernetes`, `terraform`, `aws`, `gcp`, `azure`.

Mobile: `ios`, `android`, `flutter`.

CMS / SaaS: `wordpress`, `drupal`, `cms`, `salesforce`.

Chinese platforms: `wechat`, `feishu`, `bilibili`, `xiaohongshu`,
`weibo`, `douyin`, `kuaishou`, `zhihu`, `baidu`.

Global platforms: `tiktok`, `instagram`, `twitter`, `linkedin`,
`reddit`, `youtube`.

Blockchain nets: `ton`, `ethereum`, `solana`.

AI infra: `mcp`.

### `concern` — non-functional

Security: `security`, `auth`, `authz`, `secrets`, `owasp`.

Performance: `performance`, `caching`, `scaling`.

Reliability: `reliability`, `observability`, `chaos-engineering`.

Compliance: `privacy`, `gdpr`, `pci`, `soc2`, `hipaa`.

UX / code: `a11y`, `architecture`, `refactoring`, `minimal-change`,
`database-design`, `query-optimization`,
`observability-instrumentation`, `regression`.

### `workflow` — output mode

`implementation`, `design`, `strategy`, `reporting`, `coaching`,
`estimation`, `coordination`.

### `marker` — pack infra only

`template` (for files in `agents/templates/`), `bootstrap` (used in
memory templates).

## Using the vocabulary

### From the migrator

`scripts/migrate_schema.py` loads `tags.json` on start, then for each
agent:

1. Seeds tags with the category's `per_category_defaults` + the
   agent's own `category`.
2. Scans the filename, description, and body for tag names and synonyms.
3. Filters out foreign-category tags so `marketing` tags don't leak onto
   `engineering` agents.

### From the linter

`scripts/lint_agents.py` loads `tags.json` and rejects any tag not in the
vocabulary with a clear error message, including the closest match
suggestion.

### Adding a new tag

When an agent needs a tag that's not here:

1. Pick a layer.
2. Add it to `agents/tags.json` with a short `synonyms` list (other ways
   authors might write it).
3. Add it under the appropriate layer section of this document.
4. Run `scripts/migrate_schema.py` to ensure re-migration is idempotent.
5. Run `scripts/build_index.py` to refresh `index.json`.
6. Commit all four changes together.

## Disambiguation tags

Agents with overlapping category/tag footprints may declare an optional
`distinguishes_from` frontmatter field listing slugs they're often
confused with. The orchestrator uses this as a tie-breaker when tag
intersection alone is ambiguous. Example:

```yaml
distinguishes_from: [engineering-security-engineer, blockchain-security-auditor]
```

`engineering-code-reviewer` with that field says: "when picking a
security reviewer I'm the lighter-weight code review variant; for full
threat modelling delegate to `engineering-security-engineer`."

## Anti-patterns

- Tagging with the agent's own role noun (`engineer`, `specialist`). The
  vocabulary deliberately excludes those; they add nothing for routing.
- Tagging a foreign category. `engineering-security-engineer` is not a
  `support` agent even if the body mentions "decision support".
- One-off tags (`my-favourite-framework-v3`). If it's not in `tags.json`,
  the linter fails the PR.
