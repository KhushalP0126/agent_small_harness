# clang.cindex Python Library Guide

## Setup

Install the `clang` package via pip:

```bash
pip install clang
```

You also need `libclang` installed on your system. On Ubuntu/Debian:

```bash
sudo apt-get install libclang-dev
```

On macOS with Homebrew:

```bash
brew install llvm
```

On Windows, download LLVM binaries and set the `LIBCLANG_PATH` environment variable to the directory containing `libclang.dll`.

## Common Imports

```python
from clang.cindex import (
    Index,
    TranslationUnit,
    Cursor,
    CursorKind,
    Type,
    TypeKind,
    SourceLocation,
    SourceRange,
    Diagnostic,
    Token,
    TokenKind,
    Config,
)
```

## Basic Usage Syntax

### Creating an Index and Parsing a Translation Unit

```python
# Create an index
index = Index.create()

# Parse a source file
translation_unit = index.parse("example.c", args=["-std=c99"])

# Get the cursor for the translation unit
cursor = translation_unit.cursor
```

### Traversing the AST

```python
def visit_node(node, depth=0):
    """Recursively visit AST nodes."""
    indent = "  " * depth
    print(f"{indent}{node.kind}: {node.spelling}")
    for child in node.get_children():
        visit_node(child, depth + 1)

visit_node(cursor)
```

### Getting Diagnostics

```python
for diagnostic in translation_unit.diagnostics:
    print(f"Severity: {diagnostic.severity}")
    print(f"Location: {diagnostic.location}")
    print(f"Message: {diagnostic.spelling}")
    print(f"Category: {diagnostic.category_name}")
```

## Important Objects

### Index
- `Index.create()` - Creates a new index for parsing translation units
- `index.parse(filepath, args=None, options=0)` - Parses a source file and returns a TranslationUnit

### TranslationUnit
- `translation_unit.cursor` - Returns the root cursor for the translation unit
- `translation_unit.diagnostics` - Returns a list of Diagnostic objects
- `translation_unit.get_tokens(location_range)` - Returns tokens within a source range
- `translation_unit.save(filename)` - Saves the translation unit to a file

### Cursor
- `cursor.kind` - Returns the CursorKind enum value
- `cursor.spelling` - Returns the name/spelling of the cursor
- `cursor.location` - Returns the SourceLocation
- `cursor.extent` - Returns the SourceRange
- `cursor.type` - Returns the Type of the cursor
- `cursor.get_children()` - Returns an iterator over child cursors
- `cursor.get_tokens()` - Returns tokens for the cursor's extent
- `cursor.referenced` - Returns the referenced cursor (for references)
- `cursor.definition` - Returns the definition cursor (for declarations)
- `cursor.is_definition()` - Returns True if this is a definition

### Type
- `type.kind` - Returns the TypeKind enum value
- `type.spelling` - Returns the type name as a string
- `type.get_canonical()` - Returns the canonical type
- `type.get_pointee()` - Returns the pointed-to type (for pointer types)
- `type.get_array_element_type()` - Returns the element type (for array types)
- `type.get_array_size()` - Returns the array size
- `type.get_result()` - Returns the return type (for function types)
- `type.get_argument_types()` - Returns argument types (for function types)

### SourceLocation
- `location.file` - Returns the File object
- `location.line` - Returns the line number
- `location.column` - Returns the column number
- `location.offset` - Returns the byte offset

### SourceRange
- `range.start` - Returns the start SourceLocation
- `range.end` - Returns the end SourceLocation

### Diagnostic
- `diagnostic.severity` - Returns severity level (0-4)
- `diagnostic.location` - Returns the SourceLocation
- `diagnostic.spelling` - Returns the diagnostic message
- `diagnostic.category_name` - Returns the category name
- `diagnostic.fix_its` - Returns a list of FixIt objects

### Token
- `token.kind` - Returns the TokenKind enum value
- `token.spelling` - Returns the token text
- `token.location` - Returns the SourceLocation
- `token.extent` - Returns the SourceRange

## Common Methods

### Finding Specific Nodes

```python
def find_function_definitions(cursor, name=None):
    """Find all function definitions in the AST."""
    results = []
    for child in cursor.walk_preorder():
        if child.kind == CursorKind.FUNCTION_DECL and child.is_definition():
            if name is None or child.spelling == name:
                results.append(child)
    return results
```

### Getting Include Files

```python
def get_includes(translation_unit):
    """Get all included files."""
    includes = []
    for inclusion in translation_unit.get_includes():
        includes.append(inclusion.include.name)
    return includes
```

### Working with Tokens

```python
def get_tokens_in_range(translation_unit, start, end):
    """Get tokens within a specific range."""
    range = SourceRange.from_locations(start, end)
    return list(translation_unit.get_tokens(range))
```

## Pitfalls

1. **Memory Management**: `clang.cindex` uses C bindings. Ensure you don't hold references to objects after the `Index` is garbage collected.

2. **Thread Safety**: The library is not thread-safe. Create separate `Index` objects for each thread.

3. **libclang Version Mismatch**: The Python bindings must match the installed `libclang` version. Use `Config.set_library_file()` to specify a custom path if needed.

4. **Cursor Iteration**: `get_children()` returns an iterator that can only be traversed once. Use `list()` to materialize if needed.

5. **Token Lifetime**: Tokens are only valid while the `TranslationUnit` exists. Don't store tokens beyond the lifetime of their parent.

6. **Performance**: Walking the entire AST with `walk_preorder()` can be slow for large files. Use targeted traversal when possible.

7. **File Paths**: Always use absolute paths or ensure the working directory is correct when parsing files.

## Recommended Trusted API Surface

For most use cases, stick to these core APIs:

- **Parsing**: `Index.create()`, `index.parse()`
- **AST Navigation**: `cursor.get_children()`, `cursor.walk_preorder()`, `cursor.kind`, `cursor.spelling`
- **Type Information**: `cursor.type`, `type.kind`, `type.spelling`, `type.get_canonical()`
- **Location**: `cursor.location`, `cursor.extent`, `location.file`, `location.line`, `location.column`
- **Diagnostics**: `translation_unit.diagnostics`, `diagnostic.severity`, `diagnostic.spelling`
- **Tokens**: `translation_unit.get_tokens()`, `token.kind`, `token.spelling`

Avoid using experimental or undocumented features. The `CachedProperty`, `SpellingCache`, and `CCRStructure` classes are internal implementation details and should not be used directly.
