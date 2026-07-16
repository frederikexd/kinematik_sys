# 🧭 Frames & Datums — usage

**Where:** ✅ Checks & Integration → 🧭 Frames & Datums (shared spine — every subteam sees it).
**Logic:** `coordinate_frames.py` (pure Python, self-tested: `python3 coordinate_frames.py`).

## The pain it kills (from a real team Discord)

| Quote | Failure | Feature that fixes it |
|---|---|---|
| "wait, what are we defining as x and y" | No declared convention; numbers exchanged without a frame tag | **Team convention charter** — one frame + one master datum, declared once, logged as a Decision in the Registry |
| "it might be a full redo cause i have a lot of measurements that are plane-specific" | Migration priced as days of retyping, so the debt compounds | **Migration wizard** — convert live hardpoints or any CSV between frames/datums in one pass, with per-point audit + SolidWorks Curve-Through-XYZ export |
| "we won't rly know the CG until the master assembly is final… the chassis changes length, so relativity to the front axle changes too" | Origins float; measurements rot silently | **Floating datums + datum watch** — front axle / rear axle / mid-wheelbase / CG resolve live from vehicle parameters; the tab warns with millimetres of drift since the charter was saved |
| "Idk if judges prefer it… if we talk about them wrong then maybe they will [care]" | Nobody can defend the convention at design judging | **Judge-ready charter export** — one-page markdown: axis triad, computed rotation senses, phrasebook, and a one-line answer for the judge |
| "should i change my model to sae coordinates?" (while the app itself said "SAE x-rear y-right z-up" — which is Z-**up**, i.e. not SAE) | Even tools mislabel frames | Hardpoint editor header corrected to **ISO 4130-style**, with a pointer to this tab; rotation senses are *computed* from the basis, never memorised |

## The five sections

1. **Charter** — pick ISO 8855 / SAE J670 / ISO 4130 / KinematiK internal / SolidWorks-typical, or build a custom frame from direction words (Z is derived, so a left-handed frame is impossible to declare). Saving logs a Decision so next year's cohort inherits *why*.
2. **Datum watch** — live datum positions; drift warnings vs the charter snapshot, with the exact re-anchoring recipe.
3. **Rosetta** — one point or free vector, shown in every convention simultaneously plus plain English ("585 mm left of centreline…"). Paste the *words* into Discord, not bare numbers. Free-vector mode demonstrates the classic +Z tyre-load sign flip between SAE and ISO.
4. **Migration wizard** — source = live hardpoints or pasted/uploaded CSV (`name,x,y,z`, header optional). Output = converted CSV + SolidWorks XYZ-points file (*Insert → Curve → Curve Through XYZ Points*).
5. **Sign-convention lint** — per-defect findings with fixes: below-ground points (Z-down import), mirror-pair asymmetry (Y sign flip), unit sniff (metres-as-mm), envelope-vs-wheelbase (wrong datum).

## Frame math guarantees

- All frames are proper rotations (det = +1): points, forces, moments and angular rates transform with the same matrix; only points shift by the datum.
- Conversion path is always `frame A → world → frame B` — one auditable route, no pairwise table to keep consistent.
- Rotation senses (+roll/+pitch/+yaw meaning) are computed from the basis via the right-hand rule, so custom frames get correct senses automatically. Verified in self-test: SAE +yaw = nose right, ISO +yaw = nose left; SAE +pitch = nose up, ISO +pitch = nose down.
