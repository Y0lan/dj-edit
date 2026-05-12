#!/usr/bin/env bash
# release.sh — cut a new dj-edit release in one shot.
#
# Usage:
#   ./scripts/release.sh <version> "<one-line summary>"
#   ./scripts/release.sh 0.1.1 "fix sync edge case + improve error messages"
#
# What it does:
#   1. Verifies you're on main, clean working tree, tag doesn't exist
#   2. Bumps version in pyproject.toml + CHANGELOG.md
#   3. Commits the bump
#   4. Tags v<version>
#   5. Pushes main + tag
#   6. Creates GitHub release
#   7. Waits for the GitHub-generated tarball to be available
#   8. Computes sha256 of the tarball
#   9. Updates the homebrew tap formula
#  10. Pushes the tap
#
# Env vars:
#   TAP_DIR — path to homebrew-dj-edit checkout (default: ~/Projects/homebrew-dj-edit)
#   DRY_RUN — set to 1 to print actions without executing
set -euo pipefail

VERSION="${1:-}"
SUMMARY="${2:-}"
TAP_DIR="${TAP_DIR:-$HOME/Projects/homebrew-dj-edit}"
DRY_RUN="${DRY_RUN:-0}"

usage() {
    cat <<EOF
release.sh — cut a new dj-edit release.

Usage:
    ./scripts/release.sh <version> "<one-line summary>"

Example:
    ./scripts/release.sh 0.1.1 "fix sync edge case + improve error messages"

Env:
    TAP_DIR  path to homebrew-dj-edit checkout (default: ~/Projects/homebrew-dj-edit)
    DRY_RUN  set to 1 for a dry run
EOF
    exit 1
}

[ -z "$VERSION" ] && usage
[ -z "$SUMMARY" ] && usage

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+ ]]; then
    echo "ERROR: version must be semver (e.g. 0.1.1), got: $VERSION" >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$REPO_ROOT"

step() { echo ""; echo "── $* ──"; }
run() {
    if [ "$DRY_RUN" = "1" ]; then
        echo "+ $*"
    else
        "$@"
    fi
}

# ── Pre-flight checks ──
step "Pre-flight"
BRANCH="$(git branch --show-current)"
if [ "$BRANCH" != "main" ]; then
    echo "ERROR: not on main (on $BRANCH)" >&2
    exit 2
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: working tree dirty. Commit or stash first:" >&2
    git status --short >&2
    exit 2
fi
if git rev-parse -q --verify "refs/tags/v$VERSION" >/dev/null; then
    echo "ERROR: tag v$VERSION already exists" >&2
    exit 2
fi
if [ ! -d "$TAP_DIR/.git" ]; then
    echo "ERROR: TAP_DIR=$TAP_DIR is not a git checkout" >&2
    exit 2
fi
echo "  ✓ on main, clean tree, tag fresh, tap dir exists"

# ── Bump version in pyproject.toml ──
step "Bumping version in pyproject.toml"
run sed -i.bak -E "s/^version = \"[^\"]+\"/version = \"$VERSION\"/" "$REPO_ROOT/pyproject.toml"
rm -f "$REPO_ROOT/pyproject.toml.bak"
echo "  pyproject.toml -> $VERSION"

# ── Prepend a CHANGELOG entry ──
step "Updating CHANGELOG.md"
DATE="$(date +%Y-%m-%d)"
NEW_ENTRY="## [$VERSION] — $DATE

$SUMMARY

### Changed
- (auto-generated — edit before publishing if needed)
"
if [ "$DRY_RUN" = "0" ]; then
    # Insert after the first '# Changelog' header
    awk -v entry="$NEW_ENTRY" '
        /^# Changelog/ && !inserted {
            print
            print ""
            print entry
            inserted = 1
            next
        }
        { print }
    ' "$REPO_ROOT/CHANGELOG.md" > "$REPO_ROOT/CHANGELOG.md.tmp"
    mv "$REPO_ROOT/CHANGELOG.md.tmp" "$REPO_ROOT/CHANGELOG.md"
    echo "  CHANGELOG.md ← new entry for v$VERSION"
    echo ""
    echo "  Review and edit CHANGELOG.md now if you want, then press ENTER (or ^C to abort)"
    read -r _
else
    echo "  + would prepend v$VERSION entry"
fi

# ── Commit + tag + push ──
step "Committing version bump"
run git add pyproject.toml CHANGELOG.md
run git commit -m "release: v$VERSION

$SUMMARY"

step "Tagging v$VERSION"
run git tag -a "v$VERSION" -m "v$VERSION: $SUMMARY"

step "Pushing main + tag"
run git push origin main
run git push origin "v$VERSION"

# ── Create GitHub release ──
step "Creating GitHub release"
if [ "$DRY_RUN" = "0" ]; then
    # Extract the [VERSION] section from CHANGELOG.md (next ## heading terminates)
    NOTES=$(awk -v ver="[$VERSION]" '
        /^## / {
            if (in_section) exit
            if (index($0, ver)) { in_section = 1; next }
        }
        in_section { print }
    ' "$REPO_ROOT/CHANGELOG.md")
    gh release create "v$VERSION" --title "v$VERSION — $SUMMARY" --notes "$NOTES"
else
    echo "  + gh release create v$VERSION"
fi

# ── Compute sha256 of the tarball ──
step "Fetching tarball + computing sha256"
TARBALL_URL="https://github.com/Y0lan/dj-edit/archive/refs/tags/v$VERSION.tar.gz"
# Retry up to 10 seconds for GitHub to make the tarball available
SHA=""
for i in 1 2 3 4 5; do
    SHA=$(curl -sL --fail "$TARBALL_URL" 2>/dev/null | sha256sum | cut -d' ' -f1 || true)
    if [ -n "$SHA" ] && [ "$SHA" != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" ]; then
        break
    fi
    echo "  waiting for tarball... (attempt $i/5)"
    sleep 3
done
if [ -z "$SHA" ]; then
    echo "ERROR: could not fetch tarball at $TARBALL_URL" >&2
    exit 3
fi
echo "  sha256: $SHA"

# ── Update the brew formula ──
step "Updating brew formula in $TAP_DIR"
FORMULA="$TAP_DIR/Formula/dj-edit.rb"
[ -f "$FORMULA" ] || { echo "ERROR: $FORMULA not found" >&2; exit 4; }

if [ "$DRY_RUN" = "0" ]; then
    sed -i.bak -E \
        -e "s|url \"https://github.com/Y0lan/dj-edit/archive/refs/tags/v[0-9.]+\.tar\.gz\"|url \"$TARBALL_URL\"|" \
        -e "s|sha256 \"[a-f0-9]+\"|sha256 \"$SHA\"|" \
        -e "s|version \"[0-9.]+\"|version \"$VERSION\"|" \
        "$FORMULA"
    rm -f "${FORMULA}.bak"
    echo "  formula updated"
fi

# ── Commit + push the tap ──
step "Pushing tap update"
cd "$TAP_DIR"
run git add "Formula/dj-edit.rb"
run git commit -m "dj-edit $VERSION"
run git push origin main

# ── Done ──
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  v$VERSION SHIPPED"
echo "═══════════════════════════════════════════════════════════════"
echo "  Release:   https://github.com/Y0lan/dj-edit/releases/tag/v$VERSION"
echo "  Formula:   https://github.com/Y0lan/homebrew-dj-edit/blob/main/Formula/dj-edit.rb"
echo ""
echo "  Your friend updates with:"
echo "    brew upgrade dj-edit"
echo "═══════════════════════════════════════════════════════════════"
