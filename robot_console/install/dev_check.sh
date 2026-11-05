#!/bin/bash
# Pre-commit safety net for robot_console. Checks exactly what is about to be committed:
# the staged tree is exported to a temp dir, so other sessions' uncommitted work cannot make it pass or fail.
#
# Stages (one PASS/FAIL/SKIP line each):
#   python  py_compile of every robot_console/*.py and tests/*.py      (always)
#   tests   pytest on tests/ in the console venv                       (when a .py or config file is staged)
#   web     vue-tsc -b + vite build of web/, reusing web/node_modules  (when a web/ file is staged)
#   i18n    tools/check_i18n.py: keys in both locales, backend error codes translated
#                                                                      (when web/ or robot_console/*.py is staged)
#
# Usage:
#   bash install/dev_check.sh             # check the staged tree, run only the stages the staged files need
#   bash install/dev_check.sh --all       # check the staged tree, run every stage
#   bash install/dev_check.sh --install   # install as .git/hooks/pre-commit (local, not versioned)
# Skip the hook only in an emergency: git commit --no-verify
set -uo pipefail

REPO="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)" || exit 2
cd "$REPO"
if [ "${1:-}" = --install ]; then
    hook="$(git rev-parse --git-path hooks)/pre-commit"
    printf '#!/bin/bash\n# Installed by install/dev_check.sh --install\nexec bash "$(git rev-parse --show-toplevel)/install/dev_check.sh"\n' > "$hook"
    chmod +x "$hook"
    echo "installed $hook"
    exit 0
fi
ALL=0; [ "${1:-}" = --all ] && ALL=1

start=$(date +%s.%N)
staged=$(git diff --cached --name-only --diff-filter=ACMR) || { echo "dev_check: cannot read the git index"; exit 2; }
need_tests=$ALL; need_web=$ALL
grep -qE '\.py$|^config/|^tests/' <<< "$staged" && need_tests=1
grep -qE '^web/' <<< "$staged" && need_web=1

tmp=$(mktemp -d /tmp/robot_console_check.XXXXXX)
trap 'rm -rf "$tmp"' EXIT
git checkout-index --all --prefix="$tmp/" || { echo "dev_check: exporting the staged tree failed"; exit 2; }

fail=0
result() {  # result NAME STATUS DETAIL
    local colour=32
    [ "$2" = FAIL ] && colour=31
    [ "$2" = SKIP ] && colour=33
    printf '  \033[%sm%-4s\033[0m %-7s %s\n' "$colour" "$2" "$1" "$3"
    [ "$2" = FAIL ] && fail=1
}

echo "robot_console pre-commit check ($(grep -c . <<< "$staged") staged files)"

# --- python: syntax of every module and test
out=$(cd "$tmp" && python3 - <<'EOF' 2>&1
import glob, os, py_compile, sys, tempfile
bad = 0
out = os.path.join(tempfile.mkdtemp(), 'x.pyc')
for f in sorted(glob.glob('robot_console/*.py') + glob.glob('tests/*.py')):
    try:
        py_compile.compile(f, cfile=out, doraise=True)
    except py_compile.PyCompileError as e:
        print(e.msg); bad += 1
sys.exit(1 if bad else 0)
EOF
)
if [ $? -eq 0 ]; then result python PASS "py_compile"; else result python FAIL "py_compile"; echo "$out" | sed 's/^/         /'; fi

# --- tests: pytest in the console venv (fastapi, uvicorn). ament_index_python comes from ROS.
if [ "$need_tests" -eq 0 ]; then
    result tests SKIP "no .py/config staged"
elif [ ! -d "$tmp/tests" ]; then
    result tests SKIP "no tests/"
else
    py="$HOME/venvs/console/bin/python"
    [ -x "$py" ] || py=python3
    set +u; source /opt/ros/humble/setup.bash > /dev/null 2>&1; set -u
    # -p no:anyio: the anyio plugin in the venv needs a newer pytest than Ubuntu's 6.2.5
    if out=$(cd "$tmp" && "$py" -m pytest -p no:anyio -p no:cacheprovider -q tests 2>&1); then
        result tests PASS "$(tail -1 <<< "$out")"
    else
        result tests FAIL "$(tail -1 <<< "$out")"
        grep -E '^(FAILED|ERROR)|Error' <<< "$out" | head -20 | sed 's/^/         /'
    fi
fi

# --- web: type check and build with the existing node_modules
if [ "$need_web" -eq 0 ]; then
    result web SKIP "no web/ file staged"
elif [ ! -d "$REPO/web/node_modules" ]; then
    result web FAIL "web/node_modules missing; run npm ci in web/ first"
else
    ln -s "$REPO/web/node_modules" "$tmp/web/node_modules"
    export PATH="$HOME/opt/node/bin:$PATH"
    if out=$(cd "$tmp/web" && npx vue-tsc -b 2>&1 && npx vite build --outDir "$tmp/dist" --emptyOutDir --logLevel error 2>&1); then
        result web PASS "vue-tsc -b + vite build"
    else
        result web FAIL "vue-tsc -b + vite build"
        grep -E 'error|Error' <<< "$out" | head -20 | sed 's/^/         /'
    fi
fi

# --- i18n: literal t('...') keys exist in zh-CN and en; backend error codes have error.<code> (tools/check_i18n.py)
need_i18n_backend=$ALL
grep -qE '^robot_console/.*\.py$' <<< "$staged" && need_i18n_backend=1
if [ "$need_web" -eq 0 ] && [ "$need_i18n_backend" -eq 0 ]; then
    result i18n SKIP "no web/ or robot_console/*.py staged"
elif [ ! -f "$tmp/tools/check_i18n.py" ]; then
    result i18n SKIP "no tools/check_i18n.py"
else
    part=all
    [ "$need_web" -eq 1 ] && [ "$need_i18n_backend" -eq 0 ] && part=web
    [ "$need_web" -eq 0 ] && part=backend
    if out=$(python3 "$tmp/tools/check_i18n.py" --root "$tmp" --part "$part" 2>&1); then
        result i18n PASS "$(tail -1 <<< "$out")"
    else
        result i18n FAIL "$(tail -1 <<< "$out")"
        grep -E '^MISSING|Error' <<< "$out" | head -20 | sed 's/^/         /'
    fi
fi

printf '  %.1f s\n' "$(echo "$(date +%s.%N) - $start" | bc)"
if [ "$fail" -ne 0 ]; then
    echo "Commit blocked. Fix the failures above (emergency only: git commit --no-verify)."
    exit 1
fi
