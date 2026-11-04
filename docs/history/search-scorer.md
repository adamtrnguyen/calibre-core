# Search scorer: why Indel and not a rapidfuzz scorer

Moved here from the `domain/search.py` module docstring.

## 2026-08-12: the comparison (commit f6f5461)

When `search.py` moved from stdlib difflib to rapidfuzz, swapping in any rapidfuzz *scorer*
wholesale broke the search. Measured against 1,091 real books over 8 golden queries:

| Change | Golden result sets reproduced |
|---|---|
| best single rapidfuzz scorer, structure replaced | 6/8; `perspektive` fell from 10 hits to 2 |
| structure kept, primitive swapped to `Indel` | 7/8 exactly; the eighth (`geometry of art`) only gained one hit |
| structure kept, Levenshtein | worse: loses a real `vollmar optik` result |
| structure kept, Jaro-Winkler | 4/8 |

`fuzz.token_set_ratio` intersects whole token sets, so a long title dilutes a short query; the
module's own averaging lets one well-matched token carry it. Indel wins because it is the same
longest-common-subsequence family as difflib's ratio.
