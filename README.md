# XDF Parts Finder

An iPhone-friendly web app (PWA) for searching the WinGD X72DF-2.1 spare parts on Al Areef:

- **WinGD Spare Parts Catalogue** (Issue 001, 2022-08): 2,910 rows. Each shows the part name as printed, the OE part number, the system (catalogue chapter and group), the exact catalogue page and PDF page, position, quantity and weight.
- **CMD / Jiangnan yard lists**: 50043150 tools (404 rows with CMD material and drawing numbers), 50043149 engine spares (per PDF page) and 50043141 accessories spares and tools (OCR text per PDF page).

It works offline once it is installed. Tap any number to copy it for SAP.

## Privacy

The parts data in `docs/index.html` is compressed and encrypted (AES-256-GCM, key derived from an access code with PBKDF2-SHA256), so the published site shows nothing without the code. The code and the raw data are never committed.

## Put it on the iPhone

1. GitHub Pages must be on: repo **Settings → Pages → Build and deployment → Deploy from a branch**, branch `xdf-parts-pwa` (or `main` after merging), folder **/docs**, Save. On a free plan this needs a public repo; the data stays encrypted either way.
2. On the iPhone open `https://subhashbhakar-source.github.io/Bhgvankeliye/` in **Safari**.
3. Share button → **Add to Home Screen**.
4. Open the new **XDF Parts** icon and type the access code once.

## Update the data

1. Copy the current index files from the Drive folder "00 Claude indexes" into a folder outside the repo, named as listed at the top of `tools/build.py`.
2. Run `XDF_CODE='your code' python3 tools/build.py /path/to/that/folder` (needs `pip install cryptography`).
3. Commit `docs/` and push. Phones pick up the new version the next time the app is opened online.
