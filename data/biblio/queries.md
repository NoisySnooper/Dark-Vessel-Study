# Queries

Snapshot: OpenAlex works, 2026-09-23. Years 2015 to 2026. Types kept: article, conference-paper, review, preprint. Text searched: title + reconstructed abstract (OpenAlex abstract_inverted_index).

A work gets every theme it matches. Matching is case-insensitive except for the abbreviations SAR, AIS and IUU.

| Theme | Query |
|---|---|
| `sar_ship_detection` | SAR term AND (ship|vessel|boat) AND (detect|recogni|classif|segment) |
| `dark_vessels` | (dark vessel/ship/fleet/fishing/boat/target | non-broadcasting | AIS gap/disabling | going dark) AND maritime term (vessel|ship|boat|fishing|AIS|maritime); medical 'dark vessel' excluded |
| `sar_ais_fusion` | (AIS | automatic identification system) AND SAR term AND (fusion|fuse|match|correlat|associat|integrat|combin) |
| `xview3` | xView3 | xView-3 | xView 3 |
| `iuu_remote_sensing` | (IUU | illegal fishing | unreported and unregulated | fishing effort | fishing vessel/boat/fleet | fishing activity) AND (satellite | remote sensing | SAR | VIIRS | Sentinel | imagery | night light) |
| `small_vessel` | (small vessel/boat/ship/fishing | small target + maritime term | artisanal | small-scale fish*) AND detect* AND (SAR | satellite | remote sensing | imagery | radar) |
| `viirs_boats` | (VIIRS | day/night band | night-time light | nighttime light | low light imaging) AND (boat|fishing|vessel|ship) |

## Loose and strict modes

- Loose: used during the scan; bare SAR accepted; no medical or search-and-rescue guards.
- Strict: used for the corpus; adds guards: bare SAR rejected next to search and rescue, specific absorption rate, structure-activity or Special Administrative Region; blood and other non-maritime vessels ignored; dark-vessel phrases need maritime context outside the phrase and exclude dark targets and medical text; AIS abbreviation in SAR and AIS fusion needs a ship-like word; animal satellite telemetry is not remote sensing of fishing; artisanal needs a fishing or boat context; small target needs a maritime term.

The scan stored every loose match. The corpus uses strict matching on the stored text. The exact regular expressions are in `src/darkvessel/biblio/themes.py`.

## Scan prefilter (RE2, applied to the raw abstract JSON and the title)

```
(?i)(?:\b(?:ships?|vessels?|boats?|fishing|fisher(?:y|ies|men|man|s)|maritime|artisanal|ais|iuu)\b|unreported|xview|automatic[\s-]*identification)
```

## Southeast Asia and Vietnam

- Affiliation countries: VN, TH, MY, ID, PH, SG, KH, LA, MM, BN, TL.
- Place names in title or abstract: Vietnam, Tonkin, Mekong, Ca Mau, Gulf of Thailand, South China Sea, Spratly, Paracel, Malacca, Sulu, Celebes, Java Sea, Andaman, Indonesia, Philippines, Malaysia, Thailand, Cambodia, Myanmar, Brunei, Singapore, Timor-Leste, East Sea (Vietnam). Plain "East Sea" is not counted because it also names the Sea of Japan.
- Vietnam flag: place names Vietnam, Tonkin, Ca Mau, East Sea (Vietnam), Mekong Delta, or an author affiliation in VN.
