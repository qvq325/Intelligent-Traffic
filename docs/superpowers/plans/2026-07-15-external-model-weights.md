# External Model Weights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all PyTorch weights from pushable Git history while retaining verified local copies at the runtime paths and documenting manual distribution for the team.

**Architecture:** Back up Git references and the three physical weight files outside the repository, then rewrite every local reference to remove `*.pt` and the now-unused `.gitattributes`. Restore the weights as ignored local files, document their placement and checksums, include the existing `img/img.zip` deletion, and verify both repository history and runtime tests before committing.

**Tech Stack:** Git, git-filter-repo, Git LFS, PowerShell, Markdown, GitNexus, pytest

---

### Task 1: Protect Current State

**Files:**
- Preserve locally: `yolo11m.pt`
- Preserve locally: `训练后模型/yolo26x.pt`
- Preserve locally: `训练后模型/license_plate_best.pt`
- Preserve user change: `img/img.zip`

- [ ] **Step 1: Verify the three source files before copying**

Run:

```powershell
Get-FileHash -Algorithm SHA256 yolo11m.pt, `
  '训练后模型/yolo26x.pt', `
  '训练后模型/license_plate_best.pt'
```

Expected hashes:

```text
D5FFC1A674953A08E11A8D21E022781B1B23A19B730AFC309290BD9FB5305B95
9FDD44A31C504547FFB81D2C6D9E6DAC3493C8EAA8B0398D3F43BAE6C7003E92
66F6A3C115977FE12B936035821FA8E69117B4C6F14F33F7F3B4B75CAA15BE02
```

- [ ] **Step 2: Create independent Git and binary backups**

Run from `E:\GitRepo\VideoTest`:

```powershell
git bundle create E:\GitRepo\VideoTest-before-pt-removal-20260715.bundle --all
New-Item -ItemType Directory E:\GitRepo\VideoTest-model-weights-backup-20260715
Copy-Item yolo11m.pt E:\GitRepo\VideoTest-model-weights-backup-20260715\
Copy-Item '训练后模型/yolo26x.pt' E:\GitRepo\VideoTest-model-weights-backup-20260715\
Copy-Item '训练后模型/license_plate_best.pt' E:\GitRepo\VideoTest-model-weights-backup-20260715\
git bundle verify E:\GitRepo\VideoTest-before-pt-removal-20260715.bundle
```

Expected: the bundle reports a complete history and all three copied files have
the hashes from Step 1.

- [ ] **Step 3: Protect the existing image archive deletion**

Run:

```powershell
git stash push -m 'preserve img archive deletion during weight cleanup' -- img/img.zip
git stash show --name-status 'stash@{0}'
git status --porcelain
```

Expected: the stash shows `D img/img.zip` and the worktree is clean.

### Task 2: Rewrite Git History

**Files:**
- Remove from history: `*.pt`
- Remove from history: `.gitattributes`

- [ ] **Step 1: Remove weight paths and LFS attributes from every local reference**

Run:

```powershell
git filter-repo --path-glob '*.pt' --path .gitattributes --invert-paths --force
```

Expected: history is rewritten successfully and `origin` is removed by
git-filter-repo as a safety measure.

- [ ] **Step 2: Remove the temporary stash reference after restoring the deletion**

Run:

```powershell
git stash pop
git status --short
```

Expected: only `D img/img.zip` remains. If the rewritten stash cannot be
applied, delete `img/img.zip` again and drop the stale stash only after
confirming the deletion is present.

- [ ] **Step 3: Restore the original GitHub remote without fetching old history**

Run:

```powershell
git remote add origin https://github.com/qvq325/Intelligent-Traffic.git
git remote -v
```

Expected: `origin` points to `Intelligent-Traffic.git`; `video-pro` remains as
the secondary remote.

- [ ] **Step 4: Prove the rewritten references contain no model weights**

Run:

```powershell
git rev-list --objects --all | Select-String -Pattern '\.pt$'
git log --all -- .gitattributes
```

Expected: both commands return no matches.

### Task 3: Restore Ignored Local Weights And Document Placement

**Files:**
- Modify: `.gitignore`
- Create: `MODEL_WEIGHTS.md`
- Restore locally but do not track: `yolo11m.pt`
- Restore locally but do not track: `训练后模型/yolo26x.pt`
- Restore locally but do not track: `训练后模型/license_plate_best.pt`
- Delete: `img/img.zip`

- [ ] **Step 1: Ignore all PyTorch weight files**

Add this repository rule to `.gitignore`:

```gitignore
# Model weights are distributed outside Git
*.pt
```

- [ ] **Step 2: Restore the physical files from the verified backup**

Run:

```powershell
Copy-Item E:\GitRepo\VideoTest-model-weights-backup-20260715\yolo11m.pt .\yolo11m.pt
Copy-Item E:\GitRepo\VideoTest-model-weights-backup-20260715\yolo26x.pt '.\训练后模型\yolo26x.pt'
Copy-Item E:\GitRepo\VideoTest-model-weights-backup-20260715\license_plate_best.pt '.\训练后模型\license_plate_best.pt'
```

Expected: the files exist at the runtime paths and `git check-ignore` reports
`.gitignore` for all three.

- [ ] **Step 3: Create the team-facing placement guide**

Create `MODEL_WEIGHTS.md` with the following information:

```markdown
# Model Weights

Model weights are distributed directly within the project group and are not
stored in Git. Place the received files at the exact paths listed below before
starting the application.

| File | Relative path | Size | SHA-256 |
| --- | --- | ---: | --- |
| Legacy vehicle model | `yolo11m.pt` | 38.80 MiB | `d5ffc1a674953a08e11a8d21e022781b1b23a19b730afc309290bd9fb5305b95` |
| Trained vehicle model | `训练后模型/yolo26x.pt` | 113.17 MiB | `9fdd44a31c504547ffb81d2c6d9e6dac3493c8eaa8b0398d3f43bae6c7003e92` |
| Trained plate model | `训练后模型/license_plate_best.pt` | 166.94 MiB | `66f6a3c115977fe12b936035821fa8e69117b4c6f14f33f7f3b4b75caa15be02` |
```

Also include the expected directory tree and PowerShell `Test-Path` and
`Get-FileHash` verification commands. State that `git add -f` must not be used
for these files.

### Task 4: Verify And Commit

**Files:**
- Modify: `.gitignore`
- Create: `MODEL_WEIGHTS.md`
- Delete: `img/img.zip`

- [ ] **Step 1: Rebuild GitNexus and check the pending scope**

Run:

```powershell
node .gitnexus/run.cjs analyze --force --name VideoTest --skip-agents-md
node .gitnexus/run.cjs detect-changes --scope all --repo VideoTest
```

Expected: documentation/configuration changes only, with no affected execution
flows.

- [ ] **Step 2: Run repository and local-file verification**

Run checks that assert:

```text
No reachable path ends in .pt
No .gitattributes path is reachable
git lfs ls-files --all returns no paths
git lfs status reports no objects to push
All three local files match the documented SHA-256 hashes
All three local files are ignored
img/img.zip is staged as deleted
```

- [ ] **Step 3: Run the full test suite**

Run:

```powershell
uv run pytest
```

Expected: all 301 tests pass, with no failures.

- [ ] **Step 4: Stage only the intended implementation files**

Run:

```powershell
git add -- .gitignore MODEL_WEIGHTS.md img/img.zip
git diff --cached --check
node .gitnexus/run.cjs detect-changes --scope staged --repo VideoTest
```

Expected staged paths: `.gitignore`, `MODEL_WEIGHTS.md`, and the deleted
`img/img.zip`; GitNexus reports no affected execution flows.

- [ ] **Step 5: Commit the implementation**

Run:

```powershell
git commit -m 'chore: distribute model weights outside Git'
```

- [ ] **Step 6: Refresh indexes and clean obsolete LFS cache objects**

Run:

```powershell
git lfs prune
git gc --prune=now
node .gitnexus/run.cjs analyze --force --name VideoTest --skip-agents-md
git status --short --branch
```

Expected: Git and LFS checks pass, GitNexus is current, and the worktree is
clean. Remote pushing is intentionally not performed.
