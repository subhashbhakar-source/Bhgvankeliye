#!/usr/bin/env python3
"""Build the XDF Parts Finder PWA (docs/) from the Drive index files.

Usage:
  XDF_CODE='abcd efgh ijkl mnop' python3 tools/build.py <raw_dir>

<raw_dir> holds copies of the "00 Claude indexes" files from Google Drive:
  SPC_01.md .. SPC_12.md          SPC_index_by_group (NN of 12 ...).md
  YARD_tools_50043150.md          YARD_lists_index.md
  YARD_50043149.md                YARD_50043149_spares_by_page.md
  YARD_50043141.md                YARD_50043141_accessories_spares_tools_OCR.md
  sap/SAP_XREF_OE_full.tsv        from artifact "Al Reef SAP Reference Files", files/xref/ (OE no. -> SAP no.)
  sap/SAP_XREF_other_maker_nos.tsv  same artifact (CMD / WinGD ID / maker no. -> SAP no.)
  pages/p*.txt                    page maps made from "Split - 100 pages/text" SPC files:
                                  "=== <PDF page> | <printed page>" then the OE numbers on that page

The parts data is gzip-compressed and encrypted (AES-256-GCM, key from the
access code via PBKDF2-SHA256) so the published site is unreadable without the
code. Never commit the raw files or the code.
"""
import base64, datetime, gzip, hashlib, json, os, re, secrets, shutil, sys
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent.parent
RAW = Path(sys.argv[1])
OUT = ROOT / 'docs'
ITER = 310000

def unesc(s):
    return re.sub(r'\\([\\`*_{}\[\]()#+\-.!|~<>=&])', r'\1', s)

def lines(p):
    return [unesc(l.rstrip()) for l in p.read_text(encoding='utf-8').splitlines()]

warn = []

# ---------- WinGD Spare Parts Catalogue ----------
chapters, spc = [], []
row_re = re.compile(r'^\s*(\d{3}[A-Z]?)\s*\|\s*([^|]+?)\s*\|(.*)$')
for i in range(1, 13):
    p = RAW / f'SPC_{i:02d}.md'
    cur, asm = None, ''
    for l in lines(p):
        m = re.match(r'^##\s+(\d{4}[A-Z]?)\s+(.*)$', l)
        if m:
            chapters.append([m.group(1), m.group(2).strip(), ''])
            cur, asm = len(chapters) - 1, ''
            continue
        if cur is None:
            continue
        if l.startswith('Location:'):
            chapters[cur][2] = l[len('Location:'):].strip()
            continue
        m = re.match(r'^\s*\[(.*)\]\s*$', l)
        if m:
            asm = m.group(1).strip()
            continue
        m = row_re.match(l)
        if m:
            pos, oe, rest = m.group(1), m.group(2), m.group(3)
            f = [x.strip() for x in rest.split('|')]
            qty = f[0]
            lvl = f[1] if len(f) > 1 and re.fullmatch(r'L\d+', f[1]) else ''
            body = f[2:] if lvl else f[1:]
            kg = body.pop() if body and re.search(r'\bkg$', body[-1]) else ''
            name = body[0] if body else ''
            rem = ' | '.join(body[1:])
            if oe.startswith('OE-') and oe[3:5] != chapters[cur][0][:2]:
                warn.append(f'group/OE mismatch: {chapters[cur][0]} {pos} {oe}')
            if not re.fullmatch(r'OE-\d\d-[A-Z]{3}-[A-Z]{3}-[A-Z]{3}', oe):
                warn.append(f'odd part no.: {chapters[cur][0]} {pos} {oe}')
            spc.append([cur, pos, oe, qty, lvl, name, rem, kg, asm])
        elif re.match(r'^\s*\d{3}\s*\|', l):
            warn.append(f'unparsed SPC row in {p.name}: {l}')

# Rows printed in the catalogue but missing from the Drive index (found by the page check below).
# group, pos, part no., qty, level, name, remark, weight
EXTRA_ROWS = [
    ('3403', '014', 'OE-34-AOJ-VZO-MXO', 'Pieces', 'L2', 'PISTON SKIRT',
     'Quantity number not printed in the catalogue text; check the page', '108 kg'),
]
for g, pos, oe, qty, lvl, name, rem, kg in EXTRA_ROWS:
    if any(r[2] == oe for r in spc):
        continue
    ci = next(i for i, c in enumerate(chapters) if c[0] == g)
    at = max(i for i, r in enumerate(spc) if r[0] == ci and r[1] < pos) + 1
    spc.insert(at, [ci, pos, oe, qty, lvl, name, rem, kg, spc[at - 1][8]])

# ---------- System (catalogue chapter) and exact page of every catalogue row ----------
# Chapters and their first printed page, from the catalogue's table of contents (PDF pages 3-9).
# The catalogue has no chapter after 12: groups 9604-9710 are listed at the end of chapter 12.
SYSTEMS = [[4, 'Framework and bearings', 28], [5, 'Cylinder', 81],
           [6, 'Crankshaft, connection rod and piston', 118], [7, 'Drive supply unit and control elements', 161],
           [8, 'Supply unit, pumps and control valves', 180], [9, 'Scavenge air system', 244],
           [10, 'Cylinder lubrication and balancer', 267], [11, 'Piping', 270], [12, 'Monitoring instruments', 427]]
for c in chapters:
    m1 = re.search(r'PDF pages? (\d+)(?:-(\d+))?', c[2])
    m2 = re.search(r'catalogue pages? (\d+)(?:-(\d+))?', c[2])
    pa, pb = int(m1.group(1)), int(m1.group(2) or m1.group(1))
    ca, cb = int(m2.group(1)), int(m2.group(2) or m2.group(1))
    sysi = max(i for i, s in enumerate(SYSTEMS) if s[2] <= ca)
    c += [sysi, pa, pb, ca, cb]          # chapter: [group, title, location, sys, pdfFrom, pdfTo, catFrom, catTo]

# Page maps (pages/*.txt): "=== <pdf page> | <printed page>" then the OE numbers on that page.
occ, printed = {}, {}
for pm in sorted((RAW / 'pages').glob('p*.txt')):
    pdf = None
    for l in pm.read_text(encoding='utf-8').splitlines():
        m = re.match(r'^===\s*(\d+)\s*\|\s*(\S+)', l)
        if m:
            pdf = int(m.group(1))
            printed[pdf] = int(m.group(2)) if m.group(2).isdigit() else None
        elif l.startswith('OE-') and pdf:
            occ.setdefault(l.strip(), []).append(pdf)
known = {r[2] for r in spc}
stray = sorted(o for o in occ if o not in known)
used, nopage, offrange = {}, 0, 0
for r in spc:
    c = chapters[r[0]]
    cand = [p for p in occ.get(r[2], []) if c[4] <= p <= c[5]]
    k = used.get((r[0], r[2]), 0)
    used[(r[0], r[2])] = k + 1
    if cand:
        p = cand[min(k, len(cand) - 1)]
        cp = printed.get(p)
        if cp is None or not (c[6] <= cp <= c[7]):
            offrange += 1
        r += [p, cp or 0]
    else:
        nopage += 1
        r += [0, 0]
# rows printed without a part number: same page as the rows around them, when those agree
for i, r in enumerate(spc):
    if r[9]:
        continue
    prev = next((x for x in reversed(spc[:i]) if x[0] == r[0] and x[9]), None)
    nxt = next((x for x in spc[i + 1:] if x[0] == r[0] and x[9]), None)
    if prev and (nxt is None or nxt[9] == prev[9]):
        r[9], r[10] = prev[9], prev[10]
        nopage -= 1
    elif nxt and prev is None:
        r[9], r[10] = nxt[9], nxt[10]
        nopage -= 1
# rows of one chapter should run forward through its pages
backwards = sum(1 for a, b in zip(spc, spc[1:]) if a[0] == b[0] and a[9] and b[9] and b[9] < a[9])
print(f'pages: {len(spc) - nopage} of {len(spc)} catalogue rows have an exact page; '
      f'{nopage} without; {offrange} printed page outside chapter range; {backwards} page order reversals; '
      f'{len(stray)} map numbers not in catalogue data')
for o in stray[:20]:
    warn.append(f'page map number not in catalogue data: {o} on PDF {occ[o]}')

# ---------- CMD tools list 50043150 (one row per item) ----------
tools = []
for l in lines(RAW / 'YARD_tools_50043150.md'):
    if l.startswith('##'):
        print('tools file heading:', l)
    if re.match(r'^\d{4}-\d{2}\s*\|', l):
        f = [x.strip() for x in l.split('|')]
        if len(f) < 7:
            warn.append(f'unparsed tools row: {l}')
            continue
        tools.append([f[0], f[1], f[2], ' | '.join(x for x in f[3:-3] if x), f[-3], f[-2], f[-1].lstrip('p')])

DOCNOS = {'50043141', '50043149', '50043150'}
def cmds_in(text):
    seen = []
    for n in re.findall(r'(?<!\d)5\d{7}(?!\d)', text):
        if n not in DOCNOS and n not in seen:
            seen.append(n)
    return seen

lists = [
    {'id': '50043149', 'name': 'CMD 50043149 engine spares',
     'note': 'On these pages the numbers are not paired with the names. Open this page of the PDF to see which number belongs to which item.'},
    {'id': '50043141', 'name': 'CMD 50043141 accessories spares & tools (OCR)',
     'note': 'Text read by OCR from scanned tables, so small slips are possible. Confirm on this PDF page.'},
]
pages = []

# ---------- CMD engine spares 50043149 (summary per PDF page) ----------
cur = None
for l in lines(RAW / 'YARD_50043149.md'):
    m = re.match(r'^##\s+p(\d+)\s*(.*)$', l)
    if m:
        cur = [0, m.group(1), m.group(2).strip(), [], []]
        pages.append(cur)
        continue
    if cur is None or not l.strip():
        continue
    s = l.strip()
    if re.match(r'^CMD material', s, re.I):
        cur[4] += [n for n in cmds_in(s) if n not in cur[4]]
    else:
        cur[3].append(s)
        cur[4] += [n for n in cmds_in(s) if n not in cur[4]]

# ---------- CMD accessories 50043141 (OCR text per PDF page) ----------
BOILER = ('List of Spare parts and Tools for Accessories', 'CMD-WinGD 5X72DF-2.1 |',
          'Shanghai CSSC-MES DIESEL', '上海中船三井柴油机有限公司')
cur = None
for l in lines(RAW / 'YARD_50043141.md'):
    m = re.match(r'^===\s*PDF page (\d+)\s*===', l)
    if m:
        n = int(m.group(1))
        cur = None if n <= 2 else [1, str(n), '', [], []]
        if cur:
            pages.append(cur)
        continue
    if cur is None or not l.strip() or l.strip().startswith(BOILER):
        continue
    s = l.strip()
    if not cur[2] and re.match(r'^(Spare parts|Tools|Spare part)', s, re.I):
        cur[2] = s
    cur[3].append(s)
    cur[4] += [n for n in cmds_in(s) if n not in cur[4]]
for p in pages:
    if not p[2]:
        p[2] = p[3][0] if p[3] else ''

# System for the 50043149 pages: the catalogue chapter of the first WinGD group named in "items:"
grp_sys = {}
for c in chapters:
    grp_sys.setdefault(c[0][:4], c[3])
for p in pages:
    sysi = -1
    if p[0] == 0:
        for l in p[3]:
            if l.startswith('items:'):
                for g in re.findall(r'(?<![\d.])(\d{4})(?=-\d|\s|;|$)', l[6:]):
                    if g in grp_sys:
                        sysi = grp_sys[g]
                        break
    p.append(sysi)

# WinGD groups that the yard lists name as group items (for the "CMD / SAP numbers for group" link)
ygroups = {t[0][:4] for t in tools}
for p in pages:
    if p[0] == 0:
        for l in p[3]:
            if l.startswith('items:'):
                ygroups.update(re.findall(r'(?<![\d.])(\d{4})(?=-\d|\s|;|$)', l[6:]))
ygroups = sorted(ygroups & {c[0][:4] for c in chapters})

# ---------- SAP material numbers (plant 81N3 equipment BOM, cross-referenced 5 Nov 2026) ----------
# sap/SAP_XREF_OE_full.tsv: OE part no. -> SAP material no. + SAP description
# sap/SAP_XREF_other_maker_nos.tsv: CMD yard no., WinGD ID no., maker no. -> SAP material no. + description
# Only the SAP number and SAP description are used (no equipment or stock).
import csv
SAPKINDS = ['OE no. not in the X72DF-2.1 catalogue', 'CMD yard no.', 'WinGD sensor / ID no.',
            'Maker / sub-supplier no.', 'No maker no. in SAP']
oesap, cmdsap, saprecs = {}, {}, []
app_oe = {r[2] for r in spc}
app_cmd = {t[1] for t in tools if t[1]} | {n for p in pages for n in p[4]}

def sap_rows(name):
    with open(RAW / 'sap' / name, encoding='utf-8', newline='') as fh:
        for row in csv.DictReader(fh, delimiter='\t'):
            sap, desc = row['SAP material no.'].strip(), row['SAP description'].strip()
            if not re.fullmatch(r'1200\d{8}', sap):
                warn.append(f'odd SAP no. in {name}: {sap}')
                continue
            yield row, sap, desc

for row, sap, desc in sap_rows('SAP_XREF_OE_full.tsv'):
    oe = row['OE part no.'].strip()
    if oe in app_oe:
        lst = oesap.setdefault(oe, [])
        if [sap, desc] not in lst:
            lst.append([sap, desc])
    else:
        saprecs.append([0, oe, sap, desc])
for row, sap, desc in sap_rows('SAP_XREF_other_maker_nos.tsv'):
    mk, kind = row['Maker part no. (SAP)'].strip(), row['Kind'].strip()
    if kind.startswith('CMD') and mk in app_cmd:
        lst = cmdsap.setdefault(mk, [])
        if [sap, desc] not in lst:
            lst.append([sap, desc])
        continue
    k = {'CMD yard no.': 1, 'WinGD ID no.': 2, 'maker no.': 3}.get(kind, 4)
    saprecs.append([k, mk if k != 4 else '', sap, desc])
print(f'SAP: {len(oesap)} catalogue part nos. have a SAP no. ({sum(1 for r in spc if r[2] in oesap)} of {len(spc)} rows), '
      f'{sum(1 for v in oesap.values() if len(v) > 1)} with several SAP materials; '
      f'{len(cmdsap)} CMD yard nos. linked; {len(saprecs)} other SAP entries '
      f'({", ".join(f"{SAPKINDS[k]}: {sum(1 for s in saprecs if s[0] == k)}" for k in range(5))})')
# Special entries (frequent orders): tools/specials.json, SAP no. looked up by OE no.
specials = json.loads((ROOT / 'tools' / 'specials.json').read_text(encoding='utf-8'))
for s in specials:
    s['sap'] = list(oesap.get(s['oe'], [])) + [[r[2], r[3]] for r in saprecs if r[1] == s['oe']]
    saprecs = [r for r in saprecs if r[1] != s['oe']]          # shown by the special entry instead
    print(f"special: {s['title']} | {s['oe']} | Parker {s['parker']} | SAP {s['sap']}")
    if not s['sap']:
        warn.append(f"special entry {s['oe']} has no SAP no.")
lists[0]['note'] = ('The PDF text does not pair these numbers with the names. The SAP section below '
                    'shows what each number is. Confirm on this PDF page.')

meta = {
    'source': 'WinGD X72DF-2.1 Spare Parts Catalogue (Issue 001, 2022-08); CMD/Jiangnan lists 50043150 (tools), 50043149 (engine spares), 50043141 (accessories). Copied from the "00 Claude indexes" Drive folder.',
    'built': datetime.date.today().isoformat(),
    'counts': {'spc': len(spc), 'tools': len(tools), 'pages': len(pages)},
}
data = {'meta': meta, 'systems': [[s[0], s[1]] for s in SYSTEMS], 'chapters': chapters, 'spc': spc,
        'tools': tools, 'lists': lists, 'pages': pages, 'ygroups': ygroups,
        'oesap': oesap, 'cmdsap': cmdsap, 'saprecs': saprecs, 'sapkinds': SAPKINDS, 'specials': specials}
plain = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()

# ---------- encrypt ----------
code = re.sub(r'[^a-z0-9]', '', os.environ.get('XDF_CODE', '').lower())
if len(code) < 12:
    sys.exit('Set XDF_CODE to the access code (at least 12 letters/digits).')
salt = base64.b64decode((ROOT / 'tools' / 'salt.txt').read_text().strip())
key = hashlib.pbkdf2_hmac('sha256', code.encode(), salt, ITER, 32)
iv = secrets.token_bytes(12)
ct = AESGCM(key).encrypt(iv, gzip.compress(plain, 9, mtime=0), None)
enc = {'v': 1, 'iter': ITER, 'salt': base64.b64encode(salt).decode(),
       'iv': base64.b64encode(iv).decode(), 'ct': base64.b64encode(ct).decode()}

OUT.mkdir(exist_ok=True)
html = (ROOT / 'tools' / 'template.html').read_text(encoding='utf-8').replace('/*DATA*/', json.dumps(enc))
(OUT / 'index.html').write_text(html, encoding='utf-8')
for f in ('manifest.webmanifest', 'icon-180.png', 'icon-192.png', 'icon-512.png'):
    shutil.copy(ROOT / 'app' / f, OUT / f)
ver = hashlib.sha1(html.encode()).hexdigest()[:10]
(OUT / 'sw.js').write_text((ROOT / 'tools' / 'sw.template.js').read_text().replace('__VERSION__', ver))
(OUT / '.nojekyll').write_text('')

if os.environ.get('XDF_ARTIFACT'):
    # Same page for a private claude.ai artifact: the host adds the document skeleton and
    # safe-area padding, and has no service worker, manifest or home-screen icon.
    keep = []
    for l in html.splitlines():
        s = l.strip()
        if s.lower() in ('<!doctype html>', '<html lang="en">', '<head>', '</head>', '<body>', '</body>', '</html>'):
            continue
        if re.match(r'<meta (charset|name="(viewport|theme-color|apple-[^"]*|mobile-web-app-capable)")', s) or \
           re.match(r'<link rel="(manifest|apple-touch-icon)"', s):
            continue
        keep.append(l)
        if s.startswith('<title>'):
            keep.append('<meta name="claude:viewport-fit-sides" content="cover">')
    art = '\n'.join(keep) + '\n'
    art = art.replace('</style>',
        'header{top:env(safe-area-inset-top, 0px);padding-block:10px 8px}\n'
        'body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top, 0px);z-index:10;background:var(--bg)}\n'
        '</style>', 1)
    Path(os.environ['XDF_ARTIFACT']).write_text(art, encoding='utf-8')

if os.environ.get('XDF_DUMP'):
    Path(os.environ['XDF_DUMP']).write_bytes(plain)
print(f'chapters={len(chapters)} spc={len(spc)} tools={len(tools)} pages={len(pages)} '
      f'plain={len(plain)//1024}KB html={len(html)//1024}KB ver={ver}')
for w in warn[:40]:
    print('WARN', w)
print('warnings:', len(warn))
