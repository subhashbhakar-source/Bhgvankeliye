#!/usr/bin/env python3
"""Build app/index.html from the Drive index files (markdown) in a raw folder.

Usage: python3 tools/build.py <raw_dir>
Raw files: SPC_01.md .. SPC_12.md, YARD_tools_50043150.md, YARD_50043149.md, YARD_50043141.md
"""
import datetime, hashlib, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = Path(sys.argv[1])

def unesc(s):
    return re.sub(r'\\([\\`*_{}\[\]()#+\-.!|~<>=])', r'\1', s)

def lines(p):
    return [unesc(l.rstrip()) for l in p.read_text(encoding='utf-8').splitlines()]

# ---------- WinGD SPC ----------
chapters, spc, problems = [], [], []
row_re = re.compile(r'^\s*(\d{3}[A-Z]?)\s*\|\s*(OE-[A-Z0-9-]+|\S+)\s*\|(.*)$')
for i in range(1, 13):
    p = RAW / f'SPC_{i:02d}.md'
    cur, asm = None, ''
    for l in lines(p):
        m = re.match(r'^##\s+(\d{4}[A-Z]?)\s+(.*)$', l)
        if m:
            chapters.append([m.group(1), m.group(2).strip(), ''])
            cur, asm = len(chapters) - 1, ''
            continue
        if l.startswith('Location:') and cur is not None:
            chapters[cur][2] = l[len('Location:'):].strip()
            continue
        m = re.match(r'^\s*\[(.*)\]\s*$', l)
        if m and cur is not None:
            asm = m.group(1).strip()
            continue
        m = row_re.match(l)
        if m and cur is not None:
            pos, oe, rest = m.group(1), m.group(2), m.group(3)
            f = [x.strip() for x in rest.split('|')]
            qty = f[0] if f else ''
            lvl = f[1] if len(f) > 1 and re.fullmatch(r'L\d+', f[1]) else ''
            body = f[2:] if lvl else f[1:]
            kg = ''
            if body and re.search(r'\bkg$', body[-1]):
                kg = body.pop()
            name = body[0] if body else ''
            rem = ' | '.join(body[1:])
            spc.append([cur, pos, oe, qty, lvl, name, rem, kg, asm])
        elif re.match(r'^\s*\d{3}\s*\|', l):
            problems.append(('SPC', p.name, l))

# ---------- Yard tools list 50043150 (row per item) ----------
yard = []
TOOLS = 'CMD list 50043150 (tools)'
for l in lines(RAW / 'YARD_tools_50043150.md'):
    if re.match(r'^\d{4}-\d{2}\s*\|', l):
        f = [x.strip() for x in l.split('|')]
        if len(f) < 7:
            problems.append(('TOOLS', '', l)); continue
        gi, cmd, dwg, desc, qty, kg, page = f[0], f[1], f[2], ' | '.join(f[3:-3]), f[-3], f[-2], f[-1]
        yard.append([TOOLS, gi, cmd, dwg, desc, qty, kg, page.lstrip('p')])

# ---------- Yard spares 50043149 (per page, numbers not paired) ----------
SP = 'CMD list 50043149 (engine spares) - numbers on this page are not paired with names; open the page'
def per_page(path, label):
    out, cur = [], None
    for l in lines(path):
        m = re.match(r'^##\s+p(\d+)\s*(.*)$', l)
        if m:
            cur = {'page': m.group(1), 'title': m.group(2).strip(), 'f': {}}
            out.append(cur); continue
        m = re.match(r'^\s*([^:]{2,40}):\s*(.*)$', l)
        if cur is not None and m:
            cur['f'][m.group(1).strip()] = m.group(2).strip()
    recs = []
    for c in out:
        f = c['f']
        cmd = next((v for k, v in f.items() if 'material' in k.lower() or k.lower().startswith('cmd')), '')
        cmds = ', '.join(re.findall(r'\b5\d{7}\b', cmd)) or ', '.join(re.findall(r'\b5\d{7}\b', ' '.join(f.values())))
        items = f.get('items', '')
        names = f.get('names', '')
        extra = '; '.join(f'{k}: {v}' for k, v in f.items() if k not in ('items', 'names') and v and v != cmd)
        desc = c['title'] + (' - ' + names if names else '') + (' | items: ' + items if items else '') + (' | ' + extra if extra else '')
        gi = ' '.join(sorted(set(re.findall(r'\b\d{4}(?:-\d+)?\b', items))))
        recs.append([label, gi, cmds, '', desc, '', '', c['page']])
    return recs

yard += per_page(RAW / 'YARD_50043149.md', SP)
p41 = RAW / 'YARD_50043141.md'
if p41.exists():
    yard += per_page(p41, 'CMD list 50043141 (accessories spares & tools, OCR - verify on page)')

meta = {
    'source': 'WinGD X72DF-2.1 Spare Parts Catalogue (Issue 001 2022-08) and CMD/Jiangnan yard lists 50043149, 50043150, 50043141, from the "00 Claude indexes" Drive folder',
    'built': datetime.date.today().isoformat(),
}
data = {'meta': meta, 'chapters': chapters, 'spc': spc, 'yard': yard}
js = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
html = (ROOT / 'tools' / 'template.html').read_text(encoding='utf-8').replace('/*DATA*/', js)
(ROOT / 'app' / 'index.html').write_text(html, encoding='utf-8')
ver = hashlib.sha1(html.encode()).hexdigest()[:10]
sw = (ROOT / 'tools' / 'sw.template.js').read_text().replace('__VERSION__', ver)
(ROOT / 'app' / 'sw.js').write_text(sw)

print(f'chapters={len(chapters)} spc_rows={len(spc)} yard_rows={len(yard)} html={len(html)//1024} KB ver={ver}')
for pr in problems[:30]:
    print('UNPARSED', pr)
print('unparsed total', len(problems))
