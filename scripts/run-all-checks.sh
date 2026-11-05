#!/usr/bin/env bash
#
# rms-host-voyager - Run All Checks Script
#
# This script runs linting, type checking, tests, Sphinx build, and
# Markdown lint as separate checks. In parallel mode all requested
# checks run concurrently.
#
# Usage:
#   ./scripts/run-all-checks.sh [options]
#
# Options:
#   -p, --parallel         Run all requested checks in parallel (default)
#   -s, --sequential       Run all requested checks sequentially
#   -w, --pytest-workers N Pytest workers: auto (default), 1 (serial), or N
#   -c, --code             Run all code checks (sets each RUN_* code flag true)
#   -d, --docs             Run Sphinx, codespell, and PyMarkdown (RUN_SPHINX,
#                          RUN_CODESPELL, RUN_PYMARKDOWN)
#   -m, --markdown         Run codespell and PyMarkdown (RUN_CODESPELL,
#                          RUN_PYMARKDOWN)
#   --ruff-check           Run ruff check only (may combine with other --* flags)
#   --ruff-format          Run ruff format --check only
#   --flake8-cont          Run flake8 continuation-line checks only (E12x, E13x)
#   --mypy                 Run mypy only
#   --pytest               Run pytest only
#   --pyroma               Run pyroma only
#   --bandit               Run bandit only
#   --vulture              Run vulture only
#   --pip-audit            Run pip-audit only
#   --sphinx               Run Sphinx build only
#   --codespell            Run codespell only
#   --pymarkdown           Run PyMarkdown scan only
#   -h, --help             Show this help message
#
# Requires the virtualenv created by ./scripts/setup-venv.sh.
#
# Environment:
#   VENV or VENV_PATH        Path to virtualenv (default: $PROJECT_ROOT/venv)
#   CLEANUP_GRACE_PERIOD     Seconds to wait for graceful shutdown (default: 5)
#
#   Pytest coverage minimum: configure fail_under in coverage config (e.g.
#   pyproject.toml [tool.coverage.report] or .coveragerc [report]).
#
#   RUN_* (set by this script from CLI or full-run defaults): RUN_RUFF_CHECK,
#   RUN_RUFF_FORMAT, RUN_FLAKE8_CONT, RUN_MYPY, RUN_PYTEST, RUN_PYROMA,
#   RUN_BANDIT, RUN_VULTURE, RUN_PIP_AUDIT, RUN_SPHINX,
#   RUN_CODESPELL, RUN_PYMARKDOWN
#
#   Per-check toggles (true/false). The defaults match the checks in the
#   template's CI workflow; change them here (and in CI) for a given repo, or
#   export them to override a single run. Each check runs only if both RUN_*
#   and ENABLE_* are true (RUN_* from CLI or defaults below; ENABLE_* from env):
#     ENABLE_RUFF_CHECK   (default: true)
#     ENABLE_RUFF_FORMAT  off: it would remove deliberate alignment (default: false)
#     ENABLE_FLAKE8_CONT  continuation-line indent, E12x/E13x (default: true)
#     ENABLE_MYPY         mypy on tests/ only (default: true)
#     ENABLE_PYTEST       (default: true)
#     ENABLE_PYROMA       (default: true)
#     ENABLE_BANDIT       (default: false)
#     ENABLE_VULTURE      (default: false)
#     ENABLE_PIP_AUDIT    (default: true)
#     ENABLE_SPHINX       (default: true)
#     ENABLE_CODESPELL    codespell spelling gate (default: true)
#     ENABLE_PYMARKDOWN   PyMarkdown scan (default: true)
#
# Checks (each run separately; -d runs Sphinx and the text checks):
#   Code:     optional: ruff check, ruff format --check, flake8 continuation-line
#             indent, mypy, pytest, pyroma, bandit, vulture, pip-audit
#             (see ENABLE_* above). Ruff implements no E12x/E13x rule, so the
#             continuation-line indent checks come from flake8 instead.
#   Sphinx:   make -C docs html SPHINXOPTS="-W -n"
#   Text:     codespell src/ tests/ docs/ scripts/ README.md CONTRIBUTING.md CLAUDE.md
#             pymarkdown scan -r docs/ .claude/ README.md CONTRIBUTING.md CLAUDE.md
#
# Exit codes:
#   0 - All requested checks passed
#   1 - One or more checks failed
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
RESET='\033[0m'

# Default options
PARALLEL=true
PYTEST_WORKERS=auto
RUN_RUFF_CHECK=false
RUN_RUFF_FORMAT=false
RUN_FLAKE8_CONT=false
RUN_MYPY=false
RUN_PYTEST=false
RUN_PYROMA=false
RUN_BANDIT=false
RUN_VULTURE=false
RUN_PIP_AUDIT=false
RUN_SPHINX=false
RUN_CODESPELL=false
RUN_PYMARKDOWN=false
SCOPE_SPECIFIED=false

# Per-check defaults (override by exporting before invoking this script, or
# permanently change here)
: "${ENABLE_RUFF_CHECK:=true}"
: "${ENABLE_RUFF_FORMAT:=false}"
: "${ENABLE_FLAKE8_CONT:=true}"
: "${ENABLE_MYPY:=true}"
: "${ENABLE_PYTEST:=true}"
: "${ENABLE_PYROMA:=true}"
: "${ENABLE_BANDIT:=false}"
: "${ENABLE_VULTURE:=false}"
: "${ENABLE_PIP_AUDIT:=true}"
: "${ENABLE_SPHINX:=true}"
: "${ENABLE_CODESPELL:=true}"
: "${ENABLE_PYMARKDOWN:=true}"

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="${VENV:-${VENV_PATH:-$PROJECT_ROOT/venv}}"

# Track failures and final exit code
FAILED_CHECKS=()
EXIT_CODE=0

# Temp directory for parallel output and status files
TEMP_DIR=$(mktemp -d)

# Grace period (seconds) before SIGKILL after SIGTERM
CLEANUP_GRACE_PERIOD=${CLEANUP_GRACE_PERIOD:-5}
if ! echo "$CLEANUP_GRACE_PERIOD" | grep -qE '^[0-9]+$'; then
    echo "Error: CLEANUP_GRACE_PERIOD must be a non-negative integer (got: $CLEANUP_GRACE_PERIOD)" >&2
    exit 1
fi

_wait_or_kill() {
    local pid=$1
    [ -z "$pid" ] && return 0
    kill -TERM "$pid" 2>/dev/null || true
    local waited=0
    while [ "$waited" -lt "$CLEANUP_GRACE_PERIOD" ]; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
        waited=$((waited + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
        kill -KILL "$pid" 2>/dev/null || true
    fi
    wait "$pid" 2>/dev/null || true
    return 0
}

_cleanup() {
    rm -rf "$TEMP_DIR"
}

# On INT/TERM: kill all background check jobs with grace period, then exit
_cleanup_and_exit() {
    local sig_code=$1
    local pids
    pids=$(jobs -p)
    if [ -n "$pids" ]; then
        for pid in $pids; do
            _wait_or_kill "$pid"
        done
    fi
    _cleanup
    exit "$sig_code"
}
trap '_cleanup_and_exit 130' SIGINT
trap '_cleanup_and_exit 143' SIGTERM
trap _cleanup EXIT

print_header() {
    echo -e "\n${BOLD}${BLUE}===================================================${RESET}"
    echo -e "${BOLD}${BLUE}  $1${RESET}"
    echo -e "${BOLD}${BLUE}===================================================${RESET}\n"
}

print_section() {
    echo -e "\n${BOLD}${YELLOW}>>> $1${RESET}\n"
}

print_success() {
    echo -e "${GREEN}✓${RESET} $1"
}

print_error() {
    echo -e "${RED}✗${RESET} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${RESET} $1"
}

show_usage() {
    sed -n '/^# Usage:/,/^# Exit codes:/p' "$0" | sed 's/^# //g' | sed 's/^#//g'
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--parallel)
            PARALLEL=true
            shift
            ;;
        -s|--sequential)
            PARALLEL=false
            shift
            ;;
        -w|--pytest-workers)
            if [[ -z "${2:-}" || "$2" =~ ^- ]]; then
                echo -e "${RED}Error: -w/--pytest-workers requires a value (auto, 1, 2, ...)${RESET}" >&2
                show_usage
                exit 1
            fi
            PYTEST_WORKERS="$2"
            shift 2
            ;;
        --pytest-workers=*)
            PYTEST_WORKERS="${1#*=}"
            shift
            ;;
        -c|--code)
            RUN_RUFF_CHECK=true
            RUN_RUFF_FORMAT=true
            RUN_FLAKE8_CONT=true
            RUN_MYPY=true
            RUN_PYTEST=true
            RUN_PYROMA=true
            RUN_BANDIT=true
            RUN_VULTURE=true
            RUN_PIP_AUDIT=true
            SCOPE_SPECIFIED=true
            shift
            ;;
        -d|--docs)
            RUN_SPHINX=true
            RUN_CODESPELL=true
            RUN_PYMARKDOWN=true
            SCOPE_SPECIFIED=true
            shift
            ;;
        -m|--markdown)
            RUN_CODESPELL=true
            RUN_PYMARKDOWN=true
            SCOPE_SPECIFIED=true
            shift
            ;;
        --ruff-check)
            RUN_RUFF_CHECK=true
            SCOPE_SPECIFIED=true
            shift
            ;;
        --ruff-format)
            RUN_RUFF_FORMAT=true
            SCOPE_SPECIFIED=true
            shift
            ;;
        --flake8-cont)
            RUN_FLAKE8_CONT=true
            SCOPE_SPECIFIED=true
            shift
            ;;
        --mypy)
            RUN_MYPY=true
            SCOPE_SPECIFIED=true
            shift
            ;;
        --pytest)
            RUN_PYTEST=true
            SCOPE_SPECIFIED=true
            shift
            ;;
        --pyroma)
            RUN_PYROMA=true
            SCOPE_SPECIFIED=true
            shift
            ;;
        --bandit)
            RUN_BANDIT=true
            SCOPE_SPECIFIED=true
            shift
            ;;
        --vulture)
            RUN_VULTURE=true
            SCOPE_SPECIFIED=true
            shift
            ;;
        --pip-audit)
            RUN_PIP_AUDIT=true
            SCOPE_SPECIFIED=true
            shift
            ;;
        --sphinx)
            RUN_SPHINX=true
            SCOPE_SPECIFIED=true
            shift
            ;;
        --codespell)
            RUN_CODESPELL=true
            SCOPE_SPECIFIED=true
            shift
            ;;
        --pymarkdown)
            RUN_PYMARKDOWN=true
            SCOPE_SPECIFIED=true
            shift
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            echo -e "${RED}Error: Unknown option: $1${RESET}" >&2
            show_usage
            exit 1
            ;;
    esac
done

# Default: run all checks (each RUN_* true; ENABLE_* still filters per repo)
if [ "$SCOPE_SPECIFIED" = false ]; then
    RUN_RUFF_CHECK=true
    RUN_RUFF_FORMAT=true
    RUN_FLAKE8_CONT=true
    RUN_MYPY=true
    RUN_PYTEST=true
    RUN_PYROMA=true
    RUN_BANDIT=true
    RUN_VULTURE=true
    RUN_PIP_AUDIT=true
    RUN_SPHINX=true
    RUN_CODESPELL=true
    RUN_PYMARKDOWN=true
fi

START_TIME=$(date +%s)

print_header "rms-host-voyager - Running All Checks"

if [ "$PARALLEL" = true ]; then
    print_info "Running checks in PARALLEL mode"
else
    print_info "Running checks in SEQUENTIAL mode"
fi
if [ "$RUN_PYTEST" = true ] && [ "$ENABLE_PYTEST" = true ]; then
    print_info "Pytest workers: $PYTEST_WORKERS"
fi

# True if at least one code check is both selected (RUN_*) and enabled (ENABLE_*).
_code_checks_any_scheduled() {
    [ "$RUN_RUFF_CHECK" = true ] && [ "$ENABLE_RUFF_CHECK" = true ] && return 0
    [ "$RUN_RUFF_FORMAT" = true ] && [ "$ENABLE_RUFF_FORMAT" = true ] && return 0
    [ "$RUN_FLAKE8_CONT" = true ] && [ "$ENABLE_FLAKE8_CONT" = true ] && return 0
    [ "$RUN_MYPY" = true ] && [ "$ENABLE_MYPY" = true ] && return 0
    [ "$RUN_PYTEST" = true ] && [ "$ENABLE_PYTEST" = true ] && return 0
    [ "$RUN_PYROMA" = true ] && [ "$ENABLE_PYROMA" = true ] && return 0
    [ "$RUN_BANDIT" = true ] && [ "$ENABLE_BANDIT" = true ] && return 0
    [ "$RUN_VULTURE" = true ] && [ "$ENABLE_VULTURE" = true ] && return 0
    [ "$RUN_PIP_AUDIT" = true ] && [ "$ENABLE_PIP_AUDIT" = true ] && return 0
    return 1
}

# ---- Code checks (ruff, mypy, pytest, pyroma, bandit, vulture, pip-audit) ----
run_code_checks() {
    local output_file="${1:-}"
    local status_file="${2:-}"

    if [ -n "$output_file" ]; then
        exec > "$output_file" 2>&1
    fi

    print_section "Code Checks"

    cd "$PROJECT_ROOT" || exit 1

    if ! _code_checks_any_scheduled; then
        print_info "No code checks scheduled (RUN_* and ENABLE_*); skipping code checks"
        return 0
    fi

    if [ ! -f "$VENV/bin/activate" ]; then
        print_error "Virtual environment not found at $VENV; run ./scripts/setup-venv.sh"
        [ -n "$status_file" ] && echo "Code - Virtual environment not found" >> "$status_file"
        return 1
    fi

    # shellcheck source=/dev/null
    source "$VENV/bin/activate"

    local failed=false
    local failed_checks=""

    if [ "$RUN_RUFF_CHECK" = true ] && [ "$ENABLE_RUFF_CHECK" = true ]; then
        print_info "Running ruff check..."
        if python -m ruff check src tests; then
            print_success "Ruff check passed"
        else
            print_error "Ruff check failed"
            failed=true
            failed_checks="${failed_checks}Code - Ruff check"$'\n'
        fi
    fi

    if [ "$RUN_RUFF_FORMAT" = true ] && [ "$ENABLE_RUFF_FORMAT" = true ]; then
        print_info "Running ruff format --check..."
        if python -m ruff format --check src tests; then
            print_success "Ruff format check passed"
        else
            print_error "Ruff format check failed"
            failed=true
            failed_checks="${failed_checks}Code - Ruff format"$'\n'
        fi
    fi

    if [ "$RUN_FLAKE8_CONT" = true ] && [ "$ENABLE_FLAKE8_CONT" = true ]; then
        print_info "Running flake8 continuation-line checks (E12x, E13x)..."
        # Ruff implements no rule in the E121-E133 range, so continuation-line
        # indentation is the one pycodestyle family it cannot gate. flake8 reads
        # .flake8 for the per-file exemptions.
        if python -m flake8 --select=E12,E13 src tests; then
            print_success "Flake8 continuation-line checks passed"
        else
            print_error "Flake8 continuation-line checks failed"
            failed=true
            failed_checks="${failed_checks}Code - Flake8 continuation"$'\n'
        fi
    fi

    if [ "$RUN_MYPY" = true ] && [ "$ENABLE_MYPY" = true ]; then
        print_info "Running mypy (tests/ only; src/ is deliberately unannotated)..."
        if MYPYPATH=src python -m mypy tests; then
            print_success "Mypy passed"
        else
            print_error "Mypy failed"
            failed=true
            failed_checks="${failed_checks}Code - Mypy"$'\n'
        fi
    fi

    # -n controls parallelism; --dist loadscope keeps each test module on one
    # worker to avoid time-mocking and fixture-isolation interference.
    # Coverage (--cov=src) and strict options come from pyproject.toml addopts.
    if [ "$RUN_PYTEST" = true ] && [ "$ENABLE_PYTEST" = true ]; then
        print_info "Running pytest (-n ${PYTEST_WORKERS})..."
        if python -m pytest -q -n "$PYTEST_WORKERS" --dist loadscope tests; then
            print_success "Pytest passed"
        else
            print_error "Pytest failed"
            failed=true
            failed_checks="${failed_checks}Code - Pytest"$'\n'
        fi
    fi

    if [ "$RUN_PYROMA" = true ] && [ "$ENABLE_PYROMA" = true ]; then
        print_info "Running pyroma (packaging metadata)..."
        if python -m pyroma .; then
            print_success "Pyroma passed"
        else
            print_error "Pyroma failed"
            failed=true
            failed_checks="${failed_checks}Code - Pyroma"$'\n'
        fi
    fi

    if [ "$RUN_BANDIT" = true ] && [ "$ENABLE_BANDIT" = true ]; then
        print_info "Running bandit..."
        if python -m bandit -c pyproject.toml -r src -q; then
            print_success "Bandit passed"
        else
            print_error "Bandit failed"
            failed=true
            failed_checks="${failed_checks}Code - Bandit"$'\n'
        fi
    fi

    if [ "$RUN_VULTURE" = true ] && [ "$ENABLE_VULTURE" = true ]; then
        print_info "Running vulture..."
        if python -m vulture src tests; then
            print_success "Vulture passed"
        else
            print_error "Vulture failed"
            failed=true
            failed_checks="${failed_checks}Code - Vulture"$'\n'
        fi
    fi

    # --skip-editable skips the package itself, which is installed in editable
    # mode; every other package in the environment is checked.
    if [ "$RUN_PIP_AUDIT" = true ] && [ "$ENABLE_PIP_AUDIT" = true ]; then
        print_info "Running pip-audit (known vulnerabilities in dependencies)..."
        if python -m pip_audit --skip-editable; then
            print_success "pip-audit passed"
        else
            print_error "pip-audit failed"
            failed=true
            failed_checks="${failed_checks}Code - pip-audit"$'\n'
        fi
    fi

    deactivate 2>/dev/null || true

    if [ "$failed" = true ]; then
        [ -n "$status_file" ] && printf '%s' "$failed_checks" >> "$status_file"
        return 1
    fi
    return 0
}

# ---- Sphinx build only ----
run_sphinx_build() {
    local output_file="${1:-}"
    local status_file="${2:-}"

    if [ -n "$output_file" ]; then
        exec > "$output_file" 2>&1
    fi

    print_section "Sphinx Build"

    cd "$PROJECT_ROOT" || exit 1

    if [ ! -f "$VENV/bin/activate" ]; then
        print_error "Virtual environment not found at $VENV; run ./scripts/setup-venv.sh"
        [ -n "$status_file" ] && echo "Sphinx - Virtual environment not found" >> "$status_file"
        return 1
    fi

    # shellcheck source=/dev/null
    source "$VENV/bin/activate"

    print_info "Building documentation (nitpicky, warnings treated as errors)..."
    if (cd docs && make clean && make html SPHINXOPTS="-W -n"); then
        print_success "Sphinx build passed"
        deactivate 2>/dev/null || true
        return 0
    else
        print_error "Sphinx build failed"
        [ -n "$status_file" ] && echo "Sphinx - Sphinx build" >> "$status_file"
        deactivate 2>/dev/null || true
        return 1
    fi
}

# ---- Text checks (codespell, PyMarkdown) ----
# True if at least one text check is both selected (RUN_*) and enabled (ENABLE_*).
_text_checks_any_scheduled() {
    [ "$RUN_CODESPELL" = true ] && [ "$ENABLE_CODESPELL" = true ] && return 0
    [ "$RUN_PYMARKDOWN" = true ] && [ "$ENABLE_PYMARKDOWN" = true ] && return 0
    return 1
}

run_markdown_checks() {
    local output_file="${1:-}"
    local status_file="${2:-}"

    if [ -n "$output_file" ]; then
        exec > "$output_file" 2>&1
    fi

    print_section "Text Checks (codespell, PyMarkdown)"

    cd "$PROJECT_ROOT" || exit 1

    if [ ! -f "$VENV/bin/activate" ]; then
        print_error "Virtual environment not found at $VENV; run ./scripts/setup-venv.sh"
        [ -n "$status_file" ] && echo "Markdown - Virtual environment not found" >> "$status_file"
        return 1
    fi

    # shellcheck source=/dev/null
    source "$VENV/bin/activate"

    local codespell_failed=false
    local pymarkdown_failed=false

    if [ "$RUN_CODESPELL" = true ] && [ "$ENABLE_CODESPELL" = true ]; then
        print_info "Running codespell (typos and British spellings)..."
        local spell_paths=()
        local path
        for path in src/ tests/ docs/ scripts/ README.md CONTRIBUTING.md CLAUDE.md; do
            [ -e "$path" ] && spell_paths+=("$path")
        done
        if [ ${#spell_paths[@]} -eq 0 ]; then
            print_info "No files found to spell-check"
        elif python -m codespell_lib "${spell_paths[@]}"; then
            print_success "codespell passed"
        else
            print_error "codespell failed"
            codespell_failed=true
        fi
    fi

    if [ "$RUN_PYMARKDOWN" = true ] && [ "$ENABLE_PYMARKDOWN" = true ]; then
        print_info "Running PyMarkdown scan (docs/, .claude/, root *.md, CLAUDE.md)..."
        local scan_paths=()
        [ -d "docs/" ] && scan_paths+=("docs/")
        [ -d ".claude/" ] && scan_paths+=(".claude/")
        [ -f "README.md" ] && scan_paths+=("README.md")
        [ -f "CONTRIBUTING.md" ] && scan_paths+=("CONTRIBUTING.md")
        [ -f "CLAUDE.md" ] && scan_paths+=("CLAUDE.md")
        if [ ${#scan_paths[@]} -eq 0 ]; then
            print_info "No Markdown files/directories found to scan"
        elif python -m pymarkdown scan -r "${scan_paths[@]}"; then
            print_success "PyMarkdown scan passed"
        else
            print_error "PyMarkdown scan failed"
            pymarkdown_failed=true
        fi
    fi

    deactivate 2>/dev/null || true

    # In parallel mode this lane runs in a subshell, so an append to
    # FAILED_CHECKS dies with it and status_file is the only channel the parent
    # sees. The return value has to cover both checks: returning 0 because
    # PyMarkdown passed is how a spelling failure would leave the run green.
    if [ "$codespell_failed" = true ] || [ "$pymarkdown_failed" = true ]; then
        if [ -n "$status_file" ]; then
            [ "$codespell_failed" = true ] && echo "Markdown - codespell" >> "$status_file"
            [ "$pymarkdown_failed" = true ] && echo "Markdown - PyMarkdown scan" >> "$status_file"
        fi
        return 1
    fi

    return 0
}

# ---- Collect status from a status file into FAILED_CHECKS ----
_collect_status() {
    local status_file=$1
    if [ -f "$status_file" ]; then
        while IFS= read -r line; do
            [ -n "$line" ] && FAILED_CHECKS+=("$line")
        done < "$status_file"
    fi
}

# ---- Run requested checks ----
if [ "$PARALLEL" = true ]; then
    print_info "Running requested checks in parallel, please wait..."

    pids=()
    temp_files=()
    status_files=()

    if _code_checks_any_scheduled; then
        code_output="$TEMP_DIR/code.log"
        code_status="$TEMP_DIR/code.status"
        temp_files+=("$code_output")
        status_files+=("$code_status")
        run_code_checks "$code_output" "$code_status" &
        pids+=($!)
    fi

    if [ "$RUN_SPHINX" = true ] && [ "$ENABLE_SPHINX" = true ]; then
        sphinx_output="$TEMP_DIR/sphinx.log"
        sphinx_status="$TEMP_DIR/sphinx.status"
        temp_files+=("$sphinx_output")
        status_files+=("$sphinx_status")
        run_sphinx_build "$sphinx_output" "$sphinx_status" &
        pids+=($!)
    fi

    if _text_checks_any_scheduled; then
        markdown_output="$TEMP_DIR/markdown.log"
        markdown_status="$TEMP_DIR/markdown.status"
        temp_files+=("$markdown_output")
        status_files+=("$markdown_status")
        run_markdown_checks "$markdown_output" "$markdown_status" &
        pids+=($!)
    fi

    # Wait for all jobs; any non-zero exit sets EXIT_CODE=1
    for pid in "${pids[@]}"; do
        if ! wait "$pid"; then
            EXIT_CODE=1
        fi
    done

    # Collect named failures from status files
    for status_file in "${status_files[@]}"; do
        _collect_status "$status_file"
    done

    # Safety net: if any status file had content, ensure EXIT_CODE reflects it
    [ ${#FAILED_CHECKS[@]} -gt 0 ] && EXIT_CODE=1

    # Print all outputs in a fixed order
    echo ""
    for log_file in "${temp_files[@]}"; do
        [ -f "$log_file" ] && cat "$log_file"
    done
else
    # Sequential — pass a status file so FAILED_CHECKS is populated
    if _code_checks_any_scheduled; then
        code_status="$TEMP_DIR/code.status"
        if ! run_code_checks "" "$code_status"; then
            EXIT_CODE=1
        fi
        _collect_status "$code_status"
    fi

    if [ "$RUN_SPHINX" = true ] && [ "$ENABLE_SPHINX" = true ]; then
        sphinx_status="$TEMP_DIR/sphinx.status"
        if ! run_sphinx_build "" "$sphinx_status"; then
            EXIT_CODE=1
        fi
        _collect_status "$sphinx_status"
    fi

    if _text_checks_any_scheduled; then
        markdown_status="$TEMP_DIR/markdown.status"
        if ! run_markdown_checks "" "$markdown_status"; then
            EXIT_CODE=1
        fi
        _collect_status "$markdown_status"
    fi
fi

# ---- Summary ----
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
MINUTES=$((ELAPSED / 60))
ELAPSED_SECONDS=$((ELAPSED % 60))

print_header "Summary"

if [ "$EXIT_CODE" -eq 0 ]; then
    print_success "All checks passed!"
    echo -e "${GREEN}${BOLD}✓ SUCCESS${RESET} - All checks completed successfully"
else
    print_error "Some checks failed:"
    if [ ${#FAILED_CHECKS[@]} -eq 0 ]; then
        echo -e "  ${RED}✗${RESET} One or more checks failed (see output above)"
    else
        for check in "${FAILED_CHECKS[@]}"; do
            echo -e "  ${RED}✗${RESET} $check"
        done
        echo -e "${RED}${BOLD}✗ FAILURE${RESET} - ${#FAILED_CHECKS[@]} check(s) failed"
    fi
fi

echo ""
print_info "Total time: ${MINUTES}m ${ELAPSED_SECONDS}s"
echo ""

exit "$EXIT_CODE"
