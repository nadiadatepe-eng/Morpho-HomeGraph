#!/usr/bin/env bash
# Full mutation sweep, one harness at a time, with progress that survives.
#
# Written because the first attempt piped everything through `tail` and the
# buffer died with the shell: ten minutes of work, two lines of output. The
# rule that came out of it -- a long job has to write its progress down as it
# goes, not hand it over at the end.
set -u
cd "$(dirname "$0")/.." || exit 1

OUT="${1:-/tmp/mhg-sweep}"
mkdir -p "$OUT"
touch "$OUT/progress.txt" "$OUT/summary.tsv"

# Resumable: a harness whose log already carries a verdict is not re-run.
# The first attempt lost its parent shell 20 minutes in and would otherwise
# have started again from cp0 -- a sweep that cannot resume is a sweep nobody
# runs twice.
harnesses=()
for f in tests/mutate_cp*.py tests/mutate_no_real_paths.py; do
  done_log="$OUT/$(basename "$f" .py).log"
  if [ -s "$done_log" ] && grep -q 'killed by a named gate' "$done_log"; then
    continue
  fi
  harnesses+=("$f")
done
if [ ${#harnesses[@]} -eq 0 ]; then
  printf 'nothing left to run\n' | tee -a "$OUT/progress.txt"
  exit 0
fi
total=${#harnesses[@]}
n=0
started=$(date +%s)

for f in "${harnesses[@]}"; do
  n=$((n + 1))
  name=$(basename "$f" .py)
  printf 'JCODE_PROGRESS {"current":%d,"total":%d,"unit":"harnesses","message":"%s"}\n' \
    "$n" "$total" "$name" | tee -a "$OUT/progress.txt"
  t0=$(date +%s)
  timeout 1800 python3 "$f" > "$OUT/$name.log" 2>&1
  rc=$?
  t1=$(date +%s)
  line=$(grep -E 'killed by a named gate' "$OUT/$name.log" | tail -1)
  surv=$(grep -c '^SURVIVED' "$OUT/$name.log")
  crash=$(grep -c '^CRASH' "$OUT/$name.log")
  mis=$(grep -c '^misattrib' "$OUT/$name.log")
  printf '%s\t%s\t%s\t%s\t%s\t%ss\t%s\n' \
    "$name" "$rc" "$surv" "$crash" "$mis" "$((t1 - t0))" "$line" >> "$OUT/summary.tsv"
  printf '  %-24s rc=%s survived=%s crash=%s misattrib=%s  %ss\n' \
    "$name" "$rc" "$surv" "$crash" "$mis" "$((t1 - t0))" | tee -a "$OUT/progress.txt"
done

printf 'DONE in %ss\n' "$(( $(date +%s) - started ))" | tee -a "$OUT/progress.txt"
awk -F'\t' '{s+=$3; c+=$4; m+=$5} END {printf "TOTAL survived=%d crash=%d misattrib=%d over %d harnesses\n", s, c, m, NR}' \
  "$OUT/summary.tsv" | tee -a "$OUT/progress.txt"
