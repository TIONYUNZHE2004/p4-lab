# Week 5 lab — Programmable Data Planes (P4)

Two P4 software switches (BMv2) in a Mininet network, both running `tunnel.p4`. You'll
watch a P4 pipeline forward IPv4, change its tables at run time, then finish a custom
tunnel header — and see what happens when the parser, the ingress and the deparser
disagree.

**The full step-by-step instructions are in the Lab tab of Week 5 on the course website.**

## Start

Make your own copy first, so you can save your work:

1. At the top of this repository's page, click **Use this template → Create a new repository**.
   Create it under your own account (public or private).
2. In **your** new repository: **Code → Codespaces → Create codespace on main**.

The first start takes a minute or two while the lab image downloads. Then, in the terminal:

```bash
python3 topo.py --id NNNN
```

That compiles `tunnel.p4`, starts the network, fills the switches' tables and opens the
`mininet>` prompt. Type `exit` to stop it; run it again after every change to `tunnel.p4`.

## Assignment

After the lab, the Week 5 assignment continues it: **put a hop limit on the tunnel**. The
instructions are at the end of the Week 5 Lab tab on the course website. Work on a branch,
write `assignment/REPORT.md`, save your evidence in `assignment/evidence/`, and submit your
pull request's link on Spectrum. Every push is checked automatically: the **Compile tunnel.p4**
check must be green.

`python3 topo.py --id NNNN --s2-program tunnel_v1.p4` runs s2 with a different program, for the
assignment's mixed-rollout experiment.

## Files

| File | What it is |
|---|---|
| `tunnel.p4` | The P4 program both switches run. You edit this. |
| `topo.py` | Builds the network: h1 — s1 — s2 — h2, h3. |
| `s1-commands.txt`, `s2-commands.txt` | Table entries loaded into each switch at start. |
| `send.py` | Sends one test packet, optionally tunnelled (run it on h1). |
| `receive.py` | Started automatically on h2 and h3; output in `logs/h2.txt`, `logs/h3.txt`. |
| `assignment/REPORT.md` | The assignment's report template. |
| `p4cli` | Sends one command to a switch's runtime CLI, e.g. `./p4cli s2 table_dump MyIngress.ipv4_lpm`. |
| `logs/s1.txt`, `logs/s2.txt` | What each switch did with every packet: parser states, tables, actions. |

## If something goes wrong

- **Mininet complains that interfaces already exist** (after a crash): run `mn -c`, then `python3 topo.py --id NNNN` again.
- **Compilation failed**: the message gives the line number in `tunnel.p4`. Check for a missing `;` or `}`.
- **VS Code shows "cannot open source file core.p4"** (or `v1model.p4`) on `tunnel.p4`: that's
  VS Code's C checker, not the P4 compiler — ignore it. The errors that matter come from
  `python3 topo.py`.
- **The codespace stopped**: codespaces stop after 30 minutes idle. Restart it from github.com/codespaces; your edits are kept.
- **Save your work**: commit and push from the codespace (Source Control panel, or `git` in the
  terminal) — it goes to your own repository.
- **When you've finished the lab**, delete the codespace at github.com/codespaces so it stops using your storage quota.

## Reproduce the hop-limit experiments

On the `hop-limit` branch, use the course Codespace and first exit any interactive
Mininet session. From the repository root, run:

```bash
python3 assignment/run_checks.py --id 4941
```

The runner compiles the old and new programs, starts fresh instances of the supplied
topology, and saves real command output in `assignment/evidence/`. It covers the
finished lab, the missing-deparser experiment, the original loop, tunnel delivery
and hop-limit boundary cases, the bounded loop, the mixed old/new rollout, and plain
IPv4 ping. A failure stops the run. After all checks pass it fills
`assignment/REPORT.md` from these measurements. The submitted report and evidence
already contain the completed Codespace run.

For a manual new-format packet, start `python3 topo.py --id 4941`, then use
`h1 python3 send.py 10.41.0.3 "test" --tunnel 2 --hops 8` at `mininet>`.
