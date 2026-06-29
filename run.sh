#!/usr/bin/env bash
# ============================================================
#  KAVACH allocation - one-click runner (macOS / Linux)
#  Usage:   ./run.sh                 (bundled sample template)
#           ./run.sh your_chart.xlsx (your filled chart)
#  Installs what is needed, runs the allocation (computing the
#  station/loco slots) and writes the output workbook + CSV.
#  All bundled data is synthetic / illustrative.
# ============================================================
cd "$(dirname "$0")" || exit 1
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python

PY=""
for v in 3.12 3.11 3.13; do
  if [ -z "$PY" ] && command -v "python$v" >/dev/null 2>&1; then PY="python$v"; fi
done
[ -z "$PY" ] && command -v python3 >/dev/null 2>&1 && PY=python3
[ -z "$PY" ] && command -v python  >/dev/null 2>&1 && PY=python
if [ -z "$PY" ]; then
  echo "Python 3 not found. Install Python 3.12 (https://www.python.org/downloads/) and retry."
  exit 1
fi

CHART="${1:-KAVACH_input_template.xlsx}"
BASE="$(basename "${CHART%.*}")"
mkdir -p output
OUT="output/${BASE}_compliant.xlsx"

echo "============================================================"
echo " KAVACH allocation - one-click run"
echo " Python : $PY"
echo " Chart  : $CHART"
echo " Output : $OUT"
echo "============================================================"
echo
echo "[1/2] Installing required packages (openpyxl, ortools)..."
"$PY" -m pip install -r frequency-timeslot-analysis/requirements.txt
echo
echo "[2/2] Running allocation (computes station + loco slots)..."
echo
"$PY" frequency-timeslot-analysis/run_allocation.py "$CHART" "$OUT" --rf-range 15 --no-boundary
echo
echo "============================================================"
echo " Done. Outputs in the 'output' folder:"
echo "   $OUT"
echo "   output/${BASE}_compliant.csv"
echo " (the table above shows station slots, loco slots and the plan)"
echo "============================================================"
