# Week 5 assignment — Put a limit on the tunnel

Student ID (last 4 digits only): **4941**

The experiments use my own address block, `10.41.0.0/16`, in the course Codespace.
`assignment/run_checks.py` runs the supplied topology and records the commands and real
outputs. It stops if a required check fails. The transcripts include UTC timestamps and
hashes of the programs used. Report generated after the checks: 2026-11-12T15:39:11.602986+00:00.

## 1. Analysis: the loop

I completed the lab by parsing and emitting MyTunnel and applying `myTunnel_exact`
before the IPv4 table. `tunnel_v1.p4` preserves that finished program. The lab's
`test 3` reaches h2 even though its IPv4 destination is h3, with inner IPv4 TTL 64.
The deliberate missing-deparser experiment is also recorded separately.

To make the loop, I dumped s2's tunnel table, found the actual handle for exit 3,
and changed its action parameter from port 3 to port 1. s1 already forwards exit 3
on port 2 towards s2. A packet therefore follows `s1 → s2 → s1 → …`.
The exact dump, handle, modification and sender command are in
[`loop-before.txt`](evidence/loop-before.txt).

After one second the processing counts were `{"s1": 305, "s2": 307}`. After another five
seconds they were `{"s1": 1557, "s2": 1560}`. Both increased: the original tunnel has no
counter that can end this loop. I restored the exit-3 entry before stopping the network.

The IPv4 action subtracts one from TTL, but this program **does not check TTL before
forwarding**. A plain IPv4 loop is therefore not protected either: it forwards TTL 0,
then the 8-bit subtraction wraps 0 to 255 on the next visit. Decrementing alone is
insufficient. Also, tunnel forwarding does not call `ipv4_forward`, so it does not
decrement the inner IPv4 TTL at all. This assignment adds protection to the tunnel;
the original plain-IPv4 TTL limitation is unchanged.

## 2. Design: the hop limit

I appended an **8-bit unsigned `hop_limit`** after the existing 16-bit `proto_id`
and 16-bit `dst_id`. The custom header grows from four to five bytes. Eight bits
give values 0–255 with only one additional byte. Appending keeps the existing two
fields at their old offsets, but it does not make the whole packet backward compatible:
the old parser still starts the inner IPv4 header at the wrong byte.

The sender defaults to **8** and accepts `--hops 0..255`. Eight gives room beyond
the lab's two-switch path while keeping an accidental loop short. It is a lab
choice, not a value required by P4 or a standard.

At the beginning of tunnel forwarding, ingress drops a packet if the field is 0
or 1. Otherwise it subtracts one and applies the tunnel table. The check is before
subtraction to avoid unsigned wraparound. With this convention, 8 is the maximum
number of switch visits including the final dropping visit; a two-switch delivery
needs an initial value of at least 3. A normal packet starting at 8 arrives at 6.

The header declaration, parser extraction, ingress check, deparser and both host
packet definitions agree on the five-byte format. The existing parser extracts the
extended header automatically and the deparser emits its extended form.

## 3. Implementation

- `tunnel.p4`: adds the field and checks it on the tunnel path before the lookup.
  A rejected packet never reaches a forwarding action that could undo the drop.
- `send.py`: adds `--hops`, default 8, validates the 8-bit range, and writes the field.
- `receive.py`: reads the same field and prints the remaining value.
- `tunnel_v1.p4`: keeps the finished lab program for the required old/new experiment.
- `assignment/legacy/`: keeps the matching original host scripts for baseline runs.
- `assignment/run_checks.py`: repeats the experiments on fresh instances of the
  supplied topology. It reuses the supplied compiler, switch class and table loader.
  It measures actual table handles and log counts, saves raw output, and checks the
  boundary cases as well as normal forwarding.

No change to the plain IPv4 forwarding table or its actions is required. The host
definitions use the same field order as P4. This consistency matters because a
successful compile alone cannot prove that every device agrees about packet bytes.

## 4. Evidence: it works

The following are copied directly from this run's receiver logs in
[`delivery.txt`](evidence/delivery.txt):

```text
Listening on h2-eth0...
Received (tunnelled, dst_id=2, hop_limit=6) from 10.41.0.1 to 10.41.0.3, TTL 64: 'delivery-h2'
Listening on h3-eth0...
Received (tunnelled, dst_id=3, hop_limit=6) from 10.41.0.1 to 10.41.0.3, TTL 64: 'delivery-h3'
Received (tunnelled, dst_id=3, hop_limit=1) from 10.41.0.1 to 10.41.0.3, TTL 64: 'minimum-delivery'
```

Both normal tunnel deliveries start at 8 and arrive with hop limit 6 after s1 and
s2. The inner IPv4 TTL remains 64 because the tunnel action does not route by IPv4.
Values 0 and 1 drop at s1; 2 drops at s2; 3 reaches h3 with 1 remaining. The actual
sender commands and receiver outputs for these boundary cases are in the same file.

The plain IPv4 check also succeeds:

```text
PING 10.41.0.3 (10.41.0.3) 56(84) bytes of data.
64 bytes from 10.41.0.3: icmp_seq=1 ttl=62 time=29.2 ms
64 bytes from 10.41.0.3: icmp_seq=2 ttl=62 time=7.59 ms

--- 10.41.0.3 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1001ms
rtt min/avg/max/mdev = 7.591/18.417/29.243/10.826 ms
```

With both switches upgraded, the loop's per-switch count increase is
`{"s1": 4, "s2": 4}`, eight visits in total. The two count samples five seconds apart
are identical. The eighth visit drops the packet at hop limit 1, as designed. See
[`loop-after.txt`](evidence/loop-after.txt) for the counts and drop trace.

## 5. Evaluation

### Mixed rollout: prediction and observations

The following prediction is saved in the repository and written to the evidence
before the mixed network starts:

> Prediction recorded before this mixed experiment: the new hop_limit is appended after proto_id and dst_id. The old s2 still reads dst_id correctly and should forward by the tunnel table, but its IPv4 parser starts one byte too early. Its checksum rewrite can therefore damage the real IPv4 header. A frame may reach h2 without the UDP receiver accepting it. In the loop, only s1 decrements the new field. With an initial value of 8 and a drop at <=1, I expect 8 visits to s1 and 7 to s2 (15 total), rather than 8 total with two upgraded switches.

The experiment runs s1 with `tunnel.p4` and s2 with `tunnel_v1.p4`.
The UDP receiver did not record the mixed packet. The following capture shows what actually reached h2:

```text
tcpdump: verbose output suppressed, use -v[v]... for full protocol decode
listening on h2-eth0, link-type EN10MB (Ethernet), snapshot length 262144 bytes
15:39:03.888155 00:00:00:00:00:01 > ff:ff:ff:ff:ff:ff, ethertype Unknown (0x1212), length 52: 
	0x0000:  ffff ffff ffff 0000 0000 0001 1212 0800  ................
	0x0010:  0002 0745 0000 2100 0100 0040 0d66 760a  ...E..!....@.fv.
	0x0020:  2900 010a 2900 0310 e110 e100 0d7f ed6d  )...)..........m
	0x0030:  6978 6564                                ixed
1 packet captured
1 packet received by filter
0 packets dropped by kernel
```

In the new header the bytes after Ethernet are `08 00 00 02 07` after s1:
`proto_id=0x0800`, `dst_id=2`, then remaining hop limit 7. s2's old parser consumes
only the first four bytes as MyTunnel, so its IPv4 parser starts at `07` instead of
the real IPv4 start byte `45`. It reads a false version/IHL and shifts all other
IPv4 fields. Its checksum rewrite targets those incorrect positions. In this run,
the captured real IPv4 protocol byte is `0x0d`, instead of UDP's `0x11`. This explains
why the UDP receiver does not print the packet even though the frame reaches h2.
The rewrite overlaps the real protocol byte and the high checksum byte. The raw capture,
receiver output and s2's parser trace distinguish a frame reaching h2 from successful
UDP delivery. Preserving the old tunnel lookup fields alone is therefore insufficient.

In the mixed loop, the measured increases are `{"s1": 8, "s2": 7}`, fifteen visits in
total. This matches the prediction: s1 decrements on its visits and eventually drops;
s2 does not enforce the new limit. The samples stop growing. This protection depends
on the loop continuing to cross an upgraded switch; a loop containing only old
switches would still be unbounded. See [`mixed.txt`](evidence/mixed.txt).

### Safer rollout options

One option is a **new EtherType** for the new format. Upgraded switches can parse
both formats. Old switches then reject the unknown new type clearly instead of
silently reading shifted fields. This is safer than ambiguous decoding, but traffic
must not enter the new format until every possible switch on its path supports it;
otherwise the fail-closed behaviour interrupts delivery.

A second option is a **staged dual-format rollout**. First install a parser and
deparser that accept both versions while senders continue to emit the old format.
Then verify every relevant path, including backup paths, and enable the new format
at senders. Remove old-format support last. A version or header-length indicator
helps future upgrades, but an old parser must already understand that indicator;
adding a version field today cannot retroactively teach the existing old program.
This needs more transition logic, but can keep traffic working during deployment.

### Data plane versus control plane

Week 3's `check_paths()` follows configured routes before deployment. It can reject
a planned loop or missing route without spending bandwidth, but its result is only
as current and complete as its model. It cannot stop a packet already circulating
after a manual table change or a partial update that it did not model.

The hop limit is a local data-plane safeguard on actual packets. It bounds a
packet's lifetime even when forwarding rules are wrong, but it does not repair the
route, prove reachability, or explain the underlying mistake. A low initial value
can also discard a legitimate long path. Networks benefit from both: validate the
intended paths and limit damage when the running network differs from that intent.

### Hardware cost

The change adds one byte per tunnelled packet, eight bits of header storage, a
comparison, and an 8-bit subtraction. These operations have fixed cost per visit;
the switch does not execute an unbounded software loop. They may consume an action
stage or resources in an existing stage, depending on the target and dependencies.
The extra byte also reduces the available MTU by one byte for the same underlay MTU.
BMv2 compilation and tests demonstrate the software behaviour. They do not prove
that the program fits a particular hardware switch or reaches hardware line rate;
that requires compiling and checking resource use for that target.

## AI use

I used ChatGPT for explanations of the lab logs and for help writing the hop-limit
code, the repeatable experiment script, and this report. The script generates the
result sections from the real outputs collected in my Codespace with my addresses;
it does not generate successful measurements in place of running the network.
The program, experiment commands and evidence are included so the work can be
reviewed and repeated. No AI-generated outputs are presented as measured results.

## Evidence files

| File | What it shows |
|---|---|
| `evidence/lab-finished.txt` | Finished tunnel lab: exit-2 delivery and plain IPv4 |
| `evidence/lab-broken-deparser.txt` | Deliberate missing-header failure and parser traces |
| `evidence/loop-before.txt` | Original loop with counts that keep growing |
| `evidence/delivery.txt` | h2/h3 delivery, remaining hop limits, boundaries and IPv4 ping |
| `evidence/loop-after.txt` | Same loop with eight visits and stable counts |
| `evidence/mixed.txt` | Prediction, s1-new/s2-old experiment and fifteen-visit loop |
| `evidence/mixed-frame.txt` | Raw packet captured at h2 during the mixed run |

The matching JSON files contain measured summaries used to assemble this report.

