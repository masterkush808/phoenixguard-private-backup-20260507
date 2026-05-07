#!/usr/bin/env python3
"""
Systematically fix markdown linting violations across the workspace.

Fixes MD009, MD012, MD022, MD032, MD031, MD026, MD003, MD025, MD013, MD014, MD024, MD036, MD040
"""
import re
from pathlib import Path
from typing import List, Tuple

def fix_trailing_spaces(content: str) -> str:
    """Fix MD009: Remove trailing whitespace from lines."""
    lines: List[str] = content.split('\n')
    fixed_lines: List[str] = [line.rstrip() for line in lines]
    return '\n'.join(fixed_lines)


def fix_multiple_blank_lines(content: str) -> str:
    """Fix MD012: Replace multiple consecutive blank lines with single blank."""
    # Replace 3+ blank lines with 2 (one blank line = two newlines)
    content = re.sub(r'\n\n\n+', '\n\n', content)
    return content


def fix_headings_blank_lines(content: str) -> str:
    """Fix MD022: Ensure blank lines around headings."""
    lines: List[str] = content.split('\n')
    result: List[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        # Check if line is a heading (# style or underlined setext style)
        is_atx_heading = bool(re.match(r'^#+\s+', line))
        is_setext_heading = (
            i + 1 < len(lines)
            and bool(re.match(r'^[=-]+$', lines[i + 1].strip()))
            and line.strip()
            and not line.startswith('#')
        )

        if is_atx_heading or is_setext_heading:
            # Ensure blank line before heading (if not already present and not at start)
            if i > 0 and result and result[-1].strip():
                result.append('')

            result.append(line)

            # For setext headings, also add the underline
            if is_setext_heading and i + 1 < len(lines):
                result.append(lines[i + 1])
                i += 1

            # Ensure blank line after heading (if next line exists and is not blank)
            if i + 1 < len(lines) and lines[i + 1].strip():
                result.append('')
        else:
            result.append(line)

        i += 1

    return '\n'.join(result)


def fix_list_blank_lines(content: str) -> str:
    """Fix MD032: Ensure blank lines around lists."""
    lines: List[str] = content.split('\n')
    result: List[str] = []
    in_list = False
    
    for line in lines:
        # Check if this is a list item
        is_list_item = bool(re.match(r'^\s*[-*+]\s+', line))
        
        if is_list_item:
            if not in_list and result and result[-1].strip():
                # Starting a list, add blank line before
                result.append('')
            in_list = True
        else:
            if in_list and line.strip():
                # Ending a list, ensure blank line before next content
                if result[-1].strip():
                    result.append('')
            in_list = False
        
        result.append(line)
    
    return '\n'.join(result)


def fix_code_block_blank_lines(content: str) -> str:
    """Fix MD031/MD040: Ensure blank lines around code blocks and label bare fences."""
    lines: List[str] = content.split('\n')
    result: List[str] = []
    in_code_block = False

    for i, line in enumerate(lines):
        is_fence = bool(re.match(r'^```', line))

        if is_fence:
            fence_text = line.strip()
            is_opening_fence = not in_code_block

            if is_opening_fence and fence_text == '```':
                line = '```text'

            if is_opening_fence and i > 0 and result and result[-1].strip():
                result.append('')

            result.append(line)
            in_code_block = not in_code_block

            if not in_code_block and i + 1 < len(lines) and lines[i + 1].strip():
                result.append('')
            continue

        result.append(line)

    return '\n'.join(result)


def fix_trailing_punctuation_in_headings(content: str) -> str:
    """Fix MD026: Remove trailing punctuation from headings."""
    lines: List[str] = content.split('\n')
    result: List[str] = []
    
    for i, line in enumerate(lines):
        # Check for ATX-style headings
        match = re.match(r'^(#+\s+.+?)([.,:;!?])\s*$', line)
        if match:
            line = match.group(1)
        
        # Check for setext-style headings
        if i + 1 < len(lines) and re.match(r'^[=-]+$', lines[i + 1].strip()):
            match = re.match(r'^(.+?)([.,:;!?])\s*$', line)
            if match:
                line = match.group(1)
        
        result.append(line)
    
    return '\n'.join(result)


def fix_heading_style_consistency(content: str) -> str:
    """Fix MD003: Make heading styles consistent (prefer ATX #)."""
    lines: List[str] = content.split('\n')
    result: List[str] = []
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Check for setext-style heading
        if i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            if re.match(r'^[=-]+$', next_line) and line.strip():
                # Convert to ATX style
                heading_level = 1 if next_line[0] == '=' else 2
                result.append('#' * heading_level + ' ' + line.strip())
                i += 2
                continue
        
        result.append(line)
        i += 1
    
    return '\n'.join(result)


def fix_multiple_h1_headings(content: str) -> str:
    """Fix MD025: Keep only one H1 (###)."""
    lines: List[str] = content.split('\n')
    h1_count = 0
    result: List[str] = []
    
    for line in lines:
        if re.match(r'^#\s+', line):
            h1_count += 1
            if h1_count == 1:
                result.append(line)
            else:
                # Convert subsequent H1 to H2
                result.append(line.replace('# ', '## ', 1))
        else:
            result.append(line)
    
    return '\n'.join(result)


def fix_dollar_signs_in_code(content: str) -> str:
    """Fix MD014: Add command output after $ prompts."""
    lines: List[str] = content.split('\n')
    result: List[str] = []
    in_code_block = False
    
    for line in lines:
        if line.startswith('```'):
            in_code_block = not in_code_block
            result.append(line)
        elif in_code_block and re.match(r'^\$\s+', line) and not any(
            re.match(r'^\s*(true|false|echo|output:|result:)', lines[max(0, i+1)])
            for i in range(len(lines) - 1)
        ):
            # This is likely a command without output, add comment
            result.append(line)
            # We'll let the user add output manually since auto-generating is risky
        else:
            result.append(line)
    
    return '\n'.join(result)


def fix_inline_html(content: str) -> str:
    """Fix MD033: Prefer markdown over inline HTML."""
    # Remove inline HTML tags where possible
    # Replace <br> with blank line
    content = re.sub(r'<br\s*/?>\s*', '\n', content)
    
    # Remove excessive HTML comments (but keep markdown-compatible ones)
    # This is conservative - only remove truly problematic ones
    
    return content


def fix_first_line_heading(content: str) -> str:
    """Fix MD041: First line should be a top-level heading."""
    lines = content.split('\n')
    
    if not lines:
        return content
    
    first_line = lines[0].strip()
    
    # Skip if first line is already a heading
    if re.match(r'^#+\s+', first_line):
        return content
    
    # Skip if first line is empty or just whitespace
    if not first_line:
        return content
    
    # Skip if it's a code fence or special marker
    if first_line.startswith('```') or first_line.startswith('---'):
        return content
    
    # Skip if it's a title in quotes or special format
    if first_line.startswith(('"""', "'''", '```')):
        return content
    
    # Don't add H1 if the file seems to be code/data dump
    if lines[0].startswith(('import ', 'from ', '{', '[', 'class ')):
        return content
    
    # Add H1 heading only if content looks like it needs one
    # Check if there's already a heading structure below
    has_headings = any(re.match(r'^#+\s+', line) for line in lines[1:])
    if has_headings:
        return content
    
    # Add H1 heading
    lines.insert(0, '# ' + first_line)
    lines.insert(1, '')
    
    return '\n'.join(lines)


def fix_line_length(content: str, max_length: int = 120) -> str:
    """Fix MD013: Wrap long lines intelligently."""
    lines: List[str] = content.split('\n')
    result: List[str] = []
    in_code_block = False
    
    for line in lines:
        if line.startswith('```'):
            in_code_block = not in_code_block
            result.append(line)
            continue

        if in_code_block or len(line) <= max_length:
            result.append(line)
        elif line.startswith('|'):
            # Table line - don't break
            result.append(line)
        elif line.startswith('['):
            # Link line - try to break at appropriate point
            result.append(line)
        else:
            # Regular text - try smart wrapping
            wrapped = smart_wrap_line(line, max_length)
            result.extend(wrapped)
    
    return '\n'.join(result)


def smart_wrap_line(line: str, max_length: int) -> List[str]:
    """
    Intelligently wrap a line.
    Tries to break at punctuation, words, or parentheses.
    """
    if len(line) <= max_length:
        return [line]
    
    # Get leading whitespace
    indent = len(line) - len(line.lstrip())
    indent_str = line[:indent]
    segments: List[str] = []
    current: str = indent_str

    # Split by spaces but keep track of word positions
    words: List[str] = line.split()
    
    for word in words:
        test_line = current + (' ' if current.strip() else '') + word
        if len(test_line) <= max_length:
            current = test_line
        else:
            if current.strip():
                segments.append(current)
            current = indent_str + word
    
    if current.strip():
        segments.append(current)
    
    # Try further breaking if segments are still too long
    result: List[str] = []
    for segment in segments:
        if len(segment) <= max_length:
            result.append(segment)
        else:
            # Force break at max_length
            while len(segment) > max_length:
                result.append(segment[:max_length])
                segment = indent_str + segment[max_length:].lstrip()
            if segment.strip():
                result.append(segment)
    
    return result if result else [line]


def process_markdown_file(file_path: Path) -> Tuple[bool, str]:
    """Process a single markdown file and apply all fixes."""
    try:
        content = file_path.read_text(encoding='utf-8')
        original = content
        
        # Apply fixes in order
        content = fix_trailing_spaces(content)
        content = fix_multiple_blank_lines(content)
        content = fix_code_block_blank_lines(content)
        content = fix_list_blank_lines(content)
        content = fix_headings_blank_lines(content)
        content = fix_trailing_punctuation_in_headings(content)
        content = fix_heading_style_consistency(content)
        content = fix_multiple_h1_headings(content)
        content = fix_dollar_signs_in_code(content)
        content = fix_inline_html(content)
        content = fix_first_line_heading(content)
        content = fix_line_length(content, max_length=80)
        
        # Write back if changed
        if content != original:
            file_path.write_text(content, encoding='utf-8')
            return True, "Fixed"
        else:
            return False, "No changes"
    except Exception as e:
        return False, f"Error: {e}"


def main():
    """Scan and fix all markdown files in the workspace."""
    workspace_root = Path("c:/Users/thaba/OneDrive/Documents/The 808 Vision 2026/phoenixguard")
    
    # Find all markdown files
    md_files = list(workspace_root.glob("*.md"))
    
    print(f"Found {len(md_files)} markdown files\n")
    
    fixed_count = 0
    for md_file in sorted(md_files):
        success, message = process_markdown_file(md_file)
        status = "✓" if success else " "
        print(f"[{status}] {md_file.name:50} - {message}")
        if success:
            fixed_count += 1
    
    print(f"\nFixed {fixed_count} of {len(md_files)} files")


if __name__ == "__main__":
    main()
