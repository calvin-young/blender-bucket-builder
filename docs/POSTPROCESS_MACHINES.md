# Post-processing machines: published parameters

Researched 2026-11-18 for sprue racks (#41) and polisher presets
(`docs/SPEC_PACK.md` section 5.3). The owner uses AMT PostPro today and
wants DyeMansion supported too.

**Main finding:** neither company publishes rack, hook or hanger geometry.
Chamber and basket sizes are published; hook spacing, hook type, rack size
and rack count are not, in any datasheet found. So a rack generator has to
take those as numbers the user enters (measured on the machine, or from the
vendor's fixture drawings), with the chamber size as the only preset.

Tags: **[M]** manufacturer-published, **[D]** a distributor hosting the
manufacturer's sheet, **[T]** third party (unconfirmed). "n/p" = not
published.

## AMT PostPro

| Machine | Process | Chamber W x D x H | Rack / hanging | Part or load limit | Cycle | Src |
| --- | --- | --- | --- | --- | --- | --- |
| SF100 | Vapor smoothing | 400 x 600 x 400 mm, 96 L [M] | n/p; parts hang in the chamber [T], front loading [M] | n/p; 5 kg per part in one vendor guide [T] | n/p | 1, 5 |
| SF50 PP | Vapor smoothing (PP) | 400 x 300 x 400 mm, 48 L [M] | n/p | n/p | n/p | 2 |
| SF50 | Vapor smoothing | 400 x 300 x 400 mm, 48 L [D] | n/p | n/p | n/p | 3 |
| SFX (desktop) | Vapor smoothing | 190 x 320 x 190 mm, 11.5 L [D, T] | n/p | n/p | n/p | 3, 6 |
| SF2X | Vapor smoothing | 24.4 L [T], sizes not found | n/p | n/p | n/p | 7 |
| DPX | Depowdering | 10 L basket [T] | loose in basket | n/p | n/p | 8 |
| DP Pro | Depowdering | about 20 L basket [T] | loose in basket | n/p | n/p | 9 |
| DP Max | Depowdering and blasting | not found | not found | "max load 30 kg" on a reseller page, model unclear [T] | n/p | 10 |

The SF100 itself is 830 x 1400 x 1830 mm, 850 kg [M]. AMT announced two new
PostPro systems before Formnext 2025 (source 11, not read).

## DyeMansion

| Machine | Process | Chamber / basket | Rack / hanging | Part limit | Cycle, capacity | Src |
| --- | --- | --- | --- | --- | --- | --- |
| Powerfuse S | Vapor smoothing | chamber 85 L; basket 610 x 340 x 315 mm (L x W x H) [M] | the basket is the fixture; anything inside it n/p | n/p | 45 - 180 min [M]; parts per run n/p | 12 |
| Powerfuse S, automatic loading | Vapor smoothing | loading unit 960 x 3466 x 1300 mm [M] | n/p | n/p | n/p | 12 |
| PowerShot C | Blasting (cleaning) | rotary basket, 26 L effective [M] | parts tumble loose | n/p | 1 - 40 min; up to 1 HP Jet Fusion 4200/5200 build [M] | 14 |
| PowerShot X | Blasting (cleaning, surfacing) | basket 26 L effective [M] | loose | n/p | 5 - 40 min by mode; same capacity [M] | 15 |
| PowerShot Dual Performance | Blasting | n/p | n/p | min 3 x 3 x 3 mm; max 760 x 280 mm in automatic mode [M] | 10 - 20 min per process; up to 1.5 HP 4200/5200 builds [M] | 16 |
| DM60 | Dyeing | chamber 80 L [M] | n/p | max part 390 mm long x 360 mm high [M] | 150 min; up to 1 HP 4200/5200 build [M] | 17 |

The Powerfuse S is 1850 x 1700 x 2070 mm, 1600 kg [M]. An older sheet gives
129 L and 40 - 120 min [T, snippet only]; the 2025 sheet above wins. Not
read: PowerShot S, the DM-Series, Powerfuse S PP. Every DyeMansion and AMT
sheet says the figures are subject to change.

## What a user has to measure

* **Rack and hanger geometry:** hook or clip spacing and type, rack size
  and count, the usable envelope inside the chamber or basket (door
  opening, rails, nozzles).
* **Clearances:** part to part and part to wall for even vapor exposure.
  Nothing is published; ask the vendor's application engineer.
* **Load limits** per rack or basket.
* The best source is the vendor's fixture drawings (CAD) of the supplied
  rack.

So the presets in SPEC_PACK 5.3 hold the chamber or basket size only, and
the rack, hook spacing and clearance are user settings, saved per machine.

## Sources

1. AMT PostPro SF100 spec sheet V3.1: https://surfaceprep.com/wp-content/uploads/2025/09/PostPro-SF100-Spec-Sheet-V3.1-EN_Sp.pdf
2. AMT PostPro SF50 PP spec sheet: https://surfaceprep.com/wp-content/uploads/2025/09/amt21052024-03_postprosf50_pp_spec_sheet_ENG_SP.pdf
3. SurfacePrep, vapor smoothing: https://surfaceprep.com/vapor-smoothing/
4. SurfacePrep, SF100: https://surfaceprep.com/?p=10903
5. Jaws Technologies, SF100 guide (search snippet only): https://www.jawstec.com/?p=56749
6. CDG, SFX: https://cdg.uk.com/product/amt-postpro-sfx-vapour-smoothing-station
7. Additive-X, SF2X: https://www.additive-x.com/shop/mpattachments/file/viewonline/id/1286/product_id/3369
8. 3D Printing Industry, DPX: https://3dprintingindustry.com/news/amt-unveils-its-latest-depowdering-system-technical-specifications-and-pricing-229096/
9. SurfacePrep, DP Pro: https://surfaceprep.com/?p=11488
10. Additive-X, AMT brand page: https://additive-x.com/brand-amt
11. 3DPrint.com, Formnext 2025 launches: https://3dprint.com/322065/amt-launches-two-new-postpro-systems-ahead-of-formnext-2025/
12. DyeMansion Powerfuse S datasheet 09/2025: https://hawkridgesys.com/wp-content/uploads/DyeMansion_Datasheet_Powerfuse-S_EN_092025.pdf
13. Older Powerfuse S datasheet: https://cdn.goengineer.com/dyemansion_datasheet_powerfuse_s.pdf
14. PowerShot C datasheet: https://cdn.goengineer.com/dyemansion_datasheet_powershot_c.pdf
15. PowerShot X datasheet: https://cdn.goengineer.com/dyemansion_datasheet_powershot_x.pdf
16. PowerShot Dual Performance datasheet: https://cdn.goengineer.com/dyemansion_datasheet_dual_performance.pdf
17. DM60 datasheet: https://cdn.goengineer.com/dyemansion_datasheet_dm60.pdf
