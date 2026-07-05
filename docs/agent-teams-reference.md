# Agent Teams — Master Reference Guide

> Source: https://code.claude.com/docs/en/agent-teams  
> Version: Claude Code v2.1.186+  
> Status: **Experimental** — requires `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`

---

## Table of Contents

1. [What Are Agent Teams?](#what-are-agent-teams)
2. [Enable Agent Teams](#enable-agent-teams)
3. [When to Use Agent Teams](#when-to-use-agent-teams)
4. [Agent Teams vs Subagents](#agent-teams-vs-subagents)
5. [Starting an Agent Team](#starting-an-agent-team)
6. [Display Modes](#display-modes)
7. [Controlling Your Team](#controlling-your-team)
8. [Architecture & Internals](#architecture--internals)
9. [Permissions & Context](#permissions--context)
10. [Token Usage](#token-usage)
11. [Hooks for Quality Gates](#hooks-for-quality-gates)
12. [Best Practices](#best-practices)
13. [Use Case Examples](#use-case-examples)
14. [Troubleshooting](#troubleshooting)
15. [Known Limitations](#known-limitations)

---

## What Are Agent Teams?

Agent teams let you coordinate **multiple Claude Code instances** working together. One session is the **team lead** — it coordinates work, assigns tasks, and synthesizes results. **Teammates** work independently in their own context windows and can communicate directly with each other.

Key difference from subagents: teammates can message each other directly without routing through the lead.

---

## Enable Agent Teams

Add to `.claude/settings.local.json` (project-local, not committed):

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  }
}
```

Or set in your shell environment:

```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

Without this variable: no team is set up, no directories written, Claude won't spawn or propose teammates.

---

## When to Use Agent Teams

**Best fit — parallel exploration adds real value:**

| Use Case | Why Teams Work |
|----------|---------------|
| Research & review | Multiple teammates investigate different aspects simultaneously |
| New modules/features | Each teammate owns a separate piece without conflicts |
| Debugging competing hypotheses | Test different theories in parallel, converge faster |
| Cross-layer changes | Frontend, backend, tests each owned by a different teammate |

**Poor fit — use a single session or subagents instead:**

- Sequential tasks with dependencies
- Same-file edits (causes conflicts)
- Routine/simple tasks (coordination overhead not worth it)
- Many tightly-coupled dependencies

---

## Agent Teams vs Subagents

| | Subagents | Agent Teams |
|--|-----------|-------------|
| **Context** | Own context window; results return to caller | Own context window; fully independent |
| **Communication** | Report results back to main agent only | Teammates message each other directly |
| **Coordination** | Main agent manages all work | Shared task list with self-coordination |
| **Best for** | Focused tasks where only the result matters | Complex work needing discussion & collaboration |
| **Token cost** | Lower — results summarized back to main context | Higher — each teammate is a separate Claude instance |

**Rule of thumb:** Use subagents when you need quick focused workers that report back. Use agent teams when teammates need to share findings, challenge each other, and coordinate on their own.

---

## Starting an Agent Team

After enabling, describe the task and teammates in natural language. Claude spawns and coordinates based on your prompt.

```
I'm designing a CLI tool that helps developers track TODO comments across
their codebase. Spawn three teammates to explore this from different angles:
one on UX, one on technical architecture, one playing devil's advocate.
```

Claude will:
1. Populate a shared task list
2. Spawn teammates for each perspective
3. Have them explore the problem
4. Synthesize findings when finished

The lead's terminal lists teammates in an **agent panel** below the prompt input:

| Key | Action |
|-----|--------|
| `↑` / `↓` | Select a teammate |
| `Enter` | Open teammate's transcript and message it directly |
| `Escape` | Interrupt the selected teammate's current turn |
| `x` | Stop the selected teammate (in-process mode) |
| `Ctrl+T` | Toggle the task list |

> **Note:** As of v2.1.181, an idle teammate's row hides after 30 seconds and reappears on its next turn. The teammate is still running and addressable while hidden.

---

## Display Modes

### In-Process (Default)

All teammates run inside your main terminal. Use arrow keys to select, Enter to view and type. Works in any terminal, no extra setup.

### Split Panes

Each teammate gets its own pane — you can see everyone's output at once and click into a pane to interact directly. Requires tmux or iTerm2.

**Set globally** in `~/.claude/settings.json`:

```json
{
  "teammateMode": "auto"
}
```

**Set for a single session:**

```bash
claude --teammate-mode auto
```

| Mode value | Behavior |
|------------|----------|
| `"in-process"` | Default — all teammates in one terminal |
| `"auto"` | Split panes if inside tmux or iTerm2, else in-process |
| `"tmux"` | Split panes, auto-detects tmux vs iTerm2 |
| `"iterm2"` | iTerm2 native split panes (requires `it2` CLI) |

**Install split-pane requirements:**

```bash
# tmux (macOS)
brew install tmux

# iTerm2: install it2 CLI, then enable Python API in
# iTerm2 → Settings → General → Magic → Enable Python API
```

> **Note:** Before v2.1.179, the default was `"auto"`. Upgraded sessions now stay in one terminal unless you explicitly set the mode.  
> **Note:** Split panes are NOT supported in VS Code's integrated terminal, Windows Terminal, or Ghostty.

---

## Controlling Your Team

### Specify Teammates and Models

```
Spawn 4 teammates to refactor these modules in parallel. Use Sonnet for each teammate.
```

- Teammates **don't inherit** the lead's `/model` selection by default
- To change default: set **Default teammate model** in `/config`, or choose **Default (leader's model)** to follow the lead
- Teammates **do inherit** the lead's effort level (from v2.1.186 in split-pane mode)

### Require Plan Approval

For complex/risky tasks — teammate works in read-only plan mode until the lead approves:

```
Spawn an architect teammate to refactor the authentication module.
Require plan approval before they make any changes.
```

Flow:
1. Teammate finishes planning → sends plan approval request to lead
2. Lead reviews → approves or rejects with feedback
3. If rejected → teammate revises and resubmits
4. If approved → teammate exits plan mode and begins implementation

To influence lead's decisions: add criteria in your prompt, e.g. `"only approve plans that include test coverage"`.

### Talk to Teammates Directly

- **In-process:** arrow keys to select → Enter to view → type to send message
- **Split-pane:** click into teammate's pane

### Assign and Claim Tasks

Shared task list coordinates work. Three task states: **pending**, **in progress**, **completed**.

Tasks can have **dependencies** — a task with unresolved dependencies can't be claimed until those are completed.

- **Lead assigns:** tell the lead which task to give which teammate
- **Self-claim:** teammate auto-picks next unassigned, unblocked task after finishing

File locking prevents race conditions when multiple teammates try to claim the same task.

### Shut Down Teammates

```
Ask the researcher teammate to shut down
```

Lead sends a shutdown request. Teammate can approve (exits gracefully) or reject with explanation. Team shared directories are cleaned up automatically when the session ends.

---

## Architecture & Internals

### Components

| Component | Role |
|-----------|------|
| **Team lead** | Main Claude Code session — spawns teammates, coordinates work |
| **Teammates** | Separate Claude Code instances, each working on assigned tasks |
| **Task list** | Shared list of work items teammates claim and complete |
| **Mailbox** | Messaging system for inter-agent communication |

### File Locations

Team name format: `session-` + first 8 chars of session ID

| What | Path |
|------|------|
| Team config | `~/.claude/teams/{team-name}/config.json` |
| Task list | `~/.claude/tasks/{team-name}/` |

- **Team config directory** is removed when the session ends (runtime state only — don't edit by hand)
- **Task list directory** persists locally across sessions (never uploaded, governed by `cleanupPeriodDays`)
- The `members` array in team config has each teammate's name, agent ID, and agent type — readable by teammates to discover each other

> **Important:** A `.claude/teams/teams.json` file in your project directory is NOT recognized as config — Claude treats it as an ordinary file.

### How Teams Form

Two ways teammates get spawned:
1. **You request them** — explicitly ask for teammates in your prompt
2. **Claude proposes them** — Claude suggests teammates for complex tasks; you confirm before it proceeds

Claude will never spawn teammates without your approval.

### Using Subagent Definitions for Teammates

Reference a [subagent](https://code.claude.com/docs/en/sub-agents) type when spawning:

```
Spawn a teammate using the security-reviewer agent type to audit the auth module.
```

- Teammate honors the definition's `tools` allowlist and `model`
- Definition body is **appended** to the teammate's system prompt (not a replacement)
- Team coordination tools (`SendMessage`, task management) are always available even when `tools` restricts others
- **Not applied** for teammate subagent definitions: `skills` and `mcpServers` frontmatter fields (teammates load these from project/user settings instead)

---

## Permissions & Context

### Permissions

- Teammates start with the **lead's permission settings**
- If lead runs with `--dangerously-skip-permissions`, all teammates do too
- You can change individual teammate modes after spawning
- You **cannot** set per-teammate modes at spawn time

### Context Each Teammate Gets

- Same project context as a regular session: `CLAUDE.md`, MCP servers, skills
- The spawn prompt from the lead
- **Does NOT inherit** the lead's conversation history

### How Teammates Share Information

| Mechanism | How it works |
|-----------|-------------|
| **Automatic message delivery** | Messages delivered automatically to recipients — lead doesn't need to poll |
| **Idle notifications** | Teammate auto-notifies lead when it finishes and stops |
| **Shared task list** | All agents see task status and can claim available work |
| **Direct messaging** | Any teammate can message any other by name |

> To reach everyone: send one message per recipient (no broadcast).  
> For predictable names: tell the lead what to call each teammate in your spawn instruction.

---

## Token Usage

Agent teams use **significantly more tokens** than a single session. Token usage scales linearly with active teammates — each has its own context window.

**Worth it for:** research, review, new feature work where parallel exploration has high value.  
**Not worth it for:** routine tasks where a single session suffices.

Start with 3–5 teammates. Rule of thumb: **5–6 tasks per teammate** keeps everyone productive without excessive context switching.

---

## Hooks for Quality Gates

Three hook events specific to agent teams:

### `TeammateIdle`

Runs when a teammate is about to go idle.

- **Exit code 2** → send feedback and keep the teammate working

### `TaskCreated`

Runs when a task is being created.

- **Exit code 2** → prevent creation and send feedback

### `TaskCompleted`

Runs when a task is being marked complete.

- **Exit code 2** → prevent completion and send feedback

Hook payloads for `TaskCreated` and `TaskCompleted` include a `team_name` field (deprecated — carries session-derived name now).

---

## Best Practices

### Give Teammates Enough Context

Teammates don't inherit the lead's conversation history. Include task-specific details in the spawn prompt:

```
Spawn a security reviewer teammate with the prompt: "Review the authentication module
at src/auth/ for security vulnerabilities. Focus on token handling, session
management, and input validation. The app uses JWT tokens stored in
httpOnly cookies. Report any issues with severity ratings."
```

### Team Size Guidelines

| Team size | When to use |
|-----------|-------------|
| 3–5 teammates | Most workflows — good balance of parallel work vs coordination |
| 5+ teammates | Only when work genuinely benefits from simultaneous operation |

**Diminishing returns:** more teammates = more communication overhead, more potential conflicts.

### Task Sizing

| Size | Problem |
|------|---------|
| Too small | Coordination overhead exceeds the benefit |
| Too large | Teammates work too long without check-ins, increasing wasted-effort risk |
| Just right | Self-contained units with a clear deliverable (a function, a test file, a review) |

Tip: If the lead isn't creating enough tasks, ask it to split the work into smaller pieces.

### Other Practices

- **Avoid file conflicts:** break work so each teammate owns different files
- **Wait for teammates:** if the lead starts implementing itself instead of waiting, say: `"Wait for your teammates to complete their tasks before proceeding"`
- **Monitor and steer:** check in on progress, redirect approaches that aren't working, synthesize findings as they come in
- **Start with research/review:** if new to agent teams, start with tasks that don't require writing code (reviewing a PR, researching a library, investigating a bug)
- **Pre-approve permissions:** in your permission settings before spawning, to reduce interruptions from teammates bubbling up requests

---

## Use Case Examples

### Parallel Code Review

```
Spawn three teammates to review PR #142:
- One focused on security implications
- One checking performance impact
- One validating test coverage
Have them each review and report findings.
```

Each reviewer applies a different filter to the same PR. Lead synthesizes across all three.

### Competing Hypotheses Debug

```
Users report the app exits after one message instead of staying connected.
Spawn 5 agent teammates to investigate different hypotheses. Have them talk to
each other to try to disprove each other's theories, like a scientific
debate. Update the findings doc with whatever consensus emerges.
```

Why this works: sequential investigation anchors on the first plausible theory. Multiple independent investigators actively trying to disprove each other means the surviving theory is much more likely to be the actual root cause.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Teammates not appearing | Check agent panel (below prompt input). Idle rows hide after 30s — send teammate a message by name to bring back. |
| Teammate disappeared | Hidden ≠ stopped. Send a message by name to bring it back. |
| Too many permission prompts | Pre-approve common operations in permission settings before spawning |
| Teammate stopped on error | Select in agent panel → Enter to view → give instructions, or spawn a replacement |
| Lead shuts down early | Tell it to keep going: `"Wait for teammates to finish before proceeding"` |
| Orphaned tmux sessions | `tmux ls` then `tmux kill-session -t <session-name>` |
| Split panes not working | Check tmux is installed: `which tmux`. For iTerm2: verify `it2` CLI installed and Python API enabled. |

---

## Known Limitations

| Limitation | Detail |
|-----------|--------|
| No session resumption for in-process teammates | `/resume` and `/rewind` don't restore in-process teammates. Lead may try to message teammates that no longer exist — tell it to spawn new ones. |
| Task status can lag | Teammates sometimes fail to mark tasks complete. Check if work is done and update status manually or tell the lead to nudge the teammate. |
| Slow shutdown | Teammates finish their current request or tool call before shutting down. |
| One team per session | Can't create additional named teams or share a team across sessions. |
| No nested teams | Teammates cannot spawn their own teammates — only the lead can. |
| Lead is fixed | Can't promote a teammate to lead or transfer leadership. |
| Permissions set at spawn | Can change individual teammate modes after spawning, but not at spawn time. |
| Split panes limited | Not supported in VS Code integrated terminal, Windows Terminal, or Ghostty. |

---

## Version History Notes

| Version | Change |
|---------|--------|
| v2.1.178 | Spawning a teammate no longer needs a setup step; cleanup is automatic. `TeamCreate` and `TeamDelete` tools removed. |
| v2.1.179 | Default display mode changed from `"auto"` to `"in-process"`. |
| v2.1.181 | Idle teammate rows hide after 30 seconds; reappear on next turn. |
| v2.1.186 | `"iterm2"` mode added. Teammates now inherit lead's effort level in split-pane mode. |
