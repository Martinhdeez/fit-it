# Product vision

## One line

Fit-it gives every user of an existing web app their own version of it, described in plain language, with zero changes to the app's code.

## Problem

Users of business software want small, personal changes: a discount rule, a warning badge, a hidden column, a chart nobody built. Today each one is a ticket, a sprint and a deploy, so most never happen. The app's team cannot build a variant per user, and users cannot touch the code.

## Solution

Fit-it is a reverse proxy placed in front of the app. A user describes a change in a chat widget. A coding agent turns the request into a small Python hook that only applies to that user. From then on, the proxy runs the hook on every matching response before it reaches the user. The original app never changes.

## Users

| Who | What they get |
|---|---|
| End user (receptionist, doctor, manager) | Changes the app to fit their own work, by asking |
| App operator (the company running the app) | Personalization without touching or redeploying the app; an audit trail of every change |
| App developer | Fewer one-off feature requests; a record of what users actually need |

## Principles

1. **Zero modifications.** The client app is deployed as-is behind the proxy.
2. **Never more than the user can already see.** Hooks reach data only through the app's own API, with the user's own session.
3. **Fail closed.** If a hook breaks, the user gets an error, never unfiltered data.
4. **Every change is traceable.** Prompt, generated code, validation, diff and every execution are recorded.

## Non-goals (for now)

- Direct database access (see [ADR 0003](../adr/0003-api-only-access-to-client-data.md)).
- Hooks in languages other than Python.
- Sharing hooks between users or promoting them to the base app.
- Production-grade isolation of hook code (see [ADR 0004](../adr/0004-per-user-python-hooks-in-process.md)).

## Prior art

| Product | Overlap | Difference |
|---|---|---|
| Tweeks (YC W25) | Natural-language, per-user website changes | Browser extension; DOM only; installed by the user; no governance |
| HCL Connections Customizer | Proxy that injects JS/CSS into an app | Hand-written rules, one vendor's product |
| API gateways (Apigee, Gravitee) | Hook points in a proxy flow | Developer-written policies, per tenant, not per user |
| Shopify Functions, Auth0 Actions | Custom logic at fixed extension points | Built into one platform, written by developers |

Fit-it differs by changing server-side behaviour and data, by being deployed by the operator for all users, and by inheriting the app's permissions.
