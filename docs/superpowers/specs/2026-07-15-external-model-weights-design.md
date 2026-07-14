# External Model Weights Design

## Context

The repository currently tracks three PyTorch weight files with Git LFS. The
team will distribute these files outside Git instead, so cloning and pushing
the repository must not transfer any `*.pt` objects.

## Goals

- Remove every `*.pt` file from all Git history that can be pushed.
- Keep the three existing weight files on this workstation at their current
  runtime paths.
- Prevent future `*.pt` files from being added to Git.
- Give team members one committed document that explains where externally
  distributed weights must be placed and how to verify them.
- Preserve the existing deletion of `img/img.zip` during the history rewrite
  and include that deletion in the final implementation commit.

## Non-goals

- Uploading weights to GitHub Releases, cloud storage, or another artifact
  service.
- Changing model-loading code or the expected runtime paths.
- Defining the team's file-transfer channel.

## Weight Inventory

| Runtime path | Approximate size | SHA-256 |
| --- | ---: | --- |
| `yolo11m.pt` | 38.80 MiB | `d5ffc1a674953a08e11a8d21e022781b1b23a19b730afc309290bd9fb5305b95` |
| `训练后模型/yolo26x.pt` | 113.17 MiB | `9fdd44a31c504547ffb81d2c6d9e6dac3493c8eaa8b0398d3f43bae6c7003e92` |
| `训练后模型/license_plate_best.pt` | 166.94 MiB | `66f6a3c115977fe12b936035821fa8e69117b4c6f14f33f7f3b4b75caa15be02` |

## Selected Approach

Use a full history rewrite. Before rewriting, copy all three weights to a
temporary directory outside the repository and verify their hashes. Preserve
the existing `img/img.zip` deletion separately so `git filter-repo` can run on
a clean worktree.

Rewrite all local references to remove both `*.pt` paths and
`.gitattributes`. The attributes file exists only to configure LFS for model
weights, so it is unnecessary after the weight history is removed. Restore the
three weight files from the verified backup, add `*.pt` to `.gitignore`, and
create `MODEL_WEIGHTS.md` at the repository root.

The history rewrite will change commit IDs. The original GitHub `master`
branch must therefore be updated later with an explicit
`--force-with-lease`; remote pushing remains a manual user action.

## Team Documentation

`MODEL_WEIGHTS.md` will contain:

- A statement that model weights are distributed outside Git.
- The three required relative paths and approximate sizes.
- A directory tree showing the expected placement.
- The known SHA-256 digest for each file.
- PowerShell commands for existence and checksum verification.
- A warning not to force-add ignored `*.pt` files.

No download URL or transfer provider will be invented. Team members will be
directed to obtain the files from the project group.

## Safety And Recovery

- Create a Git bundle of the current references before rewriting history.
- Back up the physical weight files outside the repository and compare hashes
  before and after restoration.
- Preserve the `img/img.zip` deletion while obtaining a clean worktree for the
  rewrite, then stage it as part of the final implementation commit.
- Keep the original remote commit ID as the expected lease value.
- Do not fetch the old remote branch into the cleaned repository.

## Verification

The implementation is complete only when all of the following hold:

1. No reachable Git object path ends in `.pt`.
2. `git lfs ls-files --all` returns no model weights and `git lfs status`
   reports no objects to push.
3. All three local files exist at the documented paths and retain the recorded
   SHA-256 digests.
4. `git check-ignore` confirms all three files are ignored.
5. `.gitattributes` is absent because no LFS patterns remain.
6. `MODEL_WEIGHTS.md` contains the final paths, sizes, hashes, and placement
   instructions.
7. The full test suite passes.
8. GitNexus `detect_changes` reports no unexpected code-flow impact before the
   implementation commit.
9. The final implementation commit records the deletion of `img/img.zip`.
