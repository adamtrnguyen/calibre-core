# Design theory list — status (2026-08-12)

> **ARCHIVED — relocated 2026-08-12 from `~/Research/infra/calibre-check-wip/queues/`
> (deleted; was never under version control).**
>
> Re-checked against live `metadata.db` and `zotero.sqlite` on relocation:
>
> - ⚠ **The book section (D1–D3) is DONE.** All three have since been acquired:
>   D1 Cross *Designerly Ways of Knowing* → **id 1200** · D2 Visser *The Cognitive
>   Artifacts of Designing* → **1202** · D3 Gal & Ventura *Introduction to Design Theory*
>   → **1203**. The "Blocked at time of writing" note about Anna quota is spent.
> - ⚠ **`id 1143` DOES NOT EXIST.** The table below says to cite Schön from 1143 (the
>   "1991 Ashgate scan, true pagination"). There is no book 1143 in the library — ids run
>   1141, 1142, then 1144. **Only 1142 exists**, so the citation pointer is broken; if the
>   true-pagination scan matters, it needs re-acquiring.
> - ✅ **The paper section (P1–P3) is STILL OUTSTANDING — this is the live part.**
>   All three are absent from `zotero.sqlite`, verified three ways: by title, by DOI
>   (`BF01405730`, `0142-694x(01)00009`, `destud.2013.01.002`), and by author surname
>   (Rittel, Webber, Dorst, Wiltschnig, Christensen, Ball — zero hits on any).
>   The metadata below is still correct and still needs entering.
> - ✅ **Both ZotLink junk stubs still present and NOT in trash** — `NVPLQN3H`
>   (title "Redirecting") and `KZ7W4C8K`. Both confirmed junk: `extra` = `下载来源: ZotLink`,
>   `publicationTitle` = "Unknown"/"Unknown Journal", **zero creators, zero attachments**,
>   in no collection (My Library root). Note `KZ7W4C8K` carries a real title
>   (the Wijntjes gloss-perception paper) but no other metadata — it is a bare stub, not a
>   filed item, so deleting it loses only the URL recorded below.

## Already owned — filed today, no action
| Item | Calibre id |
|---|---|
| Herbert Simon, *The Sciences of the Artificial* (3rd ed., MIT 1996) | **1141** |
| Donald Schön, *The Reflective Practitioner* (born-digital) | **1142** |
| Donald Schön, *The Reflective Practitioner* (1991 Ashgate scan, true pagination — cite from this one) | **1143** |

## Books to acquire — ownership verified ABSENT
Checked with `authors:"~(cross|visser|gal|ventura)"` and
`title:"~(designerly|design theory|cognitive artifact|wicked)"`. Only false positives
returned: 183 Julia Galef, 945 Kevin Crossley, 348 Armstrong *Graphic Design Theory*
(a different book). None is a match.

| # | Author | Title | Notes |
|---|---|---|---|
| D1 | Nigel Cross | *Designerly Ways of Knowing* | Springer 2006; also a 1982 Design Studies article of the same name — want the BOOK |
| D2 | Willemien Visser | *The Cognitive Artifacts of Designing* | Lawrence Erlbaum 2006. Her work = designing as construction/transformation of representations |
| D3 | Gal & Ventura | *Introduction to Design Theory: Philosophy, Critique, History and Practice* | recent overview textbook; verify publisher + year from the file on download |

Blocked at time of writing: Anna fast-download quota exhausted; both browser profiles
occupied by two running acquisition agents. Retry when one frees.

## Papers -> Zotero (NOT Calibre). Metadata verified via CrossRef this session.
⚠ Zotero was NOT running when this was written (ports 23121 and 23119 both dead), so these
were not written. Open Zotero, then create both as `journalArticle`.

### P1 — Rittel & Webber 1973  (the "wicked problems" source)
- title: `Dilemmas in a general theory of planning`
- creators: Horst W. J. Rittel (author); Melvin M. Webber (author)
- publicationTitle: `Policy Sciences`   volume `4`   issue `2`   pages `155-169`
- date: `1973`   DOI: `10.1007/BF01405730`
- tags: design, problem-structuring, wicked-problems, planning

### P2 — Dorst & Cross 2001  (problem–solution co-evolution)
- title: `Creativity in the design process: co-evolution of problem-solution`
- creators: Kees Dorst (author); Nigel Cross (author)
- publicationTitle: `Design Studies`   volume `22`   issue `5`   pages `425-437`
- date: `2001`   DOI: `10.1016/s0142-694x(01)00009-6`
- tags: design, problem-structuring, creativity, cognitive-science
- ⚠ NOTE: the commonly-cited DOI `10.1016/S0142-694X(00)00009-6` is WRONG (404s).
  Correct segment is `(01)`, not `(00)`. Confirmed by CrossRef bibliographic query.

### P3 — bonus, surfaced by the same query; a direct follow-up to P2
- `Collaborative problem-solution co-evolution in creative design`
- Stefan Wiltschnig, Bo T. Christensen, Linden J. Ball
- *Design Studies* 34(5) 515-542, 2013, DOI `10.1016/j.destud.2013.01.002`

## Also still pending in Zotero from earlier in the session
- Delete two ZotLink junk stubs in My Library root: `NVPLQN3H`, `KZ7W4C8K`
- Two papers lacking PDFs (both OA — use Zotero's "Find Available PDF"): Di Cicco 2021, Wijntjes 2024
- Anthropometry papers not yet added: Allen/Curless/Popovic 2003 "The Space of Human Body
  Shapes"; Anguelov et al. 2005 SCAPE; Loper et al. 2015 SMPL; Pavlakos et al. 2019 SMPL-X;
  Robinette et al. CAESAR technical reports
