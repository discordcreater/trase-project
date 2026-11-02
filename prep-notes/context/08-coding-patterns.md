# Coding rounds: finish, then optimize

**The recurring failure (P1, P23):** the right idea arrives, but the code isn't running at minute 25. Fix the process, not the knowledge.

## The 45-minute protocol
1. **0–4 min:** restate, ask 2 clarifying questions (input size, empty/null, duplicates, case, Unicode), write 2 examples including an edge case.
2. **4–7 min:** say the simplest correct approach and its complexity. "I'll code this first, then optimize if we have time." Interviewers reward a working baseline.
3. **7–22 min:** code it in small functions; talk while typing; no premature optimization; use library calls (split, regex, `Collections`, `Arrays.sort`) freely.
4. **22–27 min:** run the examples by hand or with prints/tests; fix bugs; say edge cases aloud.
5. **27–40 min:** optimize or do the second problem; for refactors keep tests green after **every** step (never leave code red; if a hint repeats, stop and address it).
6. Last 3 min: complexity, trade-offs, what you'd add (tests, input validation, concurrency).

## Patterns that cover most medium questions
| Pattern | Signals | Template idea |
|---|---|---|
| Two pointers | sorted array, pairs, palindromes, in-place | i from left, j from right / slow-fast |
| Sliding window | longest/shortest substring with condition | expand right, shrink left while invalid; map of counts |
| Hash map counting | anagrams, frequency, dedupe, two-sum | `Map<K,Integer>`; `merge(k,1,Integer::sum)` |
| Prefix sum + binary search | weighted random, range sums | cumulative array; `Arrays.binarySearch` or lower-bound |
| Stack | parentheses, next greater element, monotonic | deque as stack |
| Heap | top-K, k-way merge, scheduling | size-K min-heap |
| BFS / DFS | grids, graphs, shortest path in unweighted graph | queue + visited; recursion or explicit stack |
| Topological sort | dependencies, course schedule | indegree + queue (Kahn) |
| Intervals | merge/insert/meeting rooms | sort by start; sweep |
| Binary search on answer | min capacity, max minimum | check(mid) monotonic |
| Dynamic programming | counts/min cost with overlapping subproblems | define state, transition, base; memo then table |
| Design classes | LRU cache, rate limiter, time-based KV | `LinkedHashMap` access order; deque + map; `TreeMap.floorKey` |
| String parsing | abbreviations, word counts, tokenizing | index pointer, `Character.isDigit/isLetter`, regex |

## The problems you were given, solved (practice them cold)
- **Valid word abbreviation** (Apple R2): two pointers i over word, j over abbr; if abbr[j] is a digit: reject leading '0', parse the number, advance i by it; else chars must match; at the end both pointers must be at the ends. O(n+m).
- **Count valid words in a sentence** (Apple R2): split on spaces (`trim().split("\\s+")`), validate each token with explicit rules (at most one hyphen surrounded by letters, at most one punctuation mark at the end, no digits) or a regex like `^([a-z]+(-[a-z]+)?)?[!.,]?$` (and non-empty); count matches.
- **Weighted random selection** (Apple ML): prefix sums; draw `r = random * total`; return the first index with prefix > r (binary search). Validate weights, handle zero weights, mention alias method for O(1) draws and `ThreadLocalRandom`.
- **Tennis kata refactor** (ServiceNow R2): extract methods per scoring state, names array for points, constants, keep tests green after each micro-step.

## Daily drill (until offers)
Two problems a day, 25-minute timer each, in CoderPad or a plain editor (no IDE autocomplete): one string/array easy-medium, one from the pattern table. Lists: NeetCode 150 (`neetcode.io/practice`), Blind 75.
