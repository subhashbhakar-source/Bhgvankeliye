# XDF Parts Finder

An iPhone-friendly web app (PWA) for searching the WinGD X72DF-2.1 spare parts on Al Areef:

- **WinGD Spare Parts Catalogue** (Issue 001, 2022-08): 2,910 rows. Each shows the part name as printed, the OE part number, the system (catalogue chapter and group), the exact catalogue page and PDF page, position, quantity and weight.
- **CMD / Jiangnan yard lists**: 50043150 tools (404 rows with CMD material and drawing numbers), 50043149 engine spares (per PDF page) and 50043141 accessories spares and tools (OCR text per PDF page).

- **SAP material numbers** (plant 81N3 equipment BOM, cross-referenced 5 Nov 2026): shown next to the OE or CMD number with the SAP name, and searchable both ways. Parts with several SAP materials carry a warning; parts not on the BOM say so. Other SAP items (maker, sub-supplier and WinGD ID numbers) are searchable too, each with its SAP equipment name as the system. They cover every equipment the SAP BOM export names as main engine or iCER (ME1, ME, M/E, MAIN ENGINE, ICER), including the exhaust gas boiler, iCER system, FO pilot and supply pumps and the AutoChief M/E interface unit.

- **Maker part no. beside SAP material no.**: in the SAP section of every card, and in every machine's parts list, the two numbers sit side by side. The maker part no. is shown exactly as SAP stores it, including the endings SAP adds when one OE number has several materials (for example the same OE number ending in `-251` and in `-237`), and a second maker no. where SAP holds one.

- **Frequent orders** are pinned at the top of the start screen and always listed first in search. They are defined in `tools/specials.json` (title, OE no., Parker or maker no., label code, notes); the SAP no. is looked up from the SAP table by OE no. at build time.

- **Other machinery** (switch, off by default): the rest of the ship from the same SAP BOM export, 355 machines with 15,622 parts-list lines (generators, compressors, cargo and deck machinery and so on). It is searched only while the switch is on. The data sits in its own encrypted file, `docs/other-<hash>.dat`, which the phone stores once and downloads again only when that data changes.
- **Machinery view** (switch, off by default): adds a **Machines** list, each machine's parts list, and on every part the line *Equipment → BOM → Item no. → Material no.*, with the plain-word meaning of the four names at the bottom of the page.

- **SAP equipment and their codes**: a collapsible list at the bottom of the page. For each machine it shows the Technical ID (the machine's code on board, a code of the form `REEF-nnn-nnn-nn`), the SAP equipment no., the BOM no., the functional location and how many parts are in its list. It lists the 74 main engine machines, plus the 355 others while the Other machinery switch is on, and has its own filter box. It belongs to the machinery view option in `tools/config.json`.

With both switches off the app shows the main engine only. It works offline once it is installed. Tap any number to copy it. The bottom of the page says what is stored on the phone and when the page was last updated.

## Privacy

The parts data in `docs/index.html` and `docs/other-*.dat` is compressed and encrypted (AES-256-GCM, key derived from an access code with PBKDF2-SHA256), so the published site shows nothing without the code. The code and the raw data are never committed.

## Remove the optional parts

Both switches are optional parts of the build, set in `tools/config.json`:

```json
{ "other_machinery": true, "machinery_view": true }
```

- To remove the other machinery data: set `"other_machinery": false`, run the build, commit `docs/` and push. The build deletes `docs/other-*.dat`; on the next update each phone hides the switch and deletes its stored copy.
- To remove the machinery view: set `"machinery_view": false` the same way. The machines, parts lists and machinery lines disappear.
- Set them back to `true` and rebuild to restore either one. Nothing else in the app depends on them.

## Put it on the iPhone

1. GitHub Pages must be on: repo **Settings → Pages → Build and deployment → Deploy from a branch**, branch `xdf-parts-pwa` (or `main` after merging), folder **/docs**, Save. On a free plan this needs a public repo; the data stays encrypted either way.
2. On the iPhone open `https://subhashbhakar-source.github.io/Bhgvankeliye/` in **Safari**.
3. Share button → **Add to Home Screen**.
4. Open the new **XDF Parts** icon and type the access code once.

## Update the data

1. Copy the current index files from the Drive folder "00 Claude indexes", and the SAP files from the "Al Reef SAP Reference Files" artifact, into a folder outside the repo, named as listed at the top of `tools/build.py`. A new SAP BOM export replaces `sap/bom_81N3_full_export.tsv` (same 29 columns, tab separated).
2. Run `XDF_CODE='your code' python3 tools/build.py /path/to/that/folder` (needs `pip install cryptography`).
3. Commit `docs/` and push. Phones pick up the new version the next time the app is opened online.
