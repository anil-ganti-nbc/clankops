# Clank census (Foundation 0 bootstrap)

This report is a **reconstruction artefact**, not live ClankOps history.
Importing it into the ledger must use `source=RECONSTRUCTED`.

Scanned at (UTC): `2026-09-09T23:09:03Z`

## Provenance

- Bootstrap reconstruction, not live ClankOps history.
- Dirty repositories were observed, not cleaned.
- Secret files were detected by name only and never read.
- Facts in this artefact predate ClankOps and must be imported as RECONSTRUCTED.

Scanner: `clankops.census` (read-only). Secret files were detected by name only and never opened.

## Roots

- `C:\Users\anil\Clanks`
- `C:\Users\anil\Desktop`
- `C:\Users\anil\Documents\Default Project`
- `C:\Users\anil\dau-ecosystem`
- `C:\Users\anil\Clank Base`
- `C:\Users\anil\chudbox`

### Location notes

- Requested `C:\Users\anil\Clanks_\Launchers` does **not** exist. Launchers live at `C:\Users\anil\Clanks\_Launchers`.
- No canonical ClankOps repository existed before this Foundation 0 tree (`C:\Users\anil\Clanks\clankops`).
- `clank-ledger` on GitHub is a **different** product (editorial HIT/MISS evidence). It is not ClankOps.
- Secondary checkouts under `Documents\Default Project` and `Desktop\Watch clank` are classified SUPPORT_COMPONENT when a Clanks-root primary exists.
- `motherclank` and `diagnostic-clank` currently exist locally only under Documents; they are not duplicates.

## Counts

| Metric | Count |
| --- | ---: |
| total_candidates | 63 |
| VERIFIED | 17 |
| PROBABLE | 5 |
| UNKNOWN | 13 |
| SUPPORT_COMPONENT | 13 |
| NOT_A_CLANK | 14 |
| NEEDS_RECONSTRUCTION | 1 |
| github_backed | 38 |
| local_only | 25 |
| dirty_repositories | 16 |
| duplicate_identity_groups | 8 |

## Duplicate identity groups

- `anil-ganti-nbc/clank-architecture`
  - `C:\Users\anil\Clanks\clank-architecture`
  - `C:\Users\anil\Documents\Default Project\clank-architecture`
- `anil-ganti-nbc/feature-phone-clank`
  - `C:\Users\anil\Clanks\feature-phone-clank`
  - `C:\Users\anil\Documents\Default Project\feature-phone-clank`
- `anil-ganti-nbc/korean-tech-wire`
  - `C:\Users\anil\Clanks\korean-tech-wire`
  - `C:\Users\anil\Documents\Default Project\korean-tech-wire`
- `anil-ganti-nbc/smartphone-clank`
  - `C:\Users\anil\Clanks\smartphone-clank`
  - `C:\Users\anil\Documents\Default Project\smartphone-clank`
- `anil-ganti-nbc/smartwatch-clank`
  - `C:\Users\anil\Clanks\smartwatch-clank`
  - `C:\Users\anil\Documents\Default Project\smartwatch-clank`
- `anil-ganti-nbc/tablet-clank`
  - `C:\Users\anil\Clanks\tablet-clank`
  - `C:\Users\anil\Documents\Default Project\tablet-clank`
- `anil-ganti-nbc/watch-clank`
  - `C:\Users\anil\Clanks\watch-clank`
  - `C:\Users\anil\Desktop\Watch clank\watch-clank`
- `anil-ganti-nbc/chudbox`
  - `C:\Users\anil\dau-ecosystem\repos\chudbox`
  - `C:\Users\anil\chudbox`

## Stale / abandoned / lagged checkouts

These are observations, not deletions:

- Documents/Default Project copies of fleet Clanks lag the Clanks-root HEAD (duplicate checkouts).
- Desktop `Watch clank\watch-clank` is on `codex/goldsmiths-uk-experimental` while Clanks-root watch-clank is on `fix/specialist-lead-terminal-state`.
- Several GitHub-only foundation repos (`unified-clank-platform`, `clank-ledger`, `clank-systems-handbook`) have no checkout under `C:\Users\anil\Clanks`.
- DAU practice labs last moved in August 2026 and are classified NOT_A_CLANK (adjacent education ecosystem).
- Empty/unclear desktop folders remain UNKNOWN rather than being dropped.

## VERIFIED (17)

- **chinese-tech-wire** — VERIFIED (high)  
  path: `C:\Users\anil\Clanks\chinese-tech-wire`  
  remote: `https://github.com/anil-ganti-nbc/chinese-tech-wire.git`  branch: `main` dirty=2  
  purpose: Chinese Tech Wire — V0.1  
  evidence: git remote origin on anil-ganti-nbc, README heading: Chinese Tech Wire — V0.1, project config: requirements.txt, git inspect (read-only)

- **clank-architecture** — VERIFIED (high)  
  path: `C:\Users\anil\Clanks\clank-architecture`  
  remote: `https://github.com/anil-ganti-nbc/clank-architecture.git`  branch: `main` dirty=1  
  purpose: Clank Architecture  
  evidence: git remote origin on anil-ganti-nbc, README heading: Clank Architecture, git inspect (read-only)

- **clank-ledger** — VERIFIED (medium)  
  path: `(github-only)`  
  remote: `https://github.com/anil-ganti-nbc/clank-ledger`  branch: `jules-m0-foundation-9044736841197359665`  
  purpose: -  
  evidence: GitHub repository under anil-ganti-nbc

- **clank-systems-handbook** — VERIFIED (medium)  
  path: `(github-only)`  
  remote: `https://github.com/anil-ganti-nbc/clank-systems-handbook`  branch: `main`  
  purpose: Clank Systems Handbook: interactive, evidence-backed engineering literacy for the Clank ecosystem. Not a programming course. DAU-compatible practice lab.  
  evidence: GitHub repository under anil-ganti-nbc, GitHub description

- **diagnostic-clank** — VERIFIED (high)  
  path: `C:\Users\anil\Documents\Default Project\diagnostic-clank`  
  remote: `https://github.com/anil-ganti-nbc/diagnostic-clank.git`  branch: `diagnostic-clank-2026-08`  
  purpose: Unified Clank Platform  
  evidence: git remote origin on anil-ganti-nbc, README heading: Unified Clank Platform, git inspect (read-only)

- **feature-phone-clank** — VERIFIED (high)  
  path: `C:\Users\anil\Clanks\feature-phone-clank`  
  remote: `https://github.com/anil-ganti-nbc/feature-phone-clank.git`  branch: `main` dirty=2  
  purpose: Feature Phone Clank  
  evidence: git remote origin on anil-ganti-nbc, README heading: Feature Phone Clank, project config: pyproject.toml, git inspect (read-only)

- **free-game-tracker** — VERIFIED (high)  
  path: `C:\Users\anil\Clanks\free-game-tracker`  
  remote: `https://github.com/anil-ganti-nbc/free-game-tracker.git`  branch: `main`  
  purpose: Newsroom  
  evidence: git remote origin on anil-ganti-nbc, README heading: Newsroom, project config: pyproject.toml, git inspect (read-only)

- **korean-tech-wire** — VERIFIED (high)  
  path: `C:\Users\anil\Clanks\korean-tech-wire`  
  remote: `https://github.com/anil-ganti-nbc/korean-tech-wire.git`  branch: `main` dirty=14  
  purpose: Korean Tech Wire  
  evidence: git remote origin on anil-ganti-nbc, README heading: Korean Tech Wire, project config: pyproject.toml, git inspect (read-only)

- **motherclank** — VERIFIED (high)  
  path: `C:\Users\anil\Documents\Default Project\motherclank`  
  remote: `https://github.com/anil-ganti-nbc/motherclank.git`  branch: `main`  
  purpose: Motherclank M0  
  evidence: git remote origin on anil-ganti-nbc, README heading: Motherclank M0, project config: pyproject.toml, git inspect (read-only)

- **oem-radar** — VERIFIED (high)  
  path: `C:\Users\anil\Clanks\oem-radar`  
  remote: `https://github.com/anil-ganti-nbc/oem-radar.git`  branch: `expansion-handheld-sixunited-m1` dirty=1  
  purpose: OEM Radar  
  evidence: git remote origin on anil-ganti-nbc, README heading: OEM Radar, project config: pyproject.toml, git inspect (read-only)

- **semiconductor-intelligence** — VERIFIED (high)  
  path: `C:\Users\anil\Clanks\semiconductor-intelligence`  
  remote: `https://github.com/anil-ganti-nbc/semiconductor-intelligence.git`  branch: `reddit-pilot-m0-hardware` dirty=2  
  purpose: Semiconductor Intelligence Platform 3.1 — Legacy import checkpoint  
  evidence: git remote origin on anil-ganti-nbc, README heading: Semiconductor Intelligence Platform 3.1 — Legacy import checkpoint, project config: pyproject.toml, git inspect (read-only)

- **smartphone-clank** — VERIFIED (high)  
  path: `C:\Users\anil\Clanks\smartphone-clank`  
  remote: `https://github.com/anil-ganti-nbc/smartphone-clank.git`  branch: `main`  
  purpose: Smartphone Intel Clank v0.2 — Evidence Engine  
  evidence: git remote origin on anil-ganti-nbc, README heading: Smartphone Intel Clank v0.2 — Evidence Engine, project config: requirements.txt, git inspect (read-only)

- **smartwatch-clank** — VERIFIED (high)  
  path: `C:\Users\anil\Clanks\smartwatch-clank`  
  remote: `https://github.com/anil-ganti-nbc/smartwatch-clank.git`  branch: `main`  
  purpose: Smartwatch Clank  
  evidence: git remote origin on anil-ganti-nbc, README heading: Smartwatch Clank, project config: pyproject.toml, git inspect (read-only)

- **standards-clank** — VERIFIED (high)  
  path: `C:\Users\anil\Clanks\standards-clank`  
  remote: `https://github.com/anil-ganti-nbc/standards-clank.git`  branch: `master`  
  purpose: Standards Clank  
  evidence: git remote origin on anil-ganti-nbc, README heading: Standards Clank, git inspect (read-only)

- **tablet-clank** — VERIFIED (high)  
  path: `C:\Users\anil\Clanks\tablet-clank`  
  remote: `https://github.com/anil-ganti-nbc/tablet-clank.git`  branch: `main` dirty=2  
  purpose: Tablet Clank  
  evidence: git remote origin on anil-ganti-nbc, README heading: Tablet Clank, project config: pyproject.toml, git inspect (read-only)

- **unified-clank-platform** — VERIFIED (medium)  
  path: `(github-only)`  
  remote: `https://github.com/anil-ganti-nbc/unified-clank-platform`  branch: `main`  
  purpose: -  
  evidence: GitHub repository under anil-ganti-nbc

- **watch-clank** — VERIFIED (high)  
  path: `C:\Users\anil\Clanks\watch-clank`  
  remote: `https://github.com/anil-ganti-nbc/watch-clank.git`  branch: `fix/specialist-lead-terminal-state` dirty=1  
  purpose: Watch Clank  
  evidence: git remote origin on anil-ganti-nbc, README heading: Watch Clank, project config: pyproject.toml, git inspect (read-only)

## PROBABLE (5)

- **clankops** — PROBABLE (medium)  
  path: `C:\Users\anil\Clanks\clankops`  
  remote: `-`  branch: `-` dirty=7  
  purpose: ClankOps  
  evidence: local git repo with Clank-like naming or README

- **cvc-workbench** — PROBABLE (medium)  
  path: `C:\Users\anil\Desktop\CVC Workbench`  
  remote: `-`  branch: `-`  
  purpose: CVC Workbench  
  evidence: local project with README and pyproject, no git, README heading: CVC Workbench

- **editorial-assist-clank** — PROBABLE (medium)  
  path: `C:\Users\anil\Desktop\Editorial Assist Clank`  
  remote: `-`  branch: `-`  
  purpose: Story Intelligence Clank  
  evidence: local project with README and pyproject, no git, README heading: Story Intelligence Clank

- **project-anilwriter** — PROBABLE (low)  
  path: `C:\Users\anil\Desktop\Project Anilwriter`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: AnilWriter-related tooling directory

- **quartermaster-clank** — PROBABLE (medium)  
  path: `C:\Users\anil\Desktop\Quartermaster Clank`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: desktop Quartermaster wrapper (launcher + token-stats)

## NEEDS_RECONSTRUCTION (1)

- **cvc-clank** — NEEDS_RECONSTRUCTION (medium)  
  path: `C:\Users\anil\Desktop\CVC Clank`  
  remote: `-`  branch: `-` git_error=fatal: detected dubious ownership in repository at 'C:/Users/anil/Desktop/CVC Clank'  
  purpose: CVC Clank  
  evidence: git inspect blocked by dubious ownership, README identifies CVC Clank

## SUPPORT_COMPONENT (13)

- **clank-architecture** — SUPPORT_COMPONENT (high)  
  path: `C:\Users\anil\Documents\Default Project\clank-architecture`  
  remote: `https://github.com/anil-ganti-nbc/clank-architecture.git`  branch: `main` dirty=3  
  purpose: Clank Architecture  
  evidence: git remote origin on anil-ganti-nbc, README heading: Clank Architecture, git inspect (read-only), secondary local checkout; primary is C:\Users\anil\Clanks\clank-architecture

- **feature-phone-clank** — SUPPORT_COMPONENT (high)  
  path: `C:\Users\anil\Documents\Default Project\feature-phone-clank`  
  remote: `https://github.com/anil-ganti-nbc/feature-phone-clank.git`  branch: `main`  
  purpose: Feature Phone Clank  
  evidence: git remote origin on anil-ganti-nbc, README heading: Feature Phone Clank, project config: pyproject.toml, git inspect (read-only), secondary local checkout; primary is C:\Users\anil\Clanks\feature-phone-clank

- **fleet-db-backups** — SUPPORT_COMPONENT (high)  
  path: `C:\Users\anil\Clanks\_fleet_db_backups`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: underscore-prefixed directory under Clanks root

- **korean-tech-wire** — SUPPORT_COMPONENT (high)  
  path: `C:\Users\anil\Documents\Default Project\korean-tech-wire`  
  remote: `https://github.com/anil-ganti-nbc/korean-tech-wire.git`  branch: `main`  
  purpose: Korean Tech Wire  
  evidence: git remote origin on anil-ganti-nbc, README heading: Korean Tech Wire, project config: pyproject.toml, git inspect (read-only), secondary local checkout; primary is C:\Users\anil\Clanks\korean-tech-wire

- **launchers** — SUPPORT_COMPONENT (high)  
  path: `C:\Users\anil\Clanks\_Launchers`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: underscore-prefixed directory under Clanks root

- **reconciliation** — SUPPORT_COMPONENT (high)  
  path: `C:\Users\anil\Clanks\_Reconciliation`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: underscore-prefixed directory under Clanks root

- **redditadmission** — SUPPORT_COMPONENT (high)  
  path: `C:\Users\anil\Clanks\_RedditAdmission`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: underscore-prefixed directory under Clanks root

- **smartphone-clank** — SUPPORT_COMPONENT (high)  
  path: `C:\Users\anil\Documents\Default Project\smartphone-clank`  
  remote: `https://github.com/anil-ganti-nbc/smartphone-clank.git`  branch: `main`  
  purpose: Smartphone Intel Clank v0.2 — Evidence Engine  
  evidence: git remote origin on anil-ganti-nbc, README heading: Smartphone Intel Clank v0.2 — Evidence Engine, project config: requirements.txt, git inspect (read-only), secondary local checkout; primary is C:\Users\anil\Clanks\smartphone-clank

- **smartwatch-clank** — SUPPORT_COMPONENT (high)  
  path: `C:\Users\anil\Documents\Default Project\smartwatch-clank`  
  remote: `https://github.com/anil-ganti-nbc/smartwatch-clank.git`  branch: `main`  
  purpose: Smartwatch Clank  
  evidence: git remote origin on anil-ganti-nbc, README heading: Smartwatch Clank, project config: pyproject.toml, git inspect (read-only), secondary local checkout; primary is C:\Users\anil\Clanks\smartwatch-clank

- **tablet-clank** — SUPPORT_COMPONENT (high)  
  path: `C:\Users\anil\Documents\Default Project\tablet-clank`  
  remote: `https://github.com/anil-ganti-nbc/tablet-clank.git`  branch: `main`  
  purpose: Tablet Clank  
  evidence: git remote origin on anil-ganti-nbc, README heading: Tablet Clank, project config: pyproject.toml, git inspect (read-only), secondary local checkout; primary is C:\Users\anil\Clanks\tablet-clank

- **token-stats** — SUPPORT_COMPONENT (high)  
  path: `C:\Users\anil\Desktop\Quartermaster Clank\token-stats`  
  remote: `https://github.com/Annihilater/token-stats.git`  branch: `main` dirty=4  
  purpose: -  
  evidence: upstream token-stats checkout used by Quartermaster, remote is Annihilater/token-stats, not a Clank repo

- **watch-clank** — SUPPORT_COMPONENT (medium)  
  path: `C:\Users\anil\Desktop\Watch clank`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: desktop wrapper containing nested watch-clank checkout

- **watch-clank** — SUPPORT_COMPONENT (high)  
  path: `C:\Users\anil\Desktop\Watch clank\watch-clank`  
  remote: `https://github.com/anil-ganti-nbc/watch-clank.git`  branch: `codex/goldsmiths-uk-experimental` dirty=1  
  purpose: Watch Clank  
  evidence: git remote origin on anil-ganti-nbc, README heading: Watch Clank, project config: pyproject.toml, git inspect (read-only), secondary local checkout; primary is C:\Users\anil\Clanks\watch-clank

## UNKNOWN (13)

- **chudbox-local** — UNKNOWN (low)  
  path: `C:\Users\anil\Desktop\chudbox-local`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: desktop artefact without project metadata

- **clank-base** — UNKNOWN (low)  
  path: `C:\Users\anil\Clank Base`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: nearly empty home directory named Clank Base

- **clank-transfer** — UNKNOWN (low)  
  path: `C:\Users\anil\Documents\Default Project\clank-transfer`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: no git, README, or project config

- **clankopsdashboard** — UNKNOWN (medium)  
  path: `C:\Users\anil\Desktop\ClankOpsDashboard`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: empty desktop placeholder directory

- **clanks** — UNKNOWN (low)  
  path: `C:\Users\anil\Clanks`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: no git, README, or project config

- **default-project** — UNKNOWN (low)  
  path: `C:\Users\anil\Documents\Default Project`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: no git, README, or project config

- **desktop** — UNKNOWN (low)  
  path: `C:\Users\anil\Desktop`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: no git, README, or project config

- **diagnostic-clank-honor-miss-evidence** — UNKNOWN (low)  
  path: `C:\Users\anil\Desktop\diagnostic-clank-honor-miss-evidence`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: desktop artefact without project metadata

- **fleet-campaign** — UNKNOWN (low)  
  path: `C:\Users\anil\Documents\Default Project\fleet-campaign`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: no git, README, or project config

- **grand-dau-unification** — UNKNOWN (low)  
  path: `C:\Users\anil\Desktop\Grand DAU Unification`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: desktop artefact without project metadata

- **launcher** — UNKNOWN (low)  
  path: `C:\Users\anil\dau-ecosystem\launcher`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: no git, README, or project config

- **ledger** — UNKNOWN (low)  
  path: `C:\Users\anil\dau-ecosystem\ledger`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: no git, README, or project config

- **local-llm-eval** — UNKNOWN (low)  
  path: `(github-only)`  
  remote: `https://github.com/anil-ganti-nbc/local-llm-eval`  branch: `main`  
  purpose: -  
  evidence: GitHub repository without local checkout

## NOT_A_CLANK (14)

- **chudbox** — NOT_A_CLANK (high)  
  path: `C:\Users\anil\dau-ecosystem\repos\chudbox`  
  remote: `https://github.com/anil-ganti-nbc/chudbox.git`  branch: `main` dirty=1  
  purpose: Chudbox  
  evidence: Dead Air University lab/ecosystem artefact

- **chudbox** — NOT_A_CLANK (high)  
  path: `C:\Users\anil\chudbox`  
  remote: `https://github.com/anil-ganti-nbc/chudbox.git`  branch: `main` dirty=1  
  purpose: Chudbox  
  evidence: Dead Air University lab/ecosystem artefact

- **compiler-workbench** — NOT_A_CLANK (high)  
  path: `C:\Users\anil\dau-ecosystem\repos\compiler-workbench`  
  remote: `https://github.com/anil-ganti-nbc/compiler-workbench.git`  branch: `main`  
  purpose: Compiler Workbench  
  evidence: Dead Air University lab/ecosystem artefact

- **dau-ecosystem** — NOT_A_CLANK (high)  
  path: `C:\Users\anil\dau-ecosystem`  
  remote: `-`  branch: `-`  
  purpose: -  
  evidence: Dead Air University lab/ecosystem artefact

- **dau-practice-labs** — NOT_A_CLANK (high)  
  path: `C:\Users\anil\dau-ecosystem\repos\dau-practice-labs`  
  remote: `https://github.com/anil-ganti-nbc/dau-practice-labs.git`  branch: `main`  
  purpose: DAU Practice Labs  
  evidence: Dead Air University lab/ecosystem artefact

- **dau-world-generator** — NOT_A_CLANK (high)  
  path: `C:\Users\anil\dau-ecosystem\repos\dau-world-generator`  
  remote: `https://github.com/anil-ganti-nbc/dau-world-generator.git`  branch: `main`  
  purpose: DAU World Generator  
  evidence: Dead Air University lab/ecosystem artefact

- **fab-lab** — NOT_A_CLANK (high)  
  path: `C:\Users\anil\dau-ecosystem\repos\fab-lab`  
  remote: `https://github.com/anil-ganti-nbc/fab-lab.git`  branch: `main`  
  purpose: Fab Lab  
  evidence: Dead Air University lab/ecosystem artefact

- **idle-time-learning-doodad** — NOT_A_CLANK (high)  
  path: `C:\Users\anil\dau-ecosystem\repos\idle-time-learning-doodad`  
  remote: `https://github.com/anil-ganti-nbc/idle-time-learning-doodad.git`  branch: `main` dirty=1  
  purpose: Dead Air University  
  evidence: Dead Air University lab/ecosystem artefact

- **ml-lab** — NOT_A_CLANK (high)  
  path: `C:\Users\anil\dau-ecosystem\repos\ml-lab`  
  remote: `https://github.com/anil-ganti-nbc/ml-lab.git`  branch: `main`  
  purpose: ML Lab  
  evidence: Dead Air University lab/ecosystem artefact

- **movement-bench** — NOT_A_CLANK (high)  
  path: `C:\Users\anil\dau-ecosystem\repos\movement-bench`  
  remote: `https://github.com/anil-ganti-nbc/movement-bench.git`  branch: `main`  
  purpose: Movement Bench  
  evidence: Dead Air University lab/ecosystem artefact

- **os-lab** — NOT_A_CLANK (high)  
  path: `C:\Users\anil\dau-ecosystem\repos\os-lab`  
  remote: `https://github.com/anil-ganti-nbc/os-lab.git`  branch: `main`  
  purpose: OS Lab  
  evidence: Dead Air University lab/ecosystem artefact

- **packet-lab** — NOT_A_CLANK (high)  
  path: `C:\Users\anil\dau-ecosystem\repos\packet-lab`  
  remote: `https://github.com/anil-ganti-nbc/packet-lab.git`  branch: `main`  
  purpose: Packet Lab  
  evidence: Dead Air University lab/ecosystem artefact

- **pipeline-playground** — NOT_A_CLANK (high)  
  path: `C:\Users\anil\dau-ecosystem\repos\pipeline-playground`  
  remote: `https://github.com/anil-ganti-nbc/pipeline-playground.git`  branch: `main`  
  purpose: Pipeline Playground  
  evidence: Dead Air University lab/ecosystem artefact

- **self-audit-curriculum** — NOT_A_CLANK (high)  
  path: `C:\Users\anil\Desktop\self-audit-curriculum`  
  remote: `-`  branch: `master` dirty=2  
  purpose: Self-Audit Curriculum — Operations  
  evidence: README describes human training, not a Clank

