#!/usr/bin/env python3
"""Run the assignment's real Mininet experiments and save their commands and output.

Run from the repository root: python3 assignment/run_checks.py --id 4941
This uses the same compiler, BMv2 switches, table files and topology as topo.py.
It does not invent evidence: failures stop the run and remain in the transcript.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'assignment' / 'evidence'
PHASES = ['lab-finished', 'lab-broken-deparser', 'loop-before', 'delivery', 'loop-after', 'mixed']
PREDICTION = (
    'Prediction recorded before this mixed experiment: the new hop_limit is appended '
    'after proto_id and dst_id. The old s2 still reads dst_id correctly and should '
    'forward by the tunnel table, but its IPv4 parser starts one byte too early. '
    'Its checksum rewrite can therefore damage the real IPv4 header. A frame may '
    'reach h2 without the UDP receiver accepting it. In the loop, only s1 decrements '
    'the new field. With an initial value of 8 and a drop at <=1, I expect 8 visits '
    'to s1 and 7 to s2 (15 total), rather than 8 total with two upgraded switches.'
)


def command(text):
    print('\n$ ' + text, flush=True)
    result = subprocess.run(shlex.split(text), text=True, capture_output=True)
    output = result.stdout + result.stderr
    print(output, end='' if output.endswith('\n') else '\n', flush=True)
    if result.returncode:
        raise RuntimeError('Command failed: ' + text)
    if re.search(r'\b(?:Error|Invalid)\b', output):
        raise RuntimeError('Switch CLI reported an error: ' + text)
    return output


def host(net, name, text):
    print('\nmininet> ' + name + ' ' + text, flush=True)
    # Mininet's interactive CLI substitutes host names; Node.cmd does not.
    parts = shlex.split(text)
    parts = [net.get(part).IP() if part in ('h1', 'h2', 'h3') else part for part in parts]
    output = net.get(name).cmd(shlex.join(parts))
    print(output, end='' if output.endswith('\n') else '\n', flush=True)
    return output


def read_log(name):
    path = ROOT / 'logs' / (name + '.txt')
    if not path.exists():
        return ''
    with path.open('rb') as stream:
        # Read a fixed snapshot even if the switch is still appending.
        return stream.read(os.fstat(stream.fileno()).st_size).decode(errors='replace')


def counts():
    result = {s: read_log(s).count('Processing packet received') for s in ('s1', 's2')}
    print('Packet-processing counts: ' + json.dumps(result), flush=True)
    return result


def receivers():
    result = {}
    for h in ('h2', 'h3'):
        print('\n== logs/' + h + '.txt ==', flush=True)
        result[h] = read_log(h)
        print(result[h], flush=True)
    return result


def excerpt(switch, limit=65):
    text = read_log(switch)
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if 'key is 1212' in line), 0)
    print('\n== ' + switch + ' log excerpt ==', flush=True)
    print('\n'.join(lines[max(0, start - 2):start + limit]), flush=True)


def loop(net, nn, legacy=False):
    dump = command('./p4cli s2 table_dump MyIngress.myTunnel_exact')
    handle = None
    for entry in dump.split('Dumping entry ')[1:]:
        if re.search(r'myTunnel\.dst_id\s*:\s*EXACT\s+0*3\b', entry):
            handle = int(entry.splitlines()[0].strip(), 0)
            break
    if handle is None:
        raise RuntimeError('Could not identify tunnel exit 3 from the actual table dump')
    baseline = counts()
    command('./p4cli s2 table_modify MyIngress.myTunnel_exact MyIngress.myTunnel_forward %d 1' % handle)
    sender = 'assignment/legacy/send_v1.py' if legacy else 'send.py'
    host(net, 'h1', 'python3 %s 10.%d.0.3 loop --tunnel 3%s' %
         (sender, nn, '' if legacy else ' --hops 8'))
    time.sleep(1)
    first = counts()
    print('Waiting 5 seconds before measuring again...', flush=True)
    time.sleep(5)
    second = counts()
    # Stop even the old unbounded loop before reading detailed logs.
    command('./p4cli s2 table_modify MyIngress.myTunnel_exact MyIngress.myTunnel_forward %d 3' % handle)
    result = {'baseline': baseline, 'first': first, 'second': second,
              'delta': {s: second[s] - baseline[s] for s in baseline}}
    print('Loop measurements: ' + json.dumps(result), flush=True)
    return result


def experiment(net, phase, nn):
    time.sleep(2)  # let the host packet receivers attach before sending
    result = {'phase': phase, 'addresses': '10.%d.0.0/16' % nn}
    if phase == 'lab-finished':
        host(net, 'h1', 'python3 assignment/legacy/send_v1.py 10.%d.0.3 "test 3" --tunnel 2' % nn)
        time.sleep(1)
        result['receivers'] = receivers()
        result['ping'] = host(net, 'h1', 'ping -c 2 -W 1 h3')
        assert "'test 3'" in result['receivers']['h2'], 'Finished lab did not deliver to tunnel exit 2'
        assert 'TTL 64' in result['receivers']['h2'], 'Unexpected original tunnel TTL'
        assert '0% packet loss' in result['ping'] and '100% packet loss' not in result['ping'], 'Plain IPv4 failed'
    elif phase == 'lab-broken-deparser':
        host(net, 'h1', 'python3 assignment/legacy/send_v1.py 10.%d.0.3 "test 4" --tunnel 2' % nn)
        time.sleep(1)
        result['receivers'] = receivers()
        excerpt('s1')
        excerpt('s2')
        assert not any("'test 4'" in text for text in result['receivers'].values()), 'Broken deparser unexpectedly delivered'
    elif phase in ('loop-before', 'loop-after'):
        result['loop'] = loop(net, nn, legacy=(phase == 'loop-before'))
        if phase == 'loop-before':
            assert all(result['loop']['second'][s] > result['loop']['first'][s] for s in ('s1', 's2')), 'Old loop did not keep growing'
        else:
            assert result['loop']['first'] == result['loop']['second'], 'New loop did not stop'
            assert sum(result['loop']['delta'].values()) == 8, 'Hop limit did not match eight visits'
            excerpt('s2')
    elif phase == 'delivery':
        host(net, 'h1', 'python3 send.py 10.%d.0.3 "delivery-h2" --tunnel 2 --hops 8' % nn)
        host(net, 'h1', 'python3 send.py 10.%d.0.3 "delivery-h3" --tunnel 3 --hops 8' % nn)
        host(net, 'h1', 'python3 send.py 10.%d.0.3 "zero-must-drop" --tunnel 3 --hops 0' % nn)
        host(net, 'h1', 'python3 send.py 10.%d.0.3 "one-must-drop" --tunnel 3 --hops 1' % nn)
        host(net, 'h1', 'python3 send.py 10.%d.0.3 "two-must-drop" --tunnel 3 --hops 2' % nn)
        host(net, 'h1', 'python3 send.py 10.%d.0.3 "minimum-delivery" --tunnel 3 --hops 3' % nn)
        time.sleep(1)
        result['receivers'] = receivers()
        result['ping'] = host(net, 'h1', 'ping -c 2 -W 1 h3')
        assert "hop_limit=6)" in result['receivers']['h2'] and "'delivery-h2'" in result['receivers']['h2'], 'h2 delivery/hop limit failed'
        assert "'delivery-h3'" in result['receivers']['h3'] and 'hop_limit=6)' in result['receivers']['h3'], 'h3 delivery/hop limit failed'
        assert "'minimum-delivery'" in result['receivers']['h3'] and 'hop_limit=1)' in result['receivers']['h3'], 'Boundary delivery failed'
        for text in result['receivers'].values():
            assert not any(marker in text for marker in ('zero-must-drop', 'one-must-drop', 'two-must-drop')), 'Exhausted packet was delivered'
        assert '0% packet loss' in result['ping'] and '100% packet loss' not in result['ping'], 'Plain IPv4 failed'
    elif phase == 'mixed':
        print('\n' + PREDICTION, flush=True)
        cap = EVIDENCE / 'mixed-frame.txt'
        with cap.open('w') as stream:
            print('\nmininet> h2 timeout 8 tcpdump -c 1 -XX -ni h2-eth0 ether proto 0x1212', flush=True)
            process = net.get('h2').popen(['timeout', '8', 'tcpdump', '-c', '1', '-XX', '-ni', 'h2-eth0', 'ether', 'proto', '0x1212'], stdout=stream, stderr=subprocess.STDOUT)
            time.sleep(1)
            host(net, 'h1', 'python3 send.py 10.%d.0.3 mixed --tunnel 2 --hops 8' % nn)
            process.wait(timeout=12)
        result['capture'] = cap.read_text()
        print('\n== raw frame reaching h2 ==\n' + result['capture'], flush=True)
        result['receivers'] = receivers()
        excerpt('s2')
        result['loop'] = loop(net, nn)
        assert result['loop']['first'] == result['loop']['second'], 'Mixed loop did not stop'
        assert sum(result['loop']['delta'].values()) == 15, 'Mixed loop visit count differs from prediction'
    return result


def run_phase(phase, student_id):
    sys.path.insert(0, str(ROOT))
    import topo as lab
    from mininet.net import Mininet
    from mininet.log import setLogLevel
    setLogLevel('info')
    nn = int(student_id[-2:])
    os.chdir(ROOT)
    for switch in ('s1', 's2'):
        for suffix in ('.txt', '.out'):
            path = ROOT / 'logs' / (switch + suffix)
            if path.exists():
                path.unlink()
    old = phase in ('lab-finished', 'lab-broken-deparser', 'loop-before')
    program = 'tunnel_v1.p4' if old else 'tunnel.p4'
    if phase == 'lab-broken-deparser':
        program = 'build/tunnel_without_emit.p4'
        Path('build').mkdir(exist_ok=True)
        source = Path('tunnel_v1.p4').read_text()
        needle = '        packet.emit(hdr.myTunnel);'
        assert source.count(needle) == 1
        Path(program).write_text(source.replace(needle, '        // lab experiment: tunnel header omitted'))
    lab.SWITCH_BUILD = {'s1': 'build/tunnel.json', 's2': 'build/tunnel.json'}
    lab.compile_program(program, 'build/tunnel.json')
    if phase == 'mixed':
        lab.compile_program('tunnel_v1.p4', 'build/s2.json')
        lab.SWITCH_BUILD['s2'] = 'build/s2.json'
    net = Mininet(controller=None, autoSetMacs=True)
    hosts = [net.addHost('h%d' % i, ip='10.%d.0.%d/24' % (nn, i)) for i in (1, 2, 3)]
    h1, h2, h3 = hosts
    s1 = net.addSwitch('s1', cls=lab.BMv2Switch)
    s2 = net.addSwitch('s2', cls=lab.BMv2Switch)
    net.addLink(h1, s1)
    net.addLink(s1, s2)
    net.addLink(s2, h2)
    net.addLink(s2, h3)
    net.build()
    result = None
    try:
        for h in hosts:
            h.cmd('sysctl -qw net.ipv6.conf.all.disable_ipv6=1 net.ipv6.conf.default.disable_ipv6=1')
        net.start()
        net.staticArp()
        for switch in ('s1', 's2'):
            lab.load_tables(switch, nn)
        receiver = 'assignment/legacy/receive_v1.py' if old else 'receive.py'
        for h in (h2, h3):
            h.cmd('python3 -u %s > logs/%s.txt 2>&1 &' % (receiver, h.name))
        result = experiment(net, phase, nn)
    finally:
        net.stop()
    (EVIDENCE / (phase + '.json')).write_text(json.dumps(result, indent=2) + '\n')
    print('\nPASS: ' + phase, flush=True)


def make_report(student_id):
    data = {phase: json.loads((EVIDENCE / (phase + '.json')).read_text()) for phase in PHASES}
    old = data['loop-before']['loop']
    new = data['loop-after']['loop']
    mixed = data['mixed']['loop']
    captured = data['mixed']['capture']
    udp_delivered = any("'mixed'" in value for value in data['mixed']['receivers'].values())
    template = (ROOT / 'assignment' / 'REPORT_TEMPLATE.md').read_text()
    replacements = {
        '@@ID@@': student_id,
        '@@OLD_FIRST@@': json.dumps(old['first']),
        '@@OLD_SECOND@@': json.dumps(old['second']),
        '@@NEW_COUNTS@@': json.dumps(new['delta']),
        '@@MIXED_COUNTS@@': json.dumps(mixed['delta']),
        '@@DELIVERY_H2@@': data['delivery']['receivers']['h2'].strip(),
        '@@DELIVERY_H3@@': data['delivery']['receivers']['h3'].strip(),
        '@@PING@@': data['delivery']['ping'].strip(),
        '@@MIXED_DELIVERY@@': ('The UDP receiver recorded the mixed packet.' if udp_delivered else 'The UDP receiver did not record the mixed packet.'),
        '@@CAPTURE@@': captured.strip(),
        '@@PREDICTION@@': PREDICTION,
        '@@TIME@@': datetime.now(timezone.utc).isoformat(),
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    assert not re.search(r'@@[A-Z0-9_]+@@', template), 'Unfilled report fields'
    (ROOT / 'assignment' / 'REPORT.md').write_text(template)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--id', required=True)
    ap.add_argument('--phase', choices=PHASES)
    args = ap.parse_args()
    if not re.fullmatch(r'\d{4}', args.id):
        ap.error('--id requires exactly four digits')
    os.chdir(ROOT)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if args.phase:
        run_phase(args.phase, args.id)
        return
    if os.geteuid() != 0:
        sys.exit('Run in the course Codespace terminal as root, as for topo.py.')
    for phase in PHASES:
        print('\nRunning ' + phase + ' ...', flush=True)
        path = EVIDENCE / (phase + '.txt')
        with path.open('w') as stream:
            stream.write('UTC: ' + datetime.now(timezone.utc).isoformat() + '\n')
            stream.write('Student ID suffix: ' + args.id + '\n')
            for filename in ('tunnel.p4', 'tunnel_v1.p4', 'send.py', 'receive.py'):
                stream.write(filename + ' sha256: ' + hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() + '\n')
            if phase == 'mixed':
                stream.write(PREDICTION + '\n')
            stream.flush()
            try:
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--id', args.id, '--phase', phase], stdout=stream, stderr=subprocess.STDOUT, timeout=100)
            except subprocess.TimeoutExpired:
                subprocess.run(['mn', '-c'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                sys.exit('STOP: ' + phase + ' timed out. Read ' + str(path))
        if result.returncode:
            print(path.read_text()[-10000:], flush=True)
            subprocess.run(['mn', '-c'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            sys.exit('STOP: ' + phase + ' failed; evidence preserved in ' + str(path))
        print('PASS: ' + phase, flush=True)
    make_report(args.id)
    print('\nALL CHECKS PASSED. Real evidence and assignment/REPORT.md are ready.', flush=True)


if __name__ == '__main__':
    main()

