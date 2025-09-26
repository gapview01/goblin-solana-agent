#!/bin/bash
# Development workflow command
# Usage: ./dev-workflow.sh "commit message"

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[$(date +'%H:%M:%S')]${NC} $1"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Check if we're in a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    print_error "Not in a git repository"
    exit 1
fi

# Check if we're on main branch
current_branch=$(git branch --show-current)
if [ "$current_branch" = "main" ]; then
    print_error "Cannot commit directly to main branch"
    print_warning "Please create a feature branch first:"
    echo "  git checkout -b feature/your-feature-name"
    exit 1
fi

# Get commit message
commit_message="$1"
if [ -z "$commit_message" ]; then
    print_error "Please provide a commit message"
    echo "Usage: ./dev-workflow.sh \"your commit message\""
    exit 1
fi

print_status "🚀 Starting development workflow..."

# ===========================================
# BUILD PHASE
# ===========================================
print_status "🔨 Building project..."

# Check Python syntax
python3 -m py_compile planner/planner.py 2>/dev/null || {
    print_error "Python syntax error in planner/planner.py"
    exit 1
}

python3 -m py_compile planner/llm_planner.py 2>/dev/null || {
    print_error "Python syntax error in planner/llm_planner.py"
    exit 1
}

python3 -m py_compile telegram_service/server.py 2>/dev/null || {
    print_error "Python syntax error in telegram_service/server.py"
    exit 1
}

print_success "Build completed"

# ===========================================
# TEST PHASE
# ===========================================
print_status "🧪 Running tests..."

# Run unit tests
if ! python3 -m pytest tests/ -v --tb=short; then
    print_error "Tests failed"
    exit 1
fi

print_success "All tests passed"

# ===========================================
# INTEGRATION PHASE
# ===========================================
print_status "🔗 Running integration checks..."

# Test module imports
python3 -c "
try:
    import planner.planner
    import planner.llm_planner
    import telegram_service.server
    print('✅ All modules import successfully')
except Exception as e:
    print(f'❌ Import failed: {e}')
    exit(1)
" || {
    print_error "Integration test failed"
    exit 1
}

print_success "Integration checks passed"

# ===========================================
# COMMIT PHASE
# ===========================================
print_status "📝 Committing changes..."

# Add all changes
git add .

# Commit with message
git commit -m "$commit_message"

print_success "Changes committed"

# ===========================================
# PUSH PHASE
# ===========================================
print_status "🚀 Pushing to remote..."

# Push to current branch
git push origin "$current_branch"

print_success "Changes pushed to $current_branch"

# ===========================================
# SUMMARY
# ===========================================
echo ""
print_success "🎉 Development workflow completed successfully!"
echo ""
echo "📋 Next steps:"
echo "  1. Create a Pull Request to main branch"
echo "  2. Wait for CI/CD pipeline to complete"
echo "  3. Review and approve the PR"
echo "  4. Merge to main (triggers production deployment)"
echo ""
echo "🔗 GitHub Actions will now run:"
echo "  ✅ Build → Test → Integrate → Deploy → Merge Check"
echo ""
echo "📊 Monitor progress at:"
echo "  https://github.com/$(git config --get remote.origin.url | sed 's/.*github.com[:/]\([^.]*\).*/\1/')/actions"
