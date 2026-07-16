# Exception-handling audit — streamlit_app.py

Scope: all 325 broad handlers (`except Exception` / bare) in the live app,
classified by AST analysis + manual review of every flagged site.
Date: 2026-07-13. Line numbers refer to the post-patch file.

## Verdict

The raw count (325) overstates the risk dramatically. The dominant uses are
correct: analytics/telemetry wrappers that must never crash a render (the
analytics.py contract), activity-feed logging, optional-import fallback
chains, and fallbacks that RECOMPUTE exactly rather than serve stale data
(e.g. the session memoizer at L1206 and the sweep interpolator at L1948 both
fall back to a fresh exact solve — that is the right design).

Four sites violated the trust model — an engineering document or simulation
silently omitting or substituting real results — and are FIXED in this patch:

| Site | Was | Now |
|---|---|---|
| Handover doc, cross-team checks (x2 copies) | section silently omitted on failure — reads as 'no findings' | doc carries a [WARNING] line saying checks couldn't run |
| Handover doc, brake-pedal 2000 N verdict | block silently vanished — reads as 'not applicable' | doc says the verdict couldn't be computed, re-run before relying |
| Lap sim, declared-aero fold-in | ran on default Cl·A/Cd·A while the 'use my aero' toggle said on | visible st.warning: lap ran WITHOUT your aero |

## Recommended conventions going forward

1. Telemetry/activity/logging: swallow freely (existing contract — keep).
2. Cosmetic/render garnish (hints, captions, overlays): swallow is fine.
3. Optional-engine fallback chains (whisper, mesh decimators): fine, but the
   final fallback must be visible if ALL engines fail.
4. Anything that puts a NUMBER or VERDICT in front of a user (metric, chart,
   report, gate): never `except: pass`. Either surface (st.warning/st.error),
   write the failure INTO the document, or store an explicit error result
   (the transient tab's `('error', str(e), …)` pattern at L25140 is a good
   model).
5. Ledger `IntegrationLedger.from_dict(...) -> None` fallbacks appear ~7
   times. Individually acceptable (downstream treats None as 'nothing
   declared'), but consider one shared `_load_ledger_or_warn()` helper so a
   corrupt session ledger is reported once instead of silently degrading
   seven features. Not changed in this patch (churn > benefit right now).

## Full classification

protects = what the try body does; on_fire = handler behaviour.

| except line | protects | on fire | risky? | try opens at |
|---|---|---|---|---|
| L69 | misc | silent | — | L67 |
| L124 | misc | silent | — | L121 |
| L214 | misc | other | — | L212 |
| L217 | misc | silent | — | L215 |
| L240 | misc | silent | — | L237 |
| L272 | misc | silent | — | L255 |
| L299 | misc | silent | — | L290 |
| L315 | misc | silent | — | L310 |
| L332 | misc | silent | — | L328 |
| L404 | misc | silent | — | L400 |
| L783 | misc | fallback | — | L778 |
| L796 | misc | fallback | — | L794 |
| L810 | misc | fallback | — | L807 |
| L1122 | misc | silent | — | L1120 |
| L1137 | misc | silent | — | L1135 |
| L1216 | solver/number | fallback | ⚠ reviewed | L1206 |
| L1238 | misc | fallback | — | L1232 |
| L1242 | misc | silent | — | L1239 |
| L1272 | misc | fallback | — | L1270 |
| L1285 | misc | fallback | — | L1282 |
| L1345 | cosmetic | silent | — | L1343 |
| L1371 | cosmetic | fallback | — | L1357 |
| L1676 | misc | fallback | — | L1669 |
| L1693 | solver/number | surfaced | — | L1634 |
| L1730 | telemetry | silent | — | L1717 |
| L1771 | solver/number | fallback | ⚠ reviewed | L1760 |
| L1785 | misc | fallback | — | L1783 |
| L1935 | misc | fallback | — | L1929 |
| L1956 | solver/number | silent | ⚠ reviewed | L1948 |
| L4097 | misc | silent | — | L4095 |
| L4102 | import | silent | — | L4083 |
| L4135 | misc | silent | — | L4133 |
| L4262 | misc | silent | — | L4260 |
| L4264 | misc | silent | — | L4221 |
| L4308 | misc | fallback | — | L4303 |
| L4321 | import | silent | — | L4316 |
| L4383 | solver/number | surfaced | — | L4299 |
| L4405 | misc | fallback | — | L4403 |
| L4429 | misc | silent | — | L4426 |
| L4437 | misc | silent | — | L4435 |
| L4619 | telemetry | silent | — | L4616 |
| L4714 | misc | fallback | — | L4711 |
| L4718 | solver/number | fallback | ⚠ reviewed | L4716 |
| L4736 | misc | silent | — | L4723 |
| L4744 | misc | fallback | — | L4740 |
| L4905 | misc | fallback | — | L4890 |
| L4924 | misc | silent | — | L4922 |
| L4940 | misc | silent | — | L4930 |
| L4969 | persistence | fallback | ⚠ reviewed | L4913 |
| L4982 | misc | silent | — | L4980 |
| L5001 | misc | fallback | — | L4999 |
| L5019 | misc | fallback | — | L5017 |
| L5084 | misc | fallback | — | L5037 |
| L5105 | misc | fallback | — | L5102 |
| L5121 | misc | fallback | — | L5095 |
| L5149 | misc | silent | — | L5147 |
| L5240 | persistence | surfaced | — | L5228 |
| L5338 | misc | fallback | — | L5336 |
| L5346 | misc | silent | — | L5343 |
| L5363 | misc | fallback | — | L5360 |
| L5561 | misc | surfaced | — | L5529 |
| L5629 | misc | silent | — | L5611 |
| L5638 | misc | fallback | — | L5635 |
| L5702 | misc | fallback | — | L5700 |
| L5708 | misc | fallback | — | L5706 |
| L5729 | misc | fallback | — | L5725 |
| L5787 | solver/number | fallback | ⚠ reviewed | L5783 |
| L5819 | solver/number | silent | ⚠ reviewed | L5810 |
| L5848 | persistence | other | — | L5840 |
| L6005 | solver/number | fallback | ⚠ reviewed | L6002 |
| L6013 | misc | fallback | — | L6011 |
| L6018 | misc | fallback | — | L6016 |
| L6042 | solver/number | fallback | ⚠ reviewed | L6040 |
| L6071 | misc | silent | — | L6064 |
| L6148 | misc | silent | — | L6145 |
| L6208 | misc | silent | — | L6205 |
| L6216 | misc | fallback | — | L6214 |
| L6230 | misc | fallback | — | L6228 |
| L6267 | misc | fallback | — | L6263 |
| L6306 | import | fallback | — | L6302 |
| L6326 | misc | fallback | — | L6324 |
| L6332 | misc | fallback | — | L6330 |
| L6421 | misc | silent | — | L6419 |
| L6892 | misc | fallback | — | L6890 |
| L7256 | misc | fallback | — | L7253 |
| L7273 | import | fallback | — | L7268 |
| L7314 | solver/number | silent | ⚠ reviewed | L7300 |
| L7331 | misc | silent | — | L7318 |
| L7356 | misc | silent | — | L7336 |
| L7376 | misc | silent | — | L7372 |
| L7380 | misc | silent | — | L7361 |
| L7655 | misc | silent | — | L7653 |
| L7859 | misc | silent | — | L7857 |
| L8058 | misc | soft-surfaced | — | L8053 |
| L8103 | misc | surfaced | — | L8101 |
| L8131 | solver/number | fallback | ⚠ reviewed | L8127 |
| L8163 | solver/number | silent | ⚠ reviewed | L8154 |
| L8190 | persistence | other | — | L8183 |
| L8233 | misc | silent | — | L8230 |
| L8242 | misc | fallback | — | L8239 |
| L8263 | misc | soft-surfaced | — | L8258 |
| L8276 | misc | silent | — | L8274 |
| L8299 | misc | soft-surfaced | — | L8292 |
| L8468 | misc | soft-surfaced | — | L8464 |
| L8526 | misc | fallback | — | L8524 |
| L8548 | misc | fallback | — | L8543 |
| L8598 | misc | fallback | — | L8594 |
| L8719 | misc | fallback | — | L8716 |
| L8787 | misc | surfaced | — | L8781 |
| L8798 | render | silent | — | L8791 |
| L8911 | solver/number | surfaced | — | L8884 |
| L9022 | misc | silent | — | L9016 |
| L9051 | solver/number | silent | ⚠ reviewed | L9043 |
| L9263 | telemetry | silent | — | L9261 |
| L9288 | solver/number | surfaced | — | L9281 |
| L9291 | solver/number | silent | ⚠ reviewed | L9289 |
| L9298 | solver/number | silent | ⚠ reviewed | L9296 |
| L9715 | solver/number | surfaced | — | L9706 |
| L9784 | solver/number | surfaced | — | L9765 |
| L9853 | misc | silent | — | L9836 |
| L9860 | misc | silent | — | L9858 |
| L9864 | misc | silent | — | L9862 |
| L9879 | solver/number | silent | ⚠ reviewed | L9873 |
| L9975 | misc | silent | — | L9973 |
| L10063 | solver/number | silent | ⚠ reviewed | L10061 |
| L10437 | persistence | surfaced | — | L10424 |
| L10484 | misc | fallback | — | L10481 |
| L10488 | solver/number | fallback | ⚠ reviewed | L10486 |
| L10551 | misc | silent | — | L10503 |
| L10636 | misc | silent | — | L10606 |
| L10797 | misc | fallback | — | L10794 |
| L10950 | misc | fallback | — | L10947 |
| L10955 | solver/number | fallback | ⚠ reviewed | L10953 |
| L11100 | solver/number | surfaced | — | L11041 |
| L11327 | misc | silent | — | L11325 |
| L11359 | misc | silent | — | L11357 |
| L11367 | misc | silent | — | L11364 |
| L11392 | persistence | silent | ⚠ reviewed | L11376 |
| L11412 | import | silent | — | L11400 |
| L11432 | misc | fallback | — | L11430 |
| L11470 | solver/number | surfaced | — | L11292 |
| L11606 | solver/number | surfaced | — | L11490 |
| L11683 | misc | silent | — | L11679 |
| L11704 | persistence | surfaced | — | L11661 |
| L11714 | misc | silent | — | L11712 |
| L11766 | misc | other | — | L11762 |
| L11864 | misc | silent | — | L11862 |
| L11887 | misc | fallback | — | L11885 |
| L11908 | solver/number | silent | ⚠ reviewed | L11903 |
| L11918 | solver/number | surfaced | — | L11845 |
| L12048 | solver/number | fallback | ⚠ reviewed | L12038 |
| L12351 | misc | fallback | — | L12349 |
| L12369 | misc | surfaced | — | L12365 |
| L12424 | misc | other | — | L12420 |
| L12446 | misc | silent | — | L12444 |
| L12529 | solver/number | silent | ⚠ reviewed | L12520 |
| L12669 | solver/number | surfaced | — | L12659 |
| L12780 | misc | silent | — | L12774 |
| L13033 | misc | surfaced | — | L13010 |
| L13151 | solver/number | silent | ⚠ reviewed | L13148 |
| L13153 | import | surfaced | — | L13145 |
| L13518 | solver/number | silent | ⚠ reviewed | L13512 |
| L13536 | solver/number | surfaced | — | L12449 |
| L13552 | misc | silent | — | L13550 |
| L13568 | misc | fallback | — | L13566 |
| L13625 | solver/number | silent | ⚠ reviewed | L13612 |
| L13635 | misc | silent | — | L13633 |
| L13784 | misc | fallback | — | L13780 |
| L13836 | misc | surfaced | — | L13822 |
| L13880 | misc | fallback | — | L13878 |
| L13998 | solver/number | silent | ⚠ reviewed | L13990 |
| L14120 | solver/number | surfaced | — | L13924 |
| L14231 | solver/number | surfaced | — | L14162 |
| L14326 | solver/number | silent | ⚠ reviewed | L14313 |
| L14346 | solver/number | surfaced | — | L14273 |
| L14397 | misc | surfaced | — | L14386 |
| L14509 | solver/number | surfaced | — | L14471 |
| L14546 | solver/number | surfaced | — | L14522 |
| L14625 | misc | silent | — | L14616 |
| L14640 | misc | surfaced | — | L14607 |
| L14642 | solver/number | surfaced | — | L14559 |
| L14645 | solver/number | surfaced | — | L13554 |
| L14661 | misc | silent | — | L14659 |
| L14755 | solver/number | silent | ⚠ reviewed | L14749 |
| L14778 | misc | silent | — | L14762 |
| L14894 | solver/number | surfaced | — | L14879 |
| L14911 | misc | fallback | — | L14907 |
| L14930 | solver/number | surfaced | — | L14664 |
| L14945 | misc | silent | — | L14943 |
| L14952 | misc | fallback | — | L14949 |
| L15192 | telemetry | silent | — | L15190 |
| L15206 | misc | silent | — | L15204 |
| L15209 | solver/number | surfaced | — | L15194 |
| L15213 | misc | silent | — | L15211 |
| L15333 | misc | fallback | — | L15331 |
| L15345 | misc | silent | — | L15336 |
| L15387 | solver/number | surfaced | — | L15375 |
| L15506 | solver/number | surfaced | — | L15478 |
| L15523 | solver/number | other | — | L15516 |
| L15631 | solver/number | surfaced | — | L15622 |
| L15653 | solver/number | silent | ⚠ reviewed | L15646 |
| L15706 | import | surfaced | — | L15703 |
| L15728 | misc | silent | — | L15723 |
| L15796 | cosmetic | surfaced | — | L15778 |
| L15817 | misc | surfaced | — | L15811 |
| L15839 | misc | surfaced | — | L15835 |
| L15972 | render | other | — | L15953 |
| L16118 | import | other | — | L16107 |
| L16144 | import | fallback | — | L16142 |
| L16211 | import | silent | — | L16202 |
| L16242 | import | fallback | — | L16240 |
| L16369 | misc | silent | — | L16367 |
| L16447 | solver/number | surfaced | — | L16382 |
| L16578 | misc | surfaced | — | L16567 |
| L16629 | import | fallback | — | L16627 |
| L16693 | solver/number | silent | ⚠ reviewed | L16672 |
| L16711 | telemetry | surfaced | — | L14947 |
| L16893 | misc | silent | — | L16890 |
| L16898 | solver/number | surfaced | — | L16726 |
| L17028 | solver/number | surfaced | — | L16957 |
| L17033 | misc | silent | — | L17031 |
| L17146 | misc | silent | — | L17138 |
| L17156 | import | silent | — | L17149 |
| L17165 | misc | silent | — | L17161 |
| L17197 | import | silent | — | L17180 |
| L17213 | misc | silent | — | L17211 |
| L17270 | telemetry | surfaced | — | L17239 |
| L17290 | misc | fallback | — | L17288 |
| L17355 | solver/number | soft-surfaced | — | L17305 |
| L17556 | misc | silent | — | L17554 |
| L17708 | solver/number | surfaced | — | L17608 |
| L17713 | misc | silent | — | L17711 |
| L19112 | misc | other | — | L19110 |
| L19898 | misc | silent | — | L19896 |
| L19939 | misc | silent | — | L19935 |
| L19952 | solver/number | surfaced | — | L19942 |
| L19996 | solver/number | silent | ⚠ reviewed | L19990 |
| L20068 | misc | silent | — | L20064 |
| L20179 | persistence | surfaced | — | L20164 |
| L20205 | solver/number | surfaced | — | L20183 |
| L20240 | solver/number | silent | ⚠ reviewed | L20234 |
| L20310 | solver/number | silent | ⚠ reviewed | L20301 |
| L20325 | solver/number | other | — | L20319 |
| L20366 | misc | silent | — | L20364 |
| L20398 | telemetry | silent | — | L20396 |
| L20412 | misc | surfaced | — | L20407 |
| L20550 | misc | silent | — | L20548 |
| L20831 | misc | surfaced | — | L20820 |
| L21014 | solver/number | surfaced | — | L20885 |
| L21020 | misc | silent | — | L21018 |
| L21230 | misc | fallback | — | L21228 |
| L21237 | misc | fallback | — | L21234 |
| L21265 | persistence | other | — | L21258 |
| L21329 | misc | soft-surfaced | — | L21326 |
| L21358 | import | fallback | — | L21344 |
| L21498 | render | soft-surfaced | — | L21485 |
| L21606 | misc | silent | — | L21604 |
| L21694 | solver/number | other | — | L21678 |
| L21790 | misc | fallback | — | L21785 |
| L21823 | misc | surfaced | — | L21818 |
| L21898 | solver/number | soft-surfaced | — | L21875 |
| L22015 | solver/number | soft-surfaced | — | L21915 |
| L22058 | cosmetic | soft-surfaced | — | L22042 |
| L22067 | misc | silent | — | L22065 |
| L22191 | solver/number | silent | ⚠ reviewed | L22189 |
| L22206 | misc | fallback | — | L22204 |
| L22267 | misc | surfaced | — | L22248 |
| L22274 | telemetry | silent | — | L22272 |
| L22309 | solver/number | silent | ⚠ reviewed | L22307 |
| L22314 | solver/number | silent | ⚠ reviewed | L22312 |
| L22530 | solver/number | surfaced | — | L22515 |
| L22555 | solver/number | surfaced | — | L22535 |
| L22662 | solver/number | surfaced | — | L22479 |
| L22852 | solver/number | surfaced | — | L22826 |
| L22854 | solver/number | soft-surfaced | — | L22810 |
| L22862 | misc | fallback | — | L22860 |
| L22880 | render | other | — | L22877 |
| L22894 | misc | fallback | — | L22892 |
| L23554 | misc | fallback | — | L23552 |
| L23629 | misc | surfaced | — | L23620 |
| L23667 | misc | surfaced | — | L23655 |
| L23682 | solver/number | surfaced | — | L23672 |
| L23876 | cosmetic | surfaced | — | L23845 |
| L23952 | solver/number | surfaced | — | L23916 |
| L24131 | solver/number | surfaced | — | L23978 |
| L24169 | misc | surfaced | — | L24167 |
| L24339 | misc | fallback | — | L24337 |
| L24498 | telemetry | silent | — | L24495 |
| L24613 | telemetry | silent | — | L24611 |
| L24641 | solver/number | silent | ⚠ reviewed | L24639 |
| L24650 | solver/number | silent | ⚠ reviewed | L24648 |
| L24752 | misc | fallback | — | L24750 |
| L24782 | solver/number | silent | ⚠ reviewed | L24780 |
| L24830 | solver/number | surfaced | — | L24805 |
| L25004 | solver/number | surfaced | — | L24984 |
| L25018 | telemetry | silent | — | L25016 |
| L25044 | solver/number | silent | ⚠ reviewed | L25042 |
| L25046 | solver/number | surfaced | — | L25020 |
| L25050 | solver/number | silent | ⚠ reviewed | L25048 |
| L25068 | solver/number | silent | ⚠ reviewed | L25066 |
| L25152 | solver/number | fallback | ⚠ reviewed | L25140 |
| L25444 | misc | surfaced | — | L25442 |
| L25449 | misc | surfaced | — | L25447 |
| L25454 | misc | surfaced | — | L25452 |
| L25625 | persistence | surfaced | — | L25595 |
| L25632 | misc | silent | — | L25630 |
| L25680 | solver/number | surfaced | — | L25670 |
| L25695 | misc | silent | — | L25693 |
| L25785 | solver/number | other | — | L25768 |
| L25909 | solver/number | surfaced | — | L25865 |
| L25927 | misc | silent | — | L25925 |
| L25973 | telemetry | silent | — | L25971 |
| L25990 | misc | silent | — | L25988 |
| L25993 | misc | surfaced | — | L25975 |
| L25997 | misc | silent | — | L25995 |
| L26223 | misc | other | — | L26213 |
| L26229 | telemetry | surfaced | — | L25929 |
| L26670 | telemetry | surfaced | — | L26282 |
| L26701 | misc | silent | — | L26695 |
| L26724 | misc | fallback | — | L26719 |
| L26745 | misc | fallback | — | L26743 |
| L26759 | misc | silent | — | L26757 |
| L26784 | persistence | silent | ⚠ reviewed | L26769 |
| L26832 | misc | fallback | — | L26828 |
| L26978 | import | surfaced | — | L26955 |
