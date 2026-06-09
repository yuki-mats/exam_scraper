# 2026-06-10 occ95-97 active category Firestore fix

## Scope

- Target qualification: `mecnet-kokushi`
- Target app read path: Repaso iOS question set picker on iPhone SE
- Target license name: `医師`
- Intent: hide zero-count category docs from the existing app query path and keep only questionSets with readable questions active.

## iPhone SE Run Attempt

- Device: `yuuki の iPhone`
- Model: iPhone SE (3rd generation)
- UDID: `00008110-001C49190C07801E`
- OS: iOS 18.2.1

Commands/checks:

```bash
flutter devices
xcrun devicectl device info details --device D9B01531-720E-5CF5-8D45-EF77EFF58CD3
npm run dev:iphone-se
xcrun devicectl device process launch --device D9B01531-720E-5CF5-8D45-EF77EFF58CD3 --terminate-existing com.example.repaso
```

Result:

- `flutter run` built and installed the iOS app on the physical iPhone SE.
- Xcode debug attach waited after install, so a direct `devicectl` foreground launch was used.
- Local device screen mirroring URL could not be opened in this environment.
- A temporary Flutter integration test was attempted on the same physical device, but the test harness stalled at device attach after install. The temporary test file was removed.

## Root Cause

The Repaso picker reads:

- `folders` where `isDeleted == false` and `isOfficial == true`
- then filters selected licenses by `folder.licenseName` or `folder.qualificationId`
- then reads `questionSets` where `folderId == <folderId>` and `isDeleted == false`

Before this fix, live Firestore had:

- doctor folders active: `23`
- doctor questionSets active: `200`
- positive-count doctor questionSets: `72`
- zero-count active doctor questionSets: `128`

So zero-count questionSets were visible and selectable in the app, but selecting them could not fetch any question docs.

## Fix

Updated:

- `scripts/count_questions/2_update_category_counts.py`
  - sets `isDeleted=true` when `questionCount <= 0`
  - sets `isDeleted=false` when `questionCount > 0`
  - applies this to both folders and questionSets
- `scripts/upload/upload_category_to_firestore.py`
  - applies the same count-based `isDeleted` state when aggregating from source files
  - respects `folder.isDeleted` during Firestore upload instead of forcing folders active

Regenerated:

```bash
.venv/bin/python scripts/count_questions/2_update_category_counts.py \
  output/mecnet-kokushi/category/category.json \
  output/mecnet-kokushi/questions_json/upload_to_firestore \
  --write
```

Backup:

- `output/mecnet-kokushi/category/old/category.json.bak_20260610_012001`

Local category after update:

- active folders: `9`
- active questionSets: `72`
- zero-count active folders: `0`
- zero-count active questionSets: `0`

## Firestore Upload

Dry-run:

```bash
.venv/bin/python scripts/upload/upload_category_to_firestore.py \
  output/mecnet-kokushi/category/category.json \
  --licenseName 医師
```

Upload:

```bash
.venv/bin/python scripts/upload/upload_category_to_firestore.py \
  output/mecnet-kokushi/category/category.json \
  --licenseName 医師 \
  --upload \
  --credentials-json /Users/yuki/.config/exam_scraper/repaso-rbaqy4-service-account.json
```

## Live Firestore Verification

After upload:

- active doctor folders: `9`
- all doctor folders: `23`
- active doctor questionSets: `72`
- positive-count doctor questionSets: `72`
- zero-count active doctor questionSets: `0`
- deleted doctor questionSets: `128`
- active positive doctor questionSets with at least one readable question doc: `72`

Samples:

- `mk_bp_general_01_01`: `questionCount=30`, `isDeleted=false`
- `mk_bp_general_01_02`: `questionCount=0`, `isDeleted=true`
- `mk_bp_specific_01_01`: `questionCount=0`, `isDeleted=true`
- `mk_bp_required`: `questionCount=0`, `isDeleted=true`
- `mk_bp_general_01`: `questionCount=186`, `isDeleted=false`
- `mk_bp_specific_01`: `questionCount=0`, `isDeleted=true`
