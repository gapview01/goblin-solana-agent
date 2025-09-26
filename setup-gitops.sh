#!/bin/bash
# GitOps Setup Script
# This script helps configure the GitOps workflow

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

echo "🚀 GitOps Workflow Setup"
echo "========================"
echo ""

# Check if we're in a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    print_error "Not in a git repository"
    exit 1
fi

# Get repository URL
repo_url=$(git config --get remote.origin.url)
if [[ $repo_url == *"github.com"* ]]; then
    repo_name=$(echo $repo_url | sed 's/.*github.com[:/]\([^.]*\).*/\1/')
    print_success "GitHub repository detected: $repo_name"
else
    print_warning "Not a GitHub repository - some features may not work"
fi

echo ""
print_status "Setting up GitOps workflow..."

# 1. Create feature branch for setup
current_branch=$(git branch --show-current)
if [ "$current_branch" = "main" ]; then
    print_status "Creating setup branch..."
    git checkout -b setup/gitops-workflow
    print_success "Created setup branch"
fi

# 2. Test the development workflow
print_status "Testing development workflow..."
if [ -f "./dev-workflow.sh" ]; then
    print_success "Development workflow script found"
else
    print_error "Development workflow script not found"
    exit 1
fi

# 3. Test the test suite
print_status "Testing test suite..."
if python3 -m pytest tests/ -q; then
    print_success "Test suite working correctly"
else
    print_error "Test suite has issues"
    exit 1
fi

# 4. Show branch protection instructions
echo ""
print_status "Branch Protection Setup Instructions:"
echo ""
echo "To complete the GitOps setup, configure branch protection in GitHub:"
echo ""
echo "1. Go to: https://github.com/$repo_name/settings/branches"
echo "2. Click 'Add rule'"
echo "3. Set branch name pattern: main"
echo "4. Configure the following settings:"
echo ""
echo "   ✅ Require a pull request before merging"
echo "   ✅ Require status checks to pass before merging"
echo "   ✅ Require branches to be up to date before merging"
echo "   ✅ Require linear history"
echo "   ✅ Restrict pushes that create files larger than 100MB"
echo "   ✅ Require conversation resolution before merging"
echo ""
echo "5. Under 'Status checks that are required':"
echo "   ✅ Add 'Build'"
echo "   ✅ Add 'Test'"
echo "   ✅ Add 'Integrate'"
echo "   ✅ Add 'Deploy'"
echo ""
echo "6. Click 'Create'"
echo ""

# 5. Show usage instructions
echo ""
print_status "Usage Instructions:"
echo ""
echo "For daily development:"
echo "  ./dev-workflow.sh \"Your commit message\""
echo ""
echo "For testing only:"
echo "  ./test.sh"
echo ""
echo "For production readiness:"
echo "  ./test_production.sh"
echo ""

# 6. Show workflow phases
echo ""
print_status "GitOps Workflow Phases:"
echo ""
echo "1. 🔨 Build    - Compile and validate code"
echo "2. 🧪 Test     - Run unit and integration tests"
echo "3. 🔗 Integrate - Validate module compatibility"
echo "4. 🚀 Deploy   - Deploy to staging environment"
echo "5. 🔀 Merge    - Merge to main (with approval)"
echo ""

# 7. Show benefits
echo ""
print_status "Benefits:"
echo ""
echo "✅ Zero service disruption"
echo "✅ Feature loss prevention"
echo "✅ Automated testing and deployment"
echo "✅ Branch protection prevents direct main pushes"
echo "✅ Auto-fix for common issues"
echo "✅ Fast feedback (< 2 minutes)"
echo ""

print_success "GitOps workflow setup completed!"
echo ""
print_warning "Next steps:"
echo "1. Configure branch protection in GitHub (see instructions above)"
echo "2. Test the workflow with: ./dev-workflow.sh \"Test commit\""
echo "3. Create a Pull Request to main to test the full pipeline"
echo ""
print_status "Happy coding! 🚀"
