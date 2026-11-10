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
  sap/bom_81N3_full_export.tsv    same artifact, files/exports/ (the full SAP equipment BOM export:
                                  main engine equipment under other names, MPN column, equipment names)
  pages/p*.txt                    page maps made from "Split - 100 pages/text" SPC files:
                                  "=== <PDF page> | <printed page>" then the OE numbers on that page

The parts data is gzip-compressed and encrypted (AES-256-GCM, key from the
access code via PBKDF2-SHA256) so the published site is unreadable without the
code. Never commit the raw files or the code.

Output (docs/): index.html (main engine data inside), sw.js, icons, manifest and,
when tools/config.json has "other_machinery": true, other-<hash>.dat (the other
machinery data, encrypted with the same key and loaded only by the app's
"Other machinery" switch). XDF_OUT=<dir> writes somewhere else; XDF_CONFIG='{...}'
overrides config.json for one run.
"""
import base64, csv, datetime, gzip, hashlib, hmac, json, os, re, secrets, shutil, sys
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent.parent
RAW = Path(sys.argv[1])
OUT = Path(os.environ.get('XDF_OUT') or ROOT / 'docs')
ITER = 310000

# Optional parts of the app (tools/config.json). Set one to false and rebuild to remove it completely:
#   other_machinery  the rest of the ship from the SAP BOM export, kept in its own file docs/other-*.dat
#   machinery_view   machines, their parts lists and the equipment -> BOM -> item no. -> material no. lines
CFG = {'other_machinery': True, 'machinery_view': True}
CFG.update(json.loads((ROOT / 'tools' / 'config.json').read_text(encoding='utf-8')))
CFG.update(json.loads(os.environ.get('XDF_CONFIG') or '{}'))

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
# ---------- The SAP equipment BOM export ----------
# sap/bom_81N3_full_export.tsv is the full export ("EXPORT 81N3 ALL SPARES WITH NUMBERS.xlsx" as text).
# One line per BOM item: equipment (the machine) -> its BOM (parts list) -> item no. -> material no.
# Main engine scope: the engine's own functional location, plus every equipment SAP names as main engine
# or iCER ("ME1 ...", "ME ...", "M/E ...", "MAIN ENGINE ...", "ICER ..."). All other lines are "other machinery".
ME_NAME = re.compile(r'(^|[^A-Z])(ME[12]?|M/E|ICER)([^A-Z]|$)|MAIN ENGINE')
def me_scope(name, floc):
    return floc.startswith('A-LS-REEF-0600-010') or bool(ME_NAME.search(name.upper()))
clean = lambda s: re.sub(r'[^A-Z0-9]', '', s.upper())
bom = []    # (main engine scope?, equipment no., equipment name, BOM no., item no., SAP no., description, maker no., second maker no.,
            #  technical ID = the machinery code REEF-nnn-nnn-nn, functional location A-LS-REEF-nnnn-nnn)
with open(RAW / 'sap' / 'bom_81N3_full_export.tsv', encoding='utf-8', newline='') as fh:
    for row in csv.DictReader(fh, delimiter='\t'):
        name = ' '.join(row['Object Description'].split())
        sap, item = row['Component/Material No.'].strip(), row['Item Number'].strip()
        if not re.fullmatch(r'\d{12}', sap) or not item.isdigit():
            warn.append(f'odd BOM line skipped: material {sap!r} item {item!r} on {name}')
            continue
        # SAP has two maker part no. columns. The first (HERS) is the one normally filled; the second (MPN)
        # is used when the first is empty, and kept as a "second maker no." when it says something different.
        hers, mpn = row['Manufacturer Part (HERS)'].strip(), row['Manufacturer Part No. (MPN)'].strip()
        bom.append((me_scope(name, row['Functional Location']), row['Equipment'].strip(), name, row['BOM Number'].strip(),
                    int(item), sap, row['Material Description'].strip(), hers or mpn,
                    mpn if hers and mpn and clean(hers) != clean(mpn) else '',
                    row['Technical ID'].strip(), row['Functional Location'].strip()))

# Main engine: the materials the cross-reference tables do not list, the maker no. SAP keeps in its second
# column (MPN) when the first is empty, and the SAP equipment name of every "other SAP" entry (its system).
have = {s[0] for l in oesap.values() for s in l} | {s[0] for l in cmdsap.values() for s in l} | {r[2] for r in saprecs}
bom_eq, bom_mk, bom_mk2, bom_new, bom_scope = {}, {}, {}, {}, set()
for me, eqno, name, bomno, item, sap, desc, mk, mk2, tech, floc in bom:
    if not me:
        continue
    if mk2:
        bom_mk2.setdefault(sap, mk2)
    bom_scope.add(sap)
    eqs = bom_eq.setdefault(sap, [])
    if name not in eqs:
        eqs.append(name)
    if mk:
        bom_mk.setdefault(sap, mk)
    if sap not in have:
        bom_new.setdefault(sap, desc)
mpn_filled = 0
for r in saprecs:
    if r[0] == 4 and bom_mk.get(r[2]):           # SAP has the maker no. in the MPN column
        r[0], r[1] = 3, bom_mk[r[2]]
        mpn_filled += 1
    r.append(bom_eq.get(r[2], []))
for sap, desc in bom_new.items():
    mk = bom_mk.get(sap, '')
    saprecs.append([3 if mk else 4, mk, sap, desc, bom_eq[sap]])
have |= set(bom_new)
print(f'BOM export: {len(bom)} lines; main engine: {len(bom_scope)} SAP nos. on {len({l[1] for l in bom if l[0]})} equipment; '
      f'{len(bom_new)} added; {mpn_filled} maker nos. filled from the MPN column; '
      f'{len(bom_scope - have)} still missing from the app')

def machines(lines, ref):
    """[[equipment no., name, BOM no., [item no., ref, item no., ref, ...], technical ID, functional location]]:
    equipment in file order, items by item no."""
    order, by = [], {}
    for me, eqno, name, bomno, item, sap, desc, mk, mk2, tech, floc in lines:
        if eqno not in by:
            by[eqno] = [eqno, name, bomno, [], tech, floc]
            order.append(eqno)
        elif by[eqno][1] != name or by[eqno][2] != bomno or by[eqno][4] != tech or by[eqno][5] != floc:
            warn.append(f'equipment {eqno} has two names, BOM numbers, technical IDs or functional locations')
        by[eqno][3].append((item, ref(sap)))
    for e in by.values():
        if len({i for i, _ in e[3]}) != len(e[3]):
            warn.append(f'equipment {e[0]} repeats an item no.')
        e[3] = [x for pair in sorted(e[3]) for x in pair]
    return [by[k] for k in order]

# Machinery view (optional): main engine machines with their parts lists.
mach = machines([l for l in bom if l[0]], lambda s: s) if CFG['machinery_view'] else None

# Other machinery (optional): every line outside the main engine scope, as its own data set.
other = None
if CFG['other_machinery']:
    ol = [l for l in bom if not l[0]]
    mats = {}
    for me, eqno, name, bomno, item, sap, desc, mk, mk2, tech, floc in ol:
        m = mats.setdefault(sap, [sap, desc, mk, mk2])
        if m[1] != desc:
            warn.append(f'material {sap} has two descriptions')
        if not m[2] and mk:
            m[2] = mk
        if not m[3] and mk2:
            m[3] = mk2
    matlist = [m if m[3] else m[:3] for m in (mats[k] for k in sorted(mats))]   # [SAP no., description, maker no.(, second maker no.)]
    mi = {m[0]: i for i, m in enumerate(matlist)}
    other = {'eq': machines(ol, lambda s: mi[s]), 'mat': matlist}
    print(f'other machinery: {len(ol)} lines on {len(other["eq"])} equipment, {len(matlist)} materials '
          f'({len(set(mats) & bom_scope)} also on main engine equipment)')
print(f'SAP: {len(oesap)} catalogue part nos. have a SAP no. ({sum(1 for r in spc if r[2] in oesap)} of {len(spc)} rows), '
      f'{sum(1 for v in oesap.values() if len(v) > 1)} with several SAP materials; '
      f'{len(cmdsap)} CMD yard nos. linked; {len(saprecs)} other SAP entries '
      f'({", ".join(f"{SAPKINDS[k]}: {sum(1 for s in saprecs if s[0] == k)}" for k in range(5))})')
# Special entries (frequent orders): tools/specials.json, SAP no. looked up by OE no.
specials = json.loads((ROOT / 'tools' / 'specials.json').read_text(encoding='utf-8'))
for s in specials:
    s['sap'] = list(oesap.get(s['oe'], [])) + [[r[2], r[3]] for r in saprecs if r[1] == s['oe']]
    saprecs = [r for r in saprecs if r[1] != s['oe']]          # shown by the special entry instead
    if s.get('photo'):                                          # kept inside the encrypted data
        s['photo'] = 'data:image/jpeg;base64,' + base64.b64encode((ROOT / 'tools' / s['photo']).read_bytes()).decode()
    print(f"special: {s['title']} | {s['oe']} | Parker {s['parker']} | SAP {s['sap']}")
    if not s['sap']:
        warn.append(f"special entry {s['oe']} has no SAP no.")
# The maker's part no. exactly as SAP stores it, for the pair shown beside every SAP material no.
# sapmk[SAP no.] = [maker no. where SAP writes it differently from the number the app lists the material
# under (e.g. an OE no. with an ending such as -251), second maker no. where SAP holds a different one]
sapmk = {}
def note_mk(sap, listed):
    mk, mk2 = bom_mk.get(sap, ''), bom_mk2.get(sap, '')
    if (mk and mk != listed) or mk2:
        sapmk[sap] = [mk if mk != listed else '', mk2]
for oe, lst in oesap.items():
    for s in lst:
        note_mk(s[0], oe)
for c, lst in cmdsap.items():
    for s in lst:
        note_mk(s[0], c)
for r in saprecs:
    note_mk(r[2], r[1])
for s in specials:
    for x in s['sap']:
        note_mk(x[0], s['oe'])
print(f'maker part nos. as SAP writes them: {sum(1 for v in sapmk.values() if v[0])} differ from the listed number, '
      f'{sum(1 for v in sapmk.values() if v[1])} have a second maker no.')
lists[0]['note'] = ('The PDF text does not pair these numbers with the names. The SAP section below '
                    'shows what each number is. Confirm on this PDF page.')

# ---------- key ----------
code = re.sub(r'[^a-z0-9]', '', os.environ.get('XDF_CODE', '').lower())
if len(code) < 12:
    sys.exit('Set XDF_CODE to the access code (at least 12 letters/digits).')
salt = base64.b64decode((ROOT / 'tools' / 'salt.txt').read_text().strip())
key = hashlib.pbkdf2_hmac('sha256', code.encode(), salt, ITER, 32)

# The other machinery file: 12-byte IV + AES-GCM(gzip(JSON)). The IV comes from the content, so the file
# (and its name) only changes when the data changes and phones do not download it again after other updates.
other_blob = other_name = None
if other:
    gz = gzip.compress(json.dumps(other, ensure_ascii=False, separators=(',', ':')).encode(), 9, mtime=0)
    oiv = hmac.new(key, gz, hashlib.sha256).digest()[:12]
    other_blob = oiv + AESGCM(key).encrypt(oiv, gz, None)
    other_name = f'other-{hashlib.sha256(other_blob).hexdigest()[:12]}.dat'

meta = {
    'source': 'WinGD X72DF-2.1 Spare Parts Catalogue (Issue 001, 2022-08); CMD/Jiangnan lists 50043150 (tools), 50043149 (engine spares), 50043141 (accessories). Copied from the "00 Claude indexes" Drive folder.',
    'built': datetime.date.today().isoformat(),
    'builtAt': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'counts': {'spc': len(spc), 'tools': len(tools), 'pages': len(pages)},
}
if other:
    meta['other'] = {'file': other_name, 'bytes': len(other_blob), 'eq': len(other['eq']),
                     'lines': sum(len(e[3]) // 2 for e in other['eq']), 'mats': len(other['mat'])}
data = {'meta': meta, 'systems': [[s[0], s[1]] for s in SYSTEMS], 'chapters': chapters, 'spc': spc,
        'tools': tools, 'lists': lists, 'pages': pages, 'ygroups': ygroups,
        'oesap': oesap, 'cmdsap': cmdsap, 'saprecs': saprecs, 'sapkinds': SAPKINDS, 'specials': specials,
        'sapmk': sapmk}
if mach:
    data['mach'] = mach
plain = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()

# ---------- encrypt ----------
iv = secrets.token_bytes(12)
ct = AESGCM(key).encrypt(iv, gzip.compress(plain, 9, mtime=0), None)
enc = {'v': 1, 'iter': ITER, 'salt': base64.b64encode(salt).decode(),
       'iv': base64.b64encode(iv).decode(), 'ct': base64.b64encode(ct).decode()}

OUT.mkdir(exist_ok=True)
tpl = (ROOT / 'tools' / 'template.html').read_text(encoding='utf-8').replace('/*DATA*/', json.dumps(enc))
assert tpl.count('<!--OTHER-->') == 1
html = tpl.replace('<!--OTHER-->', '')
(OUT / 'index.html').write_text(html, encoding='utf-8')
for old in OUT.glob('other-*.dat'):               # one current file at most; none when the option is off
    if old.name != other_name:
        old.unlink()
if other_blob:
    (OUT / other_name).write_bytes(other_blob)
for f in ('manifest.webmanifest', 'icon-180.png', 'icon-192.png', 'icon-512.png'):
    shutil.copy(ROOT / 'app' / f, OUT / f)
ver = hashlib.sha1(html.encode()).hexdigest()[:10]
(OUT / 'sw.js').write_text((ROOT / 'tools' / 'sw.template.js').read_text().replace('__VERSION__', ver))
(OUT / '.nojekyll').write_text('')

if os.environ.get('XDF_ARTIFACT'):
    # Same page for a private claude.ai artifact: the host adds the document skeleton and
    # safe-area padding, and has no service worker, manifest or home-screen icon. The other
    # machinery data cannot be cached there, so it rides inside the page.
    inline = ('<script id="otherdata" type="text/plain">' + base64.b64encode(other_blob).decode() + '</script>') if other_blob else ''
    keep = []
    for l in tpl.replace('<!--OTHER-->', inline).splitlines():
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
    if other:
        Path(os.environ['XDF_DUMP'] + '.other').write_text(json.dumps(other, ensure_ascii=False), encoding='utf-8')
print(f'chapters={len(chapters)} spc={len(spc)} tools={len(tools)} pages={len(pages)} '
      f'plain={len(plain)//1024}KB html={len(html)//1024}KB ver={ver} built={meta["builtAt"]}')
print('options:', CFG, '| other file:', f'{other_name} {len(other_blob)//1024}KB' if other_blob else 'none',
      '| machines in page:', len(mach) if mach else 'none')
for w in warn[:40]:
    print('WARN', w)
print('warnings:', len(warn))
