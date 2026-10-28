# JP SWSH hit live tape

As of: 2026-10-29 00:34 Asia/Shanghai

Public SNKRDUNK JSON (apparel, size chips, trading-history) and PriceCharting HTML (game price + PSA population table).
GemRate was not used after the first request failed.
The PSA10 regression workbook was not opened for writing. Site pages were not modified.

## Window

Recent solds are SNK lots in the last 30 Asia/Shanghai calendar days.
The trades array is the last 20 lots the API returns. `snk_*_trades_capped_at_20=Y` means every returned lot is inside the window, so the count is a floor.
Chart-print columns count sampled chart days, not every lot.
Raw B is an ask only. Raw B solds were not requested as a separate tape.

## FX

Frankfurter 2026-10-28: 1 JPY = 0.049664 HKD; 1 USD = 7.845100 HKD.

## GemRate

GemRate not used (HTTP 403 https://www.gemrate.com/ HTTP Error 403: Forbidden)

## Identity

- pikachu-v-csr-s8b-222 | confirmed | CSR
- giratina-vstar-ur-s12a-261 | confirmed | UR
- glaceon-vmax-hr-s6a-91 | confirmed | HR: SA
  SNK HR: SA #091. Regular HR sibling is #090, not this row.
- lugia-vstar-s12-123 | differs | UR
  Number #123 is the gold UR (123/098). The HR is #118, not priced here.
- garchomp-v-s9a-84 | differs | CSR
  SNK name is CSR #084/067. Shortlist guessed SR.
- starmie-v-s9a-83 | differs | CSR
  SNK name is CSR #083/067. Shortlist guessed SR.
- umbreon-vmax-csr-s8b-245 | confirmed | CSR
- umbreon-v-csr-s8b-244 | confirmed | CSR
- zacian-v-csr-s8b-250 | confirmed | CSR
- zamazenta-v-csr-s8b-251 | confirmed | CSR
- blaziken-v-s8b-216 | confirmed | CSR
  SNK name is CSR #216, not CHR.
- blaziken-vmax-csr-s8b-217 | confirmed | CSR
- flareon-v-sr-s6a-73 | confirmed | SR: SA
  SNK SR: SA #073. Regular SR sibling is #072, not this row.
- hisuian-zoroark-vstar-s10a-97 | differs | UR
  Number #097 is UR. The HR is #092, not priced here.
- pikachu-ar-s12a-205 | confirmed | AR
- espeon-vmax-sp-189 | confirmed | SA/PROMO
- sobble-kanazawa-sp-146 | confirmed | PROMO
- cramorant-sp-226 | confirmed | PROMO
- kanazawa-pikachu-sp-147 | confirmed | PROMO
  No-mark #147. With-mark sibling is S-P #144, not this row.
- shibuya-pikachu-sp-2 | confirmed | PROMO
- umbreon-25th-s8a-12 | distinct_confirmed | PROMO
  Distinct from S8b Umbreon V #244, S8b Umbreon VMAX #245, and S6a Umbreon VMAX #095. SNK set code is S8a-P 012/025.
- rayquaza-s3a-56 | confirmed | AR
  SNK name is AR #056/076.
- espeon-vmax-hr-s6a | not_found | - | PC search hit was not accepted as the requested S6a/S8b card | skipped; card not identified
  No Espeon VMAX in S6a. S6a VMAX lines are Leafeon, Glaceon, Sylveon, Umbreon only. #094 is Umbreon VMAX HR, not Espeon.
  rejected PC URL: https://www.pricecharting.com/game/pokemon-japanese-eevee-heroes-special/espeon-vmax-4
- flareon-vmax-hr-s6a | not_found | - | PC search hit was not accepted as the requested S6a/S8b card | skipped; card not identified
  No Flareon VMAX in S6a. Flareon V SR: SA #073 is a separate shortlist row.
  rejected PC URL: https://www.pricecharting.com/game/pokemon-japanese-eevee-heroes-special/flareon-vmax-1
- vaporeon-vmax-hr-s6a | not_found | - | PC search hit was not accepted as the requested S6a/S8b card | skipped; card not identified
  No Vaporeon VMAX in S6a.
  rejected PC URL: https://www.pricecharting.com/game/pokemon-japanese-eevee-heroes-special/vaporeon-vmax-2
- jolteon-vmax-hr-s6a | not_found | - | PC search hit was not accepted as the requested S6a/S8b card | skipped; card not identified
  No Jolteon VMAX in S6a.
  rejected PC URL: https://www.pricecharting.com/game/pokemon-japanese-eevee-heroes-special/jolteon-vmax-3
- charizard-vmax-csr-s8b | not_found | - | PC search did not resolve to one card | skipped; card not identified
  No Charizard VMAX CSR on SNK. Closest S8b Charizard is CHR #187, not priced as a substitute.
  PriceCharting search stayed on a multi-result page. No single URL was accepted.

## Gaps

Espeon / Flareon / Vaporeon / Jolteon VMAX do not exist as S6a HRs on SNK.
S6a VMAX cards that do exist are Leafeon (#088/#089), Glaceon (#090/#091), Sylveon (#092/#093), and Umbreon (#094/#095).
A PriceCharting search for Espeon VMAX under Eevee Heroes resolves to Eevee Heroes Special #4 (a different product). Those prices were not copied onto the gap rows.
Charizard VMAX CSR is not an S8b SNK product. S8b Charizard is CHR #187. No substitute prices were filled in.
Lugia VSTAR #123 is UR. The HR is #118. Hisuian Zoroark VSTAR #097 is UR. The HR is #092.
Garchomp V #084 and Starmie V #083 are CSR on SNK. The shortlist guessed SR.

Giratina VSTAR #261 PriceCharting PSA population on this pull is 30,781 PSA 10 out of 33,390 PSA (gem 0.9219). The page title is this card. The October shortlist pilot had pop_psa10 67. The live table was kept.

No SNKRDUNK request returned 403. GemRate returned 403 on the first request, so population is the PriceCharting PSA column only.

Raw asks with no live listing: Sobble S-P #146 has no raw A or B ask (PSA10 ask is present). Starmie V #083, Zacian V #250, Flareon V #073, and Kanazawa Pikachu #147 have a raw A ask and no raw B ask.

