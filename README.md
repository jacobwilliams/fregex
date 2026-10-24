# fregex

Regular expressions for modern Fortran, using the same pattern syntax as Python's `re` module.

- Pure standard Fortran 2018, with no dependencies. Builds with [fpm](https://fpm.fortran-lang.org) on any platform.
- Backtracking engine with Python semantics: leftmost match, greedy/lazy/possessive quantifiers,
  and Python's rules for empty matches in `find_all`, `split` and `sub`.
- Tested against Python's `re` on about 25,000 generated cases (see [Testing](#testing)).

## Example

```fortran
program demo
    use fregex
    implicit none
    type(regex_t) :: re
    type(match_t) :: m

    re = regex('(?P<name>\w+)\s*::\s*(?P<vars>.*)', RE_IGNORECASE)
    if (re%search('integer :: i, j', m)) then
        print *, m%group('name')                  ! integer
        print *, m%group('vars'), m%start('vars') ! i, j   12
    end if

    print *, re_sub('\s*([=+])\s*', 'x=y+1', ' \1 ')  ! x = y + 1
end program demo
```

Add it to an fpm project with:

```toml
[dependencies]
fregex = { git = "https://github.com/jacobwilliams/fregex" }
```

## Syntax

Patterns are written like Python raw strings: a backslash in a Fortran string is just a backslash.

| Syntax | Meaning |
|---|---|
| `.` `^` `$` | any byte except newline, start, end (`$` also matches before a final newline) |
| `[abc]` `[^a-z]` | character class, negated class (`]` first is literal, `\d \w \s` allowed inside) |
| `\d \D \w \W \s \S` | digit, word character `[A-Za-z0-9_]`, whitespace, and their negations |
| `\b \B \A \Z` | word boundary, not a boundary, start of string, end of string |
| `\n \t \r \f \v \a \xhh \ooo` | byte escapes; any other escaped punctuation is literal |
| `*` `+` `?` `{m}` `{m,n}` `{m,}` `{,n}` | quantifiers, greedy; add `?` for lazy, `+` for possessive |
| `a\|b` | alternation, tried left to right |
| `(...)` `(?:...)` `(?P<name>...)` `(?<name>...)` | capturing, non-capturing and named groups |
| `\1` `(?P=name)` | backreferences |
| `(?=...)` `(?!...)` `(?<=...)` `(?<!...)` | lookahead and lookbehind (lookbehind may have a variable width) |
| `(?>...)` | atomic group |
| `(?ims)` `(?i-m:...)` | inline flags, for the rest of the group or for one group |
| `(?#...)` | comment |

Flags: `RE_IGNORECASE`, `RE_MULTILINE`, `RE_DOTALL` (add them together to combine).

Matching works on bytes with ASCII rules, like Python's `re` on `bytes`. UTF-8 text can be searched,
but `.` and character classes match single bytes, and case-insensitive matching only folds ASCII letters.
A quantifier after a multi-byte character applies to its last byte: write `(?:é)+`, not `é+`.

## API

Positions are 1-based and spans are inclusive: `string(m%start():m%end())` is the matched text.
An empty match has `end = start - 1`. A group that did not take part in the match has
`start = 0`, `end = -1` and empty text.

| Procedure | Description |
|---|---|
| `re = regex(pattern [, flags, stat, errmsg])` | compile (also `call re%compile(...)`) |
| `re%search(string [, m, pos, stat, errmsg])` | leftmost match at or after `pos` → logical |
| `re%match(string [, m, pos, ...])` | match starting exactly at `pos` |
| `re%fullmatch(string [, m, pos, ...])` | match from `pos` to the end of the string |
| `re%find_all(string [, stat, errmsg])` | all non-overlapping matches → `type(match_t)(:)` |
| `re%split(string [, maxsplit, ...])` | pieces between matches, plus captured groups → `type(regex_string_t)(:)` |
| `re%sub(string, repl [, count, nsubs, ...])` | replace with a template (`\1`, `\g<name>`, `\g<0>`, `\n`, ...) |
| `re%sub(string, fn [, count, nsubs, ...])` | replace with `fn(m)`, a function returning `character(:), allocatable` |
| `re%group_count()`, `re%group_index(name)`, `re%pattern()` | information about the pattern |
| `re%set_step_limit(limit)` | limit backtracking work per search (default 10 million steps) |
| `m%group([k])`, `m%group(name)` | text of a group (default 0, the whole match) |
| `m%start(k)`, `m%end(k)`, `m%matched(k)` | span of a group, and whether it matched (also by name) |
| `re_search`, `re_match`, `re_fullmatch`, `re_find_all`, `re_split`, `re_sub` | one-off versions taking a pattern string |
| `re_escape(string)` | escape special characters |

`re%sub` takes the subject string first, unlike Python's `re.sub(repl, string)`. This matches the
rest of the API, and it avoids a gfortran 15 bug: a function argument with a deferred-length result
that comes before a `character(*)` argument crashes at run time.

### Errors

Procedures that can fail have optional `stat` and `errmsg` arguments. If `stat` is absent and an
error occurs, the program stops with the message. Status codes:

| Code | Meaning |
|---|---|
| `RE_OK` | success |
| `RE_ERR_SYNTAX` | invalid pattern; the message gives the 1-based position, e.g. `missing ), unterminated subpattern at position 2` |
| `RE_ERR_TEMPLATE` | invalid replacement template |
| `RE_ERR_STEP_LIMIT` | the search exceeded the step limit (e.g. `(a+)+b` on a long run of `a`s) |
| `RE_ERR_NOT_COMPILED` | the regex was used before a successful compile |

The `re_*` convenience functions stop on an invalid pattern.

## Differences from Python's `re`

- Lookbehind may have a variable or unbounded width, e.g. `(?<=\bcall\s+)\w+`. Python requires a fixed width.
- `(?<name>...)` is accepted as well as `(?P<name>...)`.
- Inline global flags such as `(?i)` apply from where they appear to the end of the enclosing group. Python only allows them at the start of the pattern.
- `(?x)` (verbose mode) and `\N{...}` are not supported. The `a`, `u` and `L` flags are accepted and ignored.
- `split` returns an empty string for a capture group that did not match, where Python returns `None`.
- Repeat counts are limited to 100,000.

## Design

| Module | Role |
|---|---|
| `fregex` | public API |
| `fregex_parser` | pattern → syntax tree, with Python-style error messages |
| `fregex_compiler` | syntax tree → VM program; also computes the possible first bytes of a match, any literal prefix, and whether it is anchored, used to skip start positions |
| `fregex_vm` | backtracking VM: one stack holds choice points and the old values needed to undo captures; lookarounds and atomic groups run as nested calls; a repeated single byte test such as `\w+` is one instruction with one choice point |
| `fregex_match`, `fregex_template` | match results; replacement templates |
| `fregex_charset`, `fregex_string`, `fregex_program` | 256-bit byte sets, string helpers, instruction set |

A repeated sub-pattern that can match the empty string stops repeating after an iteration that
consumes nothing, as Python's engine does. This also rules out infinite loops.

## Performance

Python's `re` and fregex on the same patterns and generated texts, with results checked to agree.
The cases are defined in [tools/benchmark.py](tools/benchmark.py), which builds and runs
[bench/benchmark.f90](bench/benchmark.f90) and rewrites this table:

```sh
python -I tools/benchmark.py --readme      # or: pixi run bench
```

<!-- benchmark:start -->

| Case | Operation | Pattern | Subject | Python `re` | fregex | fregex speed |
|---|---|---|---|--:|--:|--:|
| literal, match at end | `search` | `needle` | prose, 200 KB | 38.1 µs | 49.9 µs | 🟥 1.31× slower |
| no match (full scan) | `search` | `[XYZ]{2}\d` | prose, 200 KB | 1.1 ms | 63.3 µs | 🟩 17.46× faster |
| words | `find_all` | `\w+` | prose, 200 KB | 2.22 ms | 2.77 ms | 🟥 1.25× slower |
| case-insensitive literal | `find_all` | `(?i)error` | log, 263 KB | 739 µs | 278 µs | 🟩 2.65× faster |
| dates | `find_all` | `\d{4}-\d{2}-\d{2}` | log, 263 KB | 1.58 ms | 523 µs | 🟩 3.02× faster |
| e-mail addresses | `find_all` | `[\w.+-]+@[\w-]+\.\w+` | prose, 200 KB | 3.09 ms | 2.06 ms | 🟩 1.50× faster |
| alternation of words | `find_all` | `\b(?:alpha\|beta\|gamma\|delta)\b` | prose, 200 KB | 1.25 ms | 751 µs | 🟩 1.67× faster |
| Fortran declarations | `find_all` | `(?im)^\s*(?P<type>integer\|real\|logical\|character\|type)\b.*::\s*(?P<vars>.*)$` | fortran, 123 KB | 663 µs | 815 µs | 🟥 1.23× slower |
| lookbehind | `find_all` | `(?<=call )\w+` | fortran, 123 KB | 608 µs | 72.2 µs | 🟩 8.42× faster |
| backreference (doubled words) | `find_all` | `\b(\w+)\s+\1\b` | prose, 200 KB | 2.53 ms | 3.24 ms | 🟥 1.28× slower |
| collapse whitespace | `sub` | `\s+` | prose, 200 KB | 2.74 ms | 1.14 ms | 🟩 2.41× faster |
| swap with groups | `sub` | `(\w+)@(\w+)` | prose, 200 KB | 3.27 ms | 4.49 ms | 🟥 1.37× slower |
| split CSV | `split` | `\s*,\s*` | csv, 12 KB | 141 µs | 134 µs | 🟨 1.05× faster |
| short fullmatch | `fullmatch` | `(\d{4})-(\d{2})-(\d{2})` | date, 10 B | 71.7 ns | 127 ns | 🟥 1.77× slower |
| compile e-mail pattern | `compile` | `[\w.+-]+@[\w-]+\.\w+` | — | 9.95 µs | 1.53 µs | 🟩 6.49× faster |
| compile declaration pattern | `compile` | `(?im)^\s*(?P<type>integer\|real\|logical\|character\|type)\b.*::\s*(?P<vars>.*)$` | — | 74.9 µs | 7.24 µs | 🟩 10.34× faster |

Measured 2026-10-24 on Apple M5: Python 3.13.17 `re` on `bytes`, GNU Fortran 15.3.0 with `--profile release --flag "-O3 -march=native -flto"`. Times are per operation, best of the repeated runs. Regenerate with `python -I tools/benchmark.py --readme`.

<!-- benchmark:end -->

## Testing

```sh
fpm test
```

There are two suites:

- `api`: hand-written tests of the API.
- `conformance`: cases generated by `tools/gen_conformance.py`, which runs Python's `re` on byte strings
  and records the expected results in `test/data`. The cases come from four sources:
  - hand-written feature cases;
  - patterns that must be rejected;
  - every regex in [fprettify](https://github.com/pseewald/fprettify), applied to its example Fortran files;
  - 1,500 random patterns, each tried on several random subjects of up to 8 bytes;
  - 800 random patterns with non-ASCII bytes (UTF-8 and invalid sequences), nested up to 5 deep, on
    subjects of up to 64 bytes, plus a few subjects of about 4,000 bytes.

Cases that take Python itself more than 50 ms (exponential backtracking) are left out: fregex's
step limit is meant to stop those.

To regenerate the data (Python 3.11 or later):

```sh
python -I tools/gen_conformance.py                       # keeps the existing fprettify cases
python -I tools/gen_conformance.py --fprettify DIR       # regenerates them from an fprettify checkout
```

## License

MIT. See [LICENSE](LICENSE).

API documentation can be built with [FORD](https://github.com/Fortran-FOSS-Programmers/ford): `ford ford.md`.
