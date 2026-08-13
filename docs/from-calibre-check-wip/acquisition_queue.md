# Acquisition queue — standing authorization granted 2026-08-12

> **ARCHIVED — relocated 2026-08-12 from `~/Research/infra/calibre-check-wip/queues/`
> (deleted; was never under version control).**
>
> ⚠ **MOST OF THIS QUEUE IS ALREADY DONE. Do not work the tables below as-is.**
> Re-checked against the live `metadata.db` on relocation. Status:
>
> **Already acquired since this was written (12 of 17 rows):**
> A1 Neufert *Architects' Data* → **id 1140** · A2 Allen & Iano *Fundamentals of Building
> Construction* → **1137** · A3 Ching *Visual Dictionary of Architecture* → **1138** ·
> A6 Pallasmaa *Eyes of the Skin* → **1139** · B1 Dreyfuss/Tilley *Measure of Man and
> Woman* → **1177** · B2 Pheasant & Haslegrave *Bodyspace* → **1199** · B3 Panero &
> Zelnik *Human Dimension* → **1176** · B5 Zakaria & Gupta *Anthropometry, Apparel Sizing
> and Design* → **1184** · B6 Duffy *Handbook of Digital Human Modeling* → **1181** ·
> B7 Bammes *Complete Guide to Anatomy for Artists & Illustrators* → **1175** ·
> B9 Sheldon *Atlas of Men* → **1170** · B10 Sheldon *Varieties of Human Physique* → **1183**
>
> **The entire "Also pending from the problem-structuring list" section is DONE:**
> Simon **1141** · Schön **1142** · Checkland **1144** · Dorst *Frame Innovation* **1145** ·
> Rosenhead & Mingers **1146** · Yearworth **1147**.
>
> **Still genuinely absent (verified by title AND by first-author surname) — 5 books:**
> **A4** Deplazes *Constructing Architecture* · **A5** Ching/Jarzombek/Prakash *A Global
> History of Architecture* · **A7** Alexander *A Pattern Language* · **B4** Diffrient/Tilley
> *Humanscale* · **B8** Fairbanks *Human Proportions for Artists*.
>
> **Still outstanding, Zotero side:** the five anthropometry papers named under
> *Not books* below (Allen/Curless/Popović 2003; Anguelov 2005 SCAPE; Loper 2015 SMPL;
> Pavlakos 2019 SMPL-X; Robinette CAESAR reports) are **absent from `zotero.sqlite`** —
> confirmed by title search 2026-08-12.
>
> The tables below are preserved **as written**, unedited, for their per-row research
> notes (edition targets, alt titles, do-not-re-acquire lists), which are still useful.

Rules: English only, no translations. Stage in `scratchpad/books/`. Never download into the
Calibre library dir. Verify before filing (file / pdfinfo / pdffonts / deep-page spot check).
Do NOT use `curl -C -` on a fresh download. Signed Anna links die ~161 min: mint -> curl now.
Ownership verified absent for every row below before queueing.

## Batch A — architecture (7)  [ownership check: 2 of 9 already owned, ids 1128, 1129]

| # | Author | Title | Target ed. | Notes |
|---|---|---|---|---|
| A1 | Ernst Neufert | Architects' Data | latest English | PRIORITY — also closes anthropometry gap (dimensional/reach data). Verify ed. count; German orig. = Bauentwurfslehre |
| A2 | Edward Allen & Joseph Iano | Fundamentals of Building Construction: Materials and Methods | latest | NOT Allen & Rand *Architectural Detailing* (owned, id 1130) |
| A3 | Francis D. K. Ching | A Visual Dictionary of Architecture | 2nd ed. | vocabulary reference |
| A4 | Andrea Deplazes | Constructing Architecture: Materials, Processes, Structures | latest English | orig. German; English ed. exists (Birkhäuser) |
| A5 | Ching, Jarzombek & Prakash | A Global History of Architecture | latest | 3rd ed.? verify |
| A6 | Juhani Pallasmaa | The Eyes of the Skin: Architecture and the Senses | any | short book; phenomenology |
| A7 | Christopher Alexander et al. | A Pattern Language: Towns, Buildings, Construction | 1977 | only ed. |

Owned already: Ching *Architecture: Form, Space, and Order* (1128, 5th ed. 2023, current);
Ching *Building Construction Illustrated* (1129, 6th ed. 2020 — 7th ed. 2025 exists but is
NOT on Anna; do not re-chase).

## Batch B — anthropometry / human factors (10)  [all 10 confirmed absent]

| # | Author | Title | Target ed. | Notes |
|---|---|---|---|---|
| B1 | Dreyfuss Associates / Alvin R. Tilley | The Measure of Man and Woman: Human Factors in Design | rev. ed. 2002 | 1960 orig. = *The Measure of Man* |
| B2 | Stephen Pheasant & Christine Haslegrave | Bodyspace: Anthropometry, Ergonomics and the Design of Work | 3rd ed. 2006 | |
| B3 | Julius Panero & Martin Zelnik | Human Dimension and Interior Space | 1979 | scan likely; large-format plates matter |
| B4 | Diffrient, Tilley & Bardagjy | Humanscale 1/2/3, 4/5/6, 7/8/9 | 1974-81 | orig. physical selector dials; may be 1 or 3 files, or unobtainable |
| B5 | Zakaria & Gupta (eds.) | Anthropometry, Apparel Sizing and Design | 2nd ed. 2019 | modern tier |
| B6 | Vincent G. Duffy (ed.) | Digital Human Modeling and Applications... | any recent vol. | recurring conf. volume; report year obtained |
| B7 | Gottfried Bammes | The Complete Guide to Anatomy for Artists & Illustrators | Eng. ed. 2017 | owned Bammes = 1109 ANIMAL anatomy only. Alt titles: Die Gestalt des Menschen, Der nackte Mensch, The Artist's Guide to Human Anatomy, Complete Guide to Life Drawing |
| B8 | Avard & Eugene Fairbanks | Human Proportions for Artists | any | |
| B9 | W. H. Sheldon | Atlas of Men | 1954 | historical only, per Adam |
| B10 | W. H. Sheldon | Varieties of Human Physique | 1940 | historical only, per Adam |

Owned adjacent (do NOT re-acquire): Zarins & Kondrats *Anatomy for Sculptors* (50);
Goldfinger *Human Anatomy for Artists* (665) + *Animal Anatomy* (1106); Morpho series
(58-62, 1112-1115); Bridgman (56, 303); Loomis (290-295); Hogarth (411-415, 83);
Richer (403); Hale (666); Peck (52); Vilppu (299, 300).

## Not books — different destinations, NOT queued here

- Datasets (link table only, no acquisition): CAESAR, ANSUR II + Measurer's Handbook,
  NHANES anthropometric reference data, UMTRI HumanShape, DINED (TU Delft),
  Size NorthAmerica, SizeUK, SizeGERMANY.
- Papers (-> Zotero via CrossRef + zotero-plugin write_item): Allen/Curless/Popovic 2003
  "The Space of Human Body Shapes"; Anguelov et al. 2005 SCAPE; Loper et al. 2015 SMPL;
  Pavlakos et al. 2019 SMPL-X; Robinette et al. CAESAR technical reports.

## Also pending from the problem-structuring list (agents running)
Simon *Sciences of the Artificial*; Schon *Reflective Practitioner*; Checkland *Systems
Thinking Systems Practice*; Dorst *Frame Innovation*; Rosenhead & Mingers *Rational
Analysis for a Problematic World Revisited*; Yearworth *Problem Structuring*.
Owned already: Booth et al. *Craft of Research* (ids 2 and 131, 4th + 5th ed.).
