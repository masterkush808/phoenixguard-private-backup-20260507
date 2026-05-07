#!/usr/bin/env python3
"""Fix remaining markdownlint violations in TRADE_TRIGGERING_FIXES.md"""

# Read the file
with open('TRADE_TRIGGERING_FIXES.md', 'r') as f:
    content = f.read()

# Fix trailing space after "**Why**:" (line 225)
content = content.replace('**Why**: \n', '**Why**:\n')

# Fix table spacing for MD060 issues
content = content.replace('|-------|--------|-------|', '| --- | --- | --- |')

# Write back
with open('TRADE_TRIGGERING_FIXES.md', 'w') as f:
    f.write(content)

print("✅ Fixed: trailing spaces and table formatting")
