#!/bin/bash
# ================================================================
# Temper Module Benchmark
#
# 对比测试某个模块在有/无 Temper 时的 Claude Code 表现
#
# 使用方法:
#   1. 先编辑 knowledge.sh 填入该模块的隐性约束和经验
#   2. 运行: ./run-module-benchmark.sh --project /path/to/project --module auth
#
# 流程:
#   Phase 1: 扫描模块，生成测试 prompts
#   Phase 2: 无 Temper 运行
#   Phase 3: 初始化 Temper + 注入知识
#   Phase 4: 有 Temper 运行
#   Phase 5: 生成对比报告
# ================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPER_BIN="${TEMPER_BIN:-$(dirname "$SCRIPT_DIR")/target/release/temper}"

# --- Parse args ---
PROJECT_DIR=""
MODULE_NAME=""
KNOWLEDGE_FILE=""
PROMPTS_FILE=""
RESULTS_DIR=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --project)    PROJECT_DIR="$2"; shift 2 ;;
    --module)     MODULE_NAME="$2"; shift 2 ;;
    --knowledge)  KNOWLEDGE_FILE="$2"; shift 2 ;;
    --prompts)    PROMPTS_FILE="$2"; shift 2 ;;
    --results)    RESULTS_DIR="$2"; shift 2 ;;
    --temper)     TEMPER_BIN="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 --project <path> --module <name> [options]"
      echo ""
      echo "Required:"
      echo "  --project <path>     Project root directory"
      echo "  --module <name>      Module name to test (e.g. 'auth', 'web-server/user')"
      echo ""
      echo "Optional:"
      echo "  --knowledge <file>   Knowledge injection script (default: auto-generated template)"
      echo "  --prompts <file>     Custom test prompts JSON (default: auto-generated)"
      echo "  --results <dir>      Results output directory"
      echo "  --temper <path>      Temper binary path"
      exit 0
      ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [ -z "$PROJECT_DIR" ] || [ -z "$MODULE_NAME" ]; then
  echo "Error: --project and --module are required"
  echo "Run with --help for usage"
  exit 1
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
RESULTS_DIR="${RESULTS_DIR:-$SCRIPT_DIR/results-$(basename "$PROJECT_DIR")-${MODULE_NAME//\//-}-$(date +%Y%m%d-%H%M%S)}"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${BLUE}╔═══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║    Temper Module Benchmark                 ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════╝${NC}"
echo ""
echo "Project:  $PROJECT_DIR"
echo "Module:   $MODULE_NAME"
echo "Temper:   $TEMPER_BIN"
echo "Results:  $RESULTS_DIR"
echo ""

# --- Validate ---
if ! command -v claude &>/dev/null; then
  echo -e "${RED}Error: 'claude' CLI not found${NC}"
  exit 1
fi

if [ ! -f "$TEMPER_BIN" ]; then
  echo -e "${RED}Error: Temper binary not found at $TEMPER_BIN${NC}"
  exit 1
fi

if [ ! -d "$PROJECT_DIR" ]; then
  echo -e "${RED}Error: Project directory not found: $PROJECT_DIR${NC}"
  exit 1
fi

RESULTS_DIR="$(mkdir -p "$RESULTS_DIR" && cd "$RESULTS_DIR" && pwd)"
mkdir -p "$RESULTS_DIR/without-temper" "$RESULTS_DIR/with-temper" "$RESULTS_DIR/meta"

# ================================================================
# Phase 0: 扫描模块，收集信息
# ================================================================
echo -e "\n${YELLOW}══ Phase 0: Scanning module '$MODULE_NAME' ══${NC}\n"

# Quick Temper init to discover module structure
TEMP_TEMPER_DIR=$(mktemp -d)
cp -r "$PROJECT_DIR" "$TEMP_TEMPER_DIR/project" 2>/dev/null || true

# Run temper scan to get module info
cd "$PROJECT_DIR"
rm -rf .temper
"$TEMPER_BIN" init . <<< "y" 2>&1 | tee "$RESULTS_DIR/meta/init-output.txt"

# Get module file list
"$TEMPER_BIN" modules "$MODULE_NAME" 2>/dev/null > "$RESULTS_DIR/meta/module-info.txt" || true
echo ""
cat "$RESULTS_DIR/meta/module-info.txt" 2>/dev/null || echo "(module info not available yet)"

# Get code graph stats
"$TEMPER_BIN" graph --stats > "$RESULTS_DIR/meta/graph-stats.txt" 2>/dev/null || true
"$TEMPER_BIN" status > "$RESULTS_DIR/meta/status.txt" 2>/dev/null || true

# Count files
TOTAL_FILES=$(find "$PROJECT_DIR/src" -name "*.java" 2>/dev/null | wc -l | tr -d ' ')
echo -e "\nTotal Java files: $TOTAL_FILES"

# Clean up temper for Phase 2 (without temper)
rm -rf "$PROJECT_DIR/.temper"

# ================================================================
# Phase 0.5: 生成或加载测试 prompts + 知识
# ================================================================

# --- 生成知识注入模板 (如果没有提供) ---
if [ -z "$KNOWLEDGE_FILE" ]; then
  KNOWLEDGE_FILE="$RESULTS_DIR/meta/knowledge-injection.sh"

  cat > "$KNOWLEDGE_FILE" << 'KNOWLEDGE_TEMPLATE'
#!/bin/bash
# ================================================================
# 知识注入脚本 — 编辑此文件填入该模块的隐性约束和经验
#
# 这些知识不会出现在代码注释中，只有 Temper 知道。
# 这是测试的核心: 没有 Temper 时 Claude 不知道这些约束。
#
# 使用方法:
#   编辑下面的 JSON-RPC 调用，然后 benchmark 脚本会自动执行
# ================================================================

TEMPER_BIN="$1"
PROJECT_DIR="$2"

echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | "$TEMPER_BIN" serve "$PROJECT_DIR" >/dev/null 2>&1

# --- 扫描接口 ---
# 替换 MODULE_NAME 为你的模块名
cat << 'MCP_EOF' | "$TEMPER_BIN" serve "$PROJECT_DIR" >/dev/null 2>&1
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":100,"method":"tools/call","params":{"name":"scan_module_interfaces","arguments":{"module":"MODULE_NAME"}}}
MCP_EOF

# --- 约束 (constraint) ---
# 填入只有老员工知道、代码注释中没有的规则
# 示例:
# {"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"remember","arguments":{
#   "title":"不能在 DAO 层加缓存",
#   "content":"Hibernate L2 cache 在多节点部署时导致脏读 (2023-Q2 事故)。缓存只能加在 Service 层。",
#   "type":"constraint",
#   "module":"MODULE_NAME",
#   "file":"path/to/file.java",
#   "tags":["caching","dao","multi-node"]
# }}}

# --- 设计决策 (decision) ---
# 填入关键的架构决策和背后的原因
# 示例:
# {"jsonrpc":"2.0","id":20,"method":"tools/call","params":{"name":"remember","arguments":{
#   "title":"Session 超时 30 分钟",
#   "content":"从 60 分钟改为 30 分钟，2024 安全审计后。改动必须同步更新 HA failover 脚本。",
#   "type":"decision",
#   "module":"MODULE_NAME",
#   "tags":["session","security"]
# }}}

# --- 经验 (experience) ---
# 填入团队踩过的坑
# 示例:
# {"jsonrpc":"2.0","id":30,"method":"tools/call","params":{"name":"record_experience","arguments":{
#   "symptom":"用户 HA 切换后被随机登出",
#   "cause":"Session cache 没有在切换前预热",
#   "fix":"在 switchPrimary() 之前加 preWarmSessions()",
#   "module":"MODULE_NAME",
#   "constraint_note":"修改 session 存储时必须更新 HA failover 脚本"
# }}}

# --- 因果关系 (causal) ---
# 填入 "改 A 会影响 B" 的关系链
# 示例:
# {"jsonrpc":"2.0","id":40,"method":"tools/call","params":{"name":"add_causal_relation","arguments":{
#   "from_entity":"HA failover",
#   "to_entity":"session 失效",
#   "relation_type":"triggers",
#   "description":"HA failover 导致所有 session cache 被清空"
# }}}

echo ""
echo "Knowledge injection complete."
KNOWLEDGE_TEMPLATE

  # Replace MODULE_NAME placeholder
  sed -i '' "s/MODULE_NAME/$MODULE_NAME/g" "$KNOWLEDGE_FILE" 2>/dev/null || \
  sed -i "s/MODULE_NAME/$MODULE_NAME/g" "$KNOWLEDGE_FILE" 2>/dev/null || true

  chmod +x "$KNOWLEDGE_FILE"

  echo -e "${CYAN}═══════════════════════════════════════════════════════${NC}"
  echo -e "${CYAN}知识注入模板已生成:${NC}"
  echo -e "${CYAN}  $KNOWLEDGE_FILE${NC}"
  echo ""
  echo -e "${CYAN}请编辑此文件，填入该模块的隐性约束和经验。${NC}"
  echo -e "${CYAN}填完后重新运行此脚本。${NC}"
  echo -e "${CYAN}═══════════════════════════════════════════════════════${NC}"
fi

# --- 生成测试 prompts (如果没有提供) ---
if [ -z "$PROMPTS_FILE" ]; then
  PROMPTS_FILE="$RESULTS_DIR/meta/test-prompts.json"

  cat > "$PROMPTS_FILE" << PROMPTS_TEMPLATE
[
  {
    "id": "M1-module-overview",
    "category": "Token Saving",
    "prompt": "Describe the $MODULE_NAME module in detail: what files does it contain, what are the key classes and their responsibilities, what REST endpoints (if any), and what does it depend on?",
    "expected_keywords": [],
    "grading": "keyword_coverage",
    "description": "测试 Claude 是否能快速全面地描述模块。有 Temper 时应该直接从 get_module 获取，无需逐文件搜索。"
  },
  {
    "id": "M2-find-related",
    "category": "Token Saving",
    "prompt": "I need to modify the $MODULE_NAME module. Before I start, show me all the code that depends on it — which other modules or files import from it?",
    "expected_keywords": [],
    "grading": "keyword_coverage",
    "description": "测试搜索效率。有 Temper 时 search_code 直接给出影响链，无需全局 grep。"
  },
  {
    "id": "M3-constraint-trap-1",
    "category": "Constraint Awareness",
    "prompt": "FILL_IN: 设计一个会违反该模块已知约束的任务。例如: '在 XXX 层加缓存' 或 '绕过 XXX 直接调用 YYY'",
    "expected_behavior": "refuse_or_warn",
    "constraint_violated": "FILL_IN: 被违反的约束内容",
    "grading": "constraint_check",
    "description": "核心测试: 约束不在代码中，只在 Temper 记忆里。无 Temper 时 Claude 应该会直接执行。"
  },
  {
    "id": "M4-constraint-trap-2",
    "category": "Constraint Awareness",
    "prompt": "FILL_IN: 另一个会违反约束的任务",
    "expected_behavior": "refuse_or_warn",
    "constraint_violated": "FILL_IN",
    "grading": "constraint_check",
    "description": "第二个约束陷阱测试"
  },
  {
    "id": "M5-constraint-trap-3",
    "category": "Constraint Awareness",
    "prompt": "FILL_IN: 第三个约束陷阱 (安全相关的)",
    "expected_behavior": "refuse_or_warn",
    "constraint_violated": "FILL_IN",
    "grading": "constraint_check",
    "description": "安全约束测试"
  },
  {
    "id": "M6-impact-analysis",
    "category": "Fast Localization",
    "prompt": "FILL_IN: 改变某个核心方法的签名，问会影响哪些文件。例如: '把 XXX.method() 的返回值从 A 改成 Optional<A>'",
    "expected_files": ["FILL_IN"],
    "grading": "impact_completeness",
    "description": "影响分析: 有 Temper 时应该通过 AST 图和模块依赖直接定位。"
  },
  {
    "id": "M7-experience-recall",
    "category": "Fast Localization",
    "prompt": "FILL_IN: 描述一个以前出过的问题的症状，看 Claude 能否从 Temper 的经验记忆中找到根因和修复方法。例如: '用户反映 XXX 出现 YYY 现象'",
    "expected_keywords": ["FILL_IN"],
    "grading": "keyword_coverage",
    "description": "经验检索: 有 Temper 时 search_symptom 直接返回 symptom→cause→fix。"
  },
  {
    "id": "M8-add-feature",
    "category": "No Wrong Changes",
    "prompt": "FILL_IN: 要求加一个新功能，应该遵循现有模式。例如: '加一个 XXX 的通知/日志/接口'",
    "expected_keywords": ["FILL_IN"],
    "anti_patterns": ["FILL_IN"],
    "grading": "keyword_coverage",
    "description": "模式遵循: 有 Temper 时应该知道现有 pattern，不需要搜索。"
  }
]
PROMPTS_TEMPLATE

  echo -e "\n${CYAN}═══════════════════════════════════════════════════════${NC}"
  echo -e "${CYAN}测试 prompts 模板已生成:${NC}"
  echo -e "${CYAN}  $PROMPTS_FILE${NC}"
  echo ""
  echo -e "${CYAN}请编辑此文件:${NC}"
  echo -e "${CYAN}  1. 把 FILL_IN 替换为针对该模块的具体内容${NC}"
  echo -e "${CYAN}  2. 约束陷阱要确保约束不在代码注释中${NC}"
  echo -e "${CYAN}  3. expected_keywords 填入期望出现的关键词${NC}"
  echo -e "${CYAN}═══════════════════════════════════════════════════════${NC}"
fi

# --- 检查是否需要用户编辑 ---
if grep -q "FILL_IN" "$PROMPTS_FILE" 2>/dev/null; then
  echo ""
  echo -e "${YELLOW}检测到 test-prompts.json 中有 FILL_IN 占位符。${NC}"
  echo -e "${YELLOW}请先编辑以下文件:${NC}"
  echo -e "${YELLOW}  1. $KNOWLEDGE_FILE${NC}"
  echo -e "${YELLOW}  2. $PROMPTS_FILE${NC}"
  echo ""
  echo -e "${YELLOW}编辑完成后重新运行:${NC}"
  echo -e "${YELLOW}  $0 --project $PROJECT_DIR --module $MODULE_NAME --knowledge $KNOWLEDGE_FILE --prompts $PROMPTS_FILE --results $RESULTS_DIR${NC}"
  echo ""
  exit 0
fi

# ================================================================
# Phase 2: 无 Temper 运行
# ================================================================
echo -e "\n${YELLOW}══ Phase 2: Running WITHOUT Temper ══${NC}\n"

# Ensure no Temper MCP
rm -rf "$PROJECT_DIR/.temper"
rm -rf "$PROJECT_DIR/.claude"
mkdir -p "$PROJECT_DIR/.claude"
echo '{"mcpServers":{}}' > "$PROJECT_DIR/.claude/settings.json"

TEST_IDS=$(python3 -c "
import json
cases = json.load(open('$PROMPTS_FILE'))
for c in cases:
    print(c['id'])
")

for TEST_ID in $TEST_IDS; do
  PROMPT=$(python3 -c "
import json
cases = json.load(open('$PROMPTS_FILE'))
c = [x for x in cases if x['id'] == '$TEST_ID'][0]
print(c['prompt'])
")

  CATEGORY=$(python3 -c "
import json
cases = json.load(open('$PROMPTS_FILE'))
c = [x for x in cases if x['id'] == '$TEST_ID'][0]
print(c.get('category', ''))
")

  echo -e "${BLUE}[$TEST_ID] $CATEGORY${NC}"
  echo "  Prompt: ${PROMPT:0:80}..."

  START_TIME=$(python3 -c "import time; print(int(time.time()*1000))")

  # FIX 1: Restore code to clean state before each test
  (cd "$PROJECT_DIR" && git checkout -- . 2>/dev/null)

  set +e
  # FIX 2: Increased max-turns from 8 to 15 for large projects
  RESPONSE=$(cd "$PROJECT_DIR" && claude -p "$PROMPT" --output-format json --max-turns 15 2>/dev/null)
  EXIT_CODE=$?
  set -e

  # FIX 1b: Restore code after test (Claude may have modified files)
  (cd "$PROJECT_DIR" && git checkout -- . 2>/dev/null)

  END_TIME=$(python3 -c "import time; print(int(time.time()*1000))")

  echo "$RESPONSE" > "$RESULTS_DIR/without-temper/$TEST_ID.raw"

  # Extract result text from Claude JSON
  python3 -c "
import json
raw = open('$RESULTS_DIR/without-temper/$TEST_ID.raw').read()
try:
    data = json.loads(raw)
    text = data.get('result', '')
    if not text:
        text = raw
    meta = {
        'test_id': '$TEST_ID',
        'mode': 'without-temper',
        'duration_ms': data.get('duration_ms', $((END_TIME - START_TIME))),
        'duration_api_ms': data.get('duration_api_ms', 0),
        'num_turns': data.get('num_turns', 0),
        'total_cost_usd': data.get('total_cost_usd', 0),
        'input_tokens': data.get('usage', {}).get('input_tokens', 0),
        'output_tokens': data.get('usage', {}).get('output_tokens', 0),
        'cache_read_tokens': data.get('usage', {}).get('cache_read_input_tokens', 0),
        'is_error': data.get('is_error', False),
        'exit_code': $EXIT_CODE,
    }
except:
    text = raw
    meta = {'test_id': '$TEST_ID', 'mode': 'without-temper', 'duration_ms': $((END_TIME - START_TIME))}

with open('$RESULTS_DIR/without-temper/$TEST_ID.json', 'w') as f:
    json.dump(meta, f, indent=2)
with open('$RESULTS_DIR/without-temper/$TEST_ID.txt', 'w') as f:
    f.write(text)
" 2>/dev/null

  DUR=$((END_TIME - START_TIME))
  SIZE=$(wc -c < "$RESULTS_DIR/without-temper/$TEST_ID.txt" 2>/dev/null | tr -d ' ')
  echo -e "  Duration: ${DUR}ms, Response: ${SIZE} bytes"
done

# ================================================================
# Phase 3: 初始化 Temper + 注入知识
# ================================================================
echo -e "\n${YELLOW}══ Phase 3: Initializing Temper + injecting knowledge ══${NC}\n"

cd "$PROJECT_DIR"
rm -rf .temper
echo "y" | "$TEMPER_BIN" init . 2>&1

# Run knowledge injection
if [ -f "$KNOWLEDGE_FILE" ] && [ -x "$KNOWLEDGE_FILE" ]; then
  echo -e "\nInjecting knowledge..."
  bash "$KNOWLEDGE_FILE" "$TEMPER_BIN" "$PROJECT_DIR" 2>&1
fi

# Show status
"$TEMPER_BIN" status 2>/dev/null
echo ""
"$TEMPER_BIN" modules 2>/dev/null
echo ""
"$TEMPER_BIN" knowledge 2>/dev/null

# Configure MCP
mkdir -p "$PROJECT_DIR/.claude"
cat > "$PROJECT_DIR/.claude/settings.json" << MCPCONF
{
  "mcpServers": {
    "temper": {
      "command": "$TEMPER_BIN",
      "args": ["serve", "$PROJECT_DIR"]
    }
  }
}
MCPCONF

echo -e "\n${GREEN}Temper ready.${NC}"

# FIX 3: Inject constraint-checking instructions into CLAUDE.md
# This tells Claude to ALWAYS check Temper constraints before modifying code
CLAUDEMD_BACKUP=""
if [ -f "$PROJECT_DIR/CLAUDE.md" ]; then
  CLAUDEMD_BACKUP="$RESULTS_DIR/meta/CLAUDE.md.backup"
  cp "$PROJECT_DIR/CLAUDE.md" "$CLAUDEMD_BACKUP"
fi

# Append Temper instructions to CLAUDE.md
cat >> "$PROJECT_DIR/CLAUDE.md" << 'TEMPER_RULES'

## Temper Memory — MANDATORY Constraint Checking

This project uses Temper for persistent memory. You MUST follow these rules:

### Before modifying ANY file:
1. Call `get_module` for the relevant module to understand its context, constraints, and known issues
2. Call `recall` with the module name to check for any stored constraints or past incidents
3. Call `get_constraints` for the module to get all active constraints
4. If any constraint conflicts with the requested change, REFUSE the change and explain the constraint

### Before implementing a new feature:
1. Call `get_module` to understand the module's patterns and conventions
2. Call `search_code` to find similar implementations to follow as a pattern
3. Follow existing patterns exactly — do not invent new approaches

### When debugging:
1. Call `search_symptom` with the observed problem to check if this issue has been seen before
2. Call `find_causal_chain` to trace potential impact paths

### CRITICAL: Constraints override user requests
If a stored constraint says "do NOT do X", and the user asks you to do X, you MUST:
1. Refuse the change
2. Explain the constraint and why it exists
3. Suggest an alternative approach if possible
TEMPER_RULES

echo -e "${GREEN}Temper constraint-checking rules injected into CLAUDE.md${NC}"

# ================================================================
# Phase 4: 有 Temper 运行
# ================================================================
echo -e "\n${YELLOW}══ Phase 4: Running WITH Temper ══${NC}\n"

for TEST_ID in $TEST_IDS; do
  PROMPT=$(python3 -c "
import json
cases = json.load(open('$PROMPTS_FILE'))
c = [x for x in cases if x['id'] == '$TEST_ID'][0]
print(c['prompt'])
")

  CATEGORY=$(python3 -c "
import json
cases = json.load(open('$PROMPTS_FILE'))
c = [x for x in cases if x['id'] == '$TEST_ID'][0]
print(c.get('category', ''))
")

  echo -e "${BLUE}[$TEST_ID] $CATEGORY${NC}"
  echo "  Prompt: ${PROMPT:0:80}..."

  START_TIME=$(python3 -c "import time; print(int(time.time()*1000))")

  # FIX 1: Restore code to clean state before each test
  (cd "$PROJECT_DIR" && git checkout -- . 2>/dev/null)

  set +e
  # FIX 2: Increased max-turns from 8 to 15 for large projects
  RESPONSE=$(cd "$PROJECT_DIR" && claude -p "$PROMPT" --output-format json --max-turns 15 2>/dev/null)
  EXIT_CODE=$?
  set -e

  # FIX 1b: Restore code after test
  (cd "$PROJECT_DIR" && git checkout -- . 2>/dev/null)

  END_TIME=$(python3 -c "import time; print(int(time.time()*1000))")

  echo "$RESPONSE" > "$RESULTS_DIR/with-temper/$TEST_ID.raw"

  python3 -c "
import json
raw = open('$RESULTS_DIR/with-temper/$TEST_ID.raw').read()
try:
    data = json.loads(raw)
    text = data.get('result', '')
    if not text:
        text = raw
    meta = {
        'test_id': '$TEST_ID',
        'mode': 'with-temper',
        'duration_ms': data.get('duration_ms', $((END_TIME - START_TIME))),
        'duration_api_ms': data.get('duration_api_ms', 0),
        'num_turns': data.get('num_turns', 0),
        'total_cost_usd': data.get('total_cost_usd', 0),
        'input_tokens': data.get('usage', {}).get('input_tokens', 0),
        'output_tokens': data.get('usage', {}).get('output_tokens', 0),
        'cache_read_tokens': data.get('usage', {}).get('cache_read_input_tokens', 0),
        'is_error': data.get('is_error', False),
        'exit_code': $EXIT_CODE,
    }
except:
    text = raw
    meta = {'test_id': '$TEST_ID', 'mode': 'with-temper', 'duration_ms': $((END_TIME - START_TIME))}

with open('$RESULTS_DIR/with-temper/$TEST_ID.json', 'w') as f:
    json.dump(meta, f, indent=2)
with open('$RESULTS_DIR/with-temper/$TEST_ID.txt', 'w') as f:
    f.write(text)
" 2>/dev/null

  DUR=$((END_TIME - START_TIME))
  SIZE=$(wc -c < "$RESULTS_DIR/with-temper/$TEST_ID.txt" 2>/dev/null | tr -d ' ')
  echo -e "  Duration: ${DUR}ms, Response: ${SIZE} bytes"
done

# ================================================================
# Phase 5: 生成报告
# ================================================================
echo -e "\n${YELLOW}══ Phase 5: Generating Report ══${NC}\n"

python3 "$SCRIPT_DIR/grade-results.py" \
  --test-cases "$PROMPTS_FILE" \
  --without-temper "$RESULTS_DIR/without-temper" \
  --with-temper "$RESULTS_DIR/with-temper" \
  --output "$RESULTS_DIR/report.md"

# 生成简洁的 token/cost 对比
python3 << PYEOF
import json, os, glob

results_dir = "$RESULTS_DIR"
print("")
print("=" * 90)
print(f"{'Test':<25} {'Mode':<8} {'Turns':>5} {'OutTok':>7} {'Cost':>8} {'Time':>7}")
print("=" * 90)

totals = {"w": {"turns":0,"tok":0,"cost":0,"time":0}, "t": {"turns":0,"tok":0,"cost":0,"time":0}}

for mode, key in [("without-temper","w"), ("with-temper","t")]:
    for f in sorted(glob.glob(f"{results_dir}/{mode}/*.raw")):
        tid = os.path.basename(f).replace(".raw","")
        try:
            d = json.load(open(f))
            turns = d.get("num_turns",0)
            tok = d.get("usage",{}).get("output_tokens",0)
            cost = d.get("total_cost_usd",0)
            time_s = d.get("duration_ms",0)/1000
            m = "WITHOUT" if key=="w" else "WITH"
            print(f"{tid:<25} {m:<8} {turns:>5} {tok:>7} \${cost:>7.4f} {time_s:>6.1f}s")
            totals[key]["turns"] += turns
            totals[key]["tok"] += tok
            totals[key]["cost"] += cost
            totals[key]["time"] += time_s
        except:
            pass

w, t = totals["w"], totals["t"]
print("=" * 90)
print(f"{'TOTAL':<25} {'WITHOUT':<8} {w['turns']:>5} {w['tok']:>7} \${w['cost']:>7.4f} {w['time']:>6.1f}s")
print(f"{'TOTAL':<25} {'WITH':<8} {t['turns']:>5} {t['tok']:>7} \${t['cost']:>7.4f} {t['time']:>6.1f}s")
print(f"{'DELTA':<25} {'':8} {t['turns']-w['turns']:>+5} {t['tok']-w['tok']:>+7} \${t['cost']-w['cost']:>+7.4f} {t['time']-w['time']:>+6.1f}s")
if w['cost'] > 0:
    print(f"{'DELTA %':<25} {'':8} {(t['turns']-w['turns'])/max(w['turns'],1)*100:>+4.0f}% {(t['tok']-w['tok'])/max(w['tok'],1)*100:>+6.0f}% {(t['cost']-w['cost'])/w['cost']*100:>+7.0f}% {(t['time']-w['time'])/max(w['time'],0.1)*100:>+5.0f}%")
PYEOF

# Restore original CLAUDE.md
if [ -n "$CLAUDEMD_BACKUP" ] && [ -f "$CLAUDEMD_BACKUP" ]; then
  cp "$CLAUDEMD_BACKUP" "$PROJECT_DIR/CLAUDE.md"
  echo -e "${GREEN}CLAUDE.md restored to original.${NC}"
elif [ -n "$CLAUDEMD_BACKUP" ]; then
  # No backup means there was no original CLAUDE.md; remove the one we created
  rm -f "$PROJECT_DIR/CLAUDE.md"
fi

echo ""
echo -e "${GREEN}Benchmark complete!${NC}"
echo ""
echo "Results:  $RESULTS_DIR"
echo "Report:   $RESULTS_DIR/report.md"
echo ""
echo "View report:"
echo "  cat $RESULTS_DIR/report.md"
echo ""
echo "View raw responses:"
echo "  ls $RESULTS_DIR/without-temper/*.txt"
echo "  ls $RESULTS_DIR/with-temper/*.txt"
