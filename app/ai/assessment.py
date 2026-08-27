import json
import logging
import random
import re

from langchain_core.prompts import ChatPromptTemplate
from app.ai import get_llm, extract_llm_text
from app.ai.rag import retrieve_context

logger = logging.getLogger(__name__)

_GEN_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are a principal technical examiner. Given a list of a candidate's skills and resume context, "
     "generate EXACTLY 20 multiple-choice questions distributed across those skills.\n\n"
     "CRITICAL REQUIREMENTS:\n"
     "1. All 20 questions MUST be completely unique and distinct from each other.\n"
     "2. Do NOT repeat questions, concepts, phrasing, or templates.\n"
     "3. Vary question types across syntax, error handling, design patterns, performance, real-world debugging, and best practices.\n"
     "4. Each question must have exactly 4 plausible options, with only ONE unambiguously correct answer.\n"
     "5. Randomize the position of the correct answer (correct_index 0, 1, 2, or 3).\n"
     "6. Output ONLY valid, strictly-formatted JSON with no markdown fences, no backticks, and no commentary.\n\n"
     "JSON Schema:\n"
     '{{"questions": [{{"skill": "Skill Name", "question": "Question text?", "options": ["Option A", "Option B", "Option C", "Option D"], "correct_index": 0}}]}}'),
    ("human", "Candidate skills:\n{skills}\n\nRelevant resume context:\n{context}"),
])

_SUMMARY_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are a career coach. Given a per-skill correct/incorrect breakdown from a technical "
     "assessment, write a short strengths and weaknesses summary, and score the candidate 0-100 "
     "on three axes inferred from the breakdown: technical (raw correctness), communication "
     "(clarity implied by how well-rounded their correct skills are), and problem_solving (how "
     "well they handle harder/less common skills). Output ONLY valid JSON, no markdown fences, shape: "
     '{{"strengths": [str], "weaknesses": [str], "validated_skills": [str], '
     '"technical_pct": number, "communication_pct": number, "problem_solving_pct": number}} '
     "validated_skills = skills the candidate demonstrably scored well on."),
    ("human", "Per-skill breakdown (skill: correct/total):\n{breakdown}"),
])


def _strip_code_fences(text):
    if not text:
        return ""
    clean = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.IGNORECASE).strip()
    clean = re.sub(r"```$", "", clean).strip()
    return clean


def _normalize_skill(skill_str):
    return str(skill_str).strip()


def _sanitize_skills(skills):
    if not skills:
        return ["General Programming", "Problem Solving", "Software Engineering", "Git"]
    seen = set()
    cleaned = []
    for s in skills:
        if not s:
            continue
        trimmed = str(s).strip()
        lower = trimmed.lower()
        if lower and lower not in seen:
            seen.add(lower)
            cleaned.append(trimmed)
    return cleaned or ["General Programming", "Problem Solving", "Software Engineering", "Git"]


# Curated question bank covering standard tech stacks with verified, high-quality MCQs
_QUESTION_BANK = {
    "python": [
        {
            "question": "What is the primary difference between `deepcopy` and `copy` (shallow copy) in Python?",
            "options": [
                "`deepcopy` constructs a new compound object and recursively inserts copies of the objects found in the original.",
                "`copy` duplicates nested objects while `deepcopy` creates references only.",
                "`deepcopy` is only used for primitive data types like integers and strings.",
                "`copy` raises an exception if the object contains mutable lists or dictionaries."
            ],
            "correct_index": 0
        },
        {
            "question": "In Python, how does a generator function yield values compared to a standard function returning a list?",
            "options": [
                "Generators evaluate lazily and produce items on demand using `yield`, saving memory.",
                "Generators pre-compute all items in RAM and return an immutable tuple.",
                "Generators execute on a separate operating system thread automatically.",
                "Generators can only yield integer values and cannot maintain state."
            ],
            "correct_index": 0
        },
        {
            "question": "What is the purpose of the `@property` decorator in Python classes?",
            "options": [
                "To define getter, setter, and deleter methods with standard attribute access syntax.",
                "To make the class method run asynchronously in the background.",
                "To ensure the method can only be called from static context.",
                "To encrypt the returned attribute value in memory."
            ],
            "correct_index": 0
        },
        {
            "question": "What happens when you pass a mutable object (like a list) as a default argument in a Python function definition?",
            "options": [
                "The default object is created once when the function is defined and shared across all calls.",
                "A fresh copy of the list is instantiated each time the function is invoked.",
                "Python throws a SyntaxError during script interpretation.",
                "The list is converted into an immutable frozenset automatically."
            ],
            "correct_index": 0
        },
        {
            "question": "Which Python built-in module is used to achieve true parallelism for CPU-bound tasks bypassing the GIL?",
            "options": [
                "`multiprocessing`",
                "`threading`",
                "`asyncio`",
                "`queue`"
            ],
            "correct_index": 0
        },
        {
            "question": "What is the time complexity of looking up a key in a standard Python dictionary under average conditions?",
            "options": [
                "O(1)",
                "O(n)",
                "O(log n)",
                "O(n log n)"
            ],
            "correct_index": 0
        }
    ],
    "javascript": [
        {
            "question": "What is a closure in JavaScript?",
            "options": [
                "A function bundled together with references to its surrounding lexical state.",
                "A method used to immediately terminate an active event loop.",
                "A syntax error caused by unmatched curly braces.",
                "A built-in method that deep clones an object."
            ],
            "correct_index": 0
        },
        {
            "question": "What is the difference between `==` and `===` operators in JavaScript?",
            "options": [
                "`===` checks both value and type without type coercion, whereas `==` performs type coercion.",
                "`==` checks memory addresses while `===` checks primitive value equality.",
                "`===` is only valid for objects and arrays, not primitive numbers or strings.",
                "`==` is deprecated in modern ECMAScript specifications."
            ],
            "correct_index": 0
        },
        {
            "question": "In the JavaScript Event Loop, which queue has priority after the current synchronous execution context completes?",
            "options": [
                "Microtask queue (e.g., `Promise.then`, `queueMicrotask`)",
                "Macrotask queue (e.g., `setTimeout`, `setInterval`)",
                "Rendering frame callback queue",
                "Garbage collection sweep queue"
            ],
            "correct_index": 0
        },
        {
            "question": "What does the `bind()` method do when attached to a JavaScript function?",
            "options": [
                "Returns a new function with its `this` keyword permanently bound to the provided value.",
                "Immediately executes the function with the given arguments.",
                "Converts the function into a WebAssembly executable binary.",
                "Freezes the function definition to prevent monkey-patching."
            ],
            "correct_index": 0
        },
        {
            "question": "What is the output of `typeof NaN` in JavaScript?",
            "options": [
                "\"number\"",
                "\"nan\"",
                "\"undefined\"",
                "\"object\""
            ],
            "correct_index": 0
        }
    ],
    "typescript": [
        {
            "question": "In TypeScript, what is the main distinction between `interface` and `type` alias?",
            "options": [
                "Interfaces support declaration merging (open for extension), whereas type aliases cannot be redeclared.",
                "Types can only represent primitives while interfaces can only represent classes.",
                "Interfaces exist at runtime in generated JavaScript while types are erased.",
                "Type aliases cannot be used with generics or union types."
            ],
            "correct_index": 0
        },
        {
            "question": "What is the purpose of the `readonly` modifier in TypeScript?",
            "options": [
                "It prevents reassignment of a property after its initial creation.",
                "It makes the property accessible only within the declaring class.",
                "It stores the property in read-only CPU cache memory.",
                "It automatically generates getter and setter methods."
            ],
            "correct_index": 0
        },
        {
            "question": "What does the `unknown` type in TypeScript represent compared to `any`?",
            "options": [
                "A type-safe counterpart to `any` that requires type narrowing or assertion before performing operations.",
                "A type that can never occur, typically returned by infinite loops.",
                "An alias for `undefined` and `null` simultaneously.",
                "A deprecated type removed in TypeScript 4.0."
            ],
            "correct_index": 0
        }
    ],
    "react": [
        {
            "question": "Why should you never mutate React state directly (e.g., `state.items.push(newItem)`)?",
            "options": [
                "React relies on object identity comparisons to trigger re-renders, and mutating directly skips change detection.",
                "Direct mutation immediately throws a fatal runtime error in the browser.",
                "Direct mutation converts all component state into global window variables.",
                "React components become read-only once rendered to the Virtual DOM."
            ],
            "correct_index": 0
        },
        {
            "question": "What is the purpose of the dependency array in React's `useEffect` hook?",
            "options": [
                "To specify which state or prop values should trigger the effect callback when changed.",
                "To import external third-party packages into the component lifecycle.",
                "To define the order in which child components will mount to the DOM.",
                "To bind keyboard event listeners to the root window."
            ],
            "correct_index": 0
        },
        {
            "question": "What is the benefit of `useCallback` in React?",
            "options": [
                "It memoizes a callback function instance between renders to prevent unnecessary child re-renders.",
                "It executes asynchronous API requests without blocking UI paint cycles.",
                "It registers an event handler directly to the browser DOM instead of synthetic events.",
                "It caches the calculated return value of expensive pure mathematical functions."
            ],
            "correct_index": 0
        },
        {
            "question": "What is the purpose of the `key` prop when rendering dynamic lists in React?",
            "options": [
                "It helps React identify which items have changed, been added, or been removed during reconciliation.",
                "It encrypts list item data for secure transmission over HTTPS.",
                "It sets the CSS z-index and flex order of the rendered list items.",
                "It creates a persistent reference accessible via `document.getElementById`."
            ],
            "correct_index": 0
        }
    ],
    "sql": [
        {
            "question": "What is the primary difference between `WHERE` and `HAVING` clauses in SQL?",
            "options": [
                "`WHERE` filters rows before aggregation, while `HAVING` filters groups after `GROUP BY` aggregation.",
                "`HAVING` can only be used with primary keys, while `WHERE` works on foreign keys.",
                "`WHERE` is used exclusively for SELECT statements, while `HAVING` is used for UPDATE statements.",
                "`WHERE` is executed after sorting, while `HAVING` is executed before indexing."
            ],
            "correct_index": 0
        },
        {
            "question": "In SQL transactions, what does the 'I' in ACID properties stand for?",
            "options": [
                "Isolation (transactions execute concurrently without interference).",
                "Integrity (ensures all columns have non-null foreign keys).",
                "Indexing (automatically creates B-tree indexes for fast queries).",
                "Idempotency (running the same query multiple times produces identical results)."
            ],
            "correct_index": 0
        },
        {
            "question": "What type of JOIN returns all records when there is a match in either left or right table?",
            "options": [
                "FULL OUTER JOIN",
                "INNER JOIN",
                "CROSS JOIN",
                "LEFT SEMI JOIN"
            ],
            "correct_index": 0
        },
        {
            "question": "Why are database indexes (like B-Tree) added to specific columns?",
            "options": [
                "To drastically speed up data retrieval queries at the cost of slight overhead on write operations.",
                "To ensure column values are automatically compressed to save disk storage.",
                "To enforce encryption at rest for sensitive student information.",
                "To prevent duplicate rows from being inserted without a UNIQUE constraint."
            ],
            "correct_index": 0
        }
    ],
    "git": [
        {
            "question": "What is the key difference between `git merge` and `git rebase`?",
            "options": [
                "`git merge` preserves complete branch history with a merge commit, while `git rebase` rewrites history linearly.",
                "`git rebase` creates duplicate remote repositories while `git merge` syncs local tags.",
                "`git merge` can only be performed on the main branch, while `git rebase` only works on tags.",
                "`git rebase` deletes uncommitted working tree files automatically."
            ],
            "correct_index": 0
        },
        {
            "question": "What does `git stash` do?",
            "options": [
                "Temporarily shelves (stashes) uncommitted modifications so you can work with a clean working directory.",
                "Permanently purges all untracked files from the local filesystem.",
                "Pushes changes directly to remote origin without creating a commit.",
                "Reverts the last 5 commits on the active branch."
            ],
            "correct_index": 0
        },
        {
            "question": "What does a 'detached HEAD' state in Git mean?",
            "options": [
                "HEAD is pointing directly to a specific commit hash rather than a named local branch.",
                "The repository has lost its remote tracking connection to GitHub.",
                "The `.git` directory has been corrupted by concurrent write processes.",
                "The working tree has unresolved merge conflicts that cannot be auto-merged."
            ],
            "correct_index": 0
        }
    ],
    "docker": [
        {
            "question": "What is the primary advantage of multi-stage builds in Dockerfiles?",
            "options": [
                "They separate build tools and intermediate artifacts from the final lightweight production runtime image.",
                "They allow running multiple container operating systems inside a single process.",
                "They automatically publish the container image to Docker Hub upon build completion.",
                "They enable running Windows binaries directly inside Linux alpine images."
            ],
            "correct_index": 0
        },
        {
            "question": "What is the difference between Docker `CMD` and `ENTRYPOINT` instructions?",
            "options": [
                "`ENTRYPOINT` defines the default executable, while `CMD` sets default parameters that can be overridden at runtime.",
                "`CMD` runs during `docker build` while `ENTRYPOINT` only runs during `docker push`.",
                "`ENTRYPOINT` is deprecated in Docker Engine 20+ in favor of `CMD`.",
                "`CMD` requires root administrative permissions to execute."
            ],
            "correct_index": 0
        }
    ],
    "html": [
        {
            "question": "What is the purpose of semantic HTML elements such as `<article>`, `<nav>`, and `<main>`?",
            "options": [
                "To clearly describe their meaning to both the browser and developer, improving accessibility and SEO.",
                "To apply default CSS styling without requiring stylesheets.",
                "To automatically encrypt DOM nodes from scraping bots.",
                "To enable hardware acceleration for CSS animations."
            ],
            "correct_index": 0
        },
        {
            "question": "What does the `box-sizing: border-box` CSS rule do?",
            "options": [
                "Includes padding and border within the element's specified total width and height.",
                "Adds a visible 1px solid border to all nested child elements.",
                "Forces the element to be rendered as an inline flex container.",
                "Prevents the element from overflowing outside the viewport window."
            ],
            "correct_index": 0
        }
    ],
    "data structures": [
        {
            "question": "What is the average time complexity of searching an element in a balanced Binary Search Tree (BST)?",
            "options": [
                "O(log n)",
                "O(n)",
                "O(1)",
                "O(n^2)"
            ],
            "correct_index": 0
        },
        {
            "question": "Which data structure operates on a Last-In, First-Out (LIFO) principle?",
            "options": [
                "Stack",
                "Queue",
                "Min-Heap",
                "Circular Buffer"
            ],
            "correct_index": 0
        },
        {
            "question": "What is the primary purpose of a hash function in a Hash Table?",
            "options": [
                "To map keys of arbitrary size to fixed-size array indices for fast O(1) lookup.",
                "To sort all inserted keys in lexicographical ascending order.",
                "To encrypt table values so they cannot be read in memory dumps.",
                "To ensure no two keys ever produce the same hash value."
            ],
            "correct_index": 0
        }
    ],
    "general": [
        {
            "question": "Which HTTP status code signifies that a requested resource was successfully created on the server?",
            "options": [
                "201 Created",
                "200 OK",
                "204 No Content",
                "304 Not Modified"
            ],
            "correct_index": 0
        },
        {
            "question": "What makes an HTTP method 'idempotent' in RESTful API design?",
            "options": [
                "Making multiple identical requests has the same effect on server state as making a single request (e.g., GET, PUT, DELETE).",
                "The endpoint can only be called once every 60 seconds by a client.",
                "The endpoint never returns a JSON response body.",
                "The request requires two-factor authentication tokens."
            ],
            "correct_index": 0
        },
        {
            "question": "What is the primary purpose of unit testing in software development?",
            "options": [
                "To verify that individual isolated components or functions work correctly as expected.",
                "To test network latency across distributed cloud data centers.",
                "To validate end-to-end database replication under high concurrent load.",
                "To benchmark CPU hardware performance under stress."
            ],
            "correct_index": 0
        },
        {
            "question": "In web security, what is the best defense against Cross-Site Scripting (XSS) vulnerabilities?",
            "options": [
                "Context-aware output encoding/escaping and implementing a Content Security Policy (CSP).",
                "Using HTTPS SSL certificates exclusively.",
                "Disabling all GET request query parameters.",
                "Storing user passwords using plaintext base64 encoding."
            ],
            "correct_index": 0
        }
    ]
}


def _match_question_bank_category(skill):
    s = skill.lower().strip()
    if any(k in s for k in ["python", "django", "flask", "fastapi", "pandas", "numpy"]):
        return "python"
    if any(k in s for k in ["react", "redux", "next.js", "frontend", "vue"]):
        return "react"
    if any(k in s for k in ["typescript", "ts"]):
        return "typescript"
    if any(k in s for k in ["javascript", "js", "node", "express", "ecmascript"]):
        return "javascript"
    if any(k in s for k in ["sql", "postgres", "mysql", "database", "sqlite", "relational"]):
        return "sql"
    if any(k in s for k in ["git", "github", "gitlab", "version control"]):
        return "git"
    if any(k in s for k in ["docker", "kubernetes", "devops", "container", "ci/cd"]):
        return "docker"
    if any(k in s for k in ["html", "css", "tailwind", "sass", "web design"]):
        return "html"
    if any(k in s for k in ["algorithm", "data structure", "dsa", "tree", "graph", "sorting"]):
        return "data structures"
    return "general"


def _build_parametric_question(skill, angle_index):
    """
    Generates varied, high-quality technical questions for arbitrary or custom skills.
    Uses 10 distinct technical angles to prevent any repeated phrasing.
    """
    angles = [
        (
            f"When building production applications with {skill}, what is the recommended practice for managing configuration and sensitive secrets?",
            [
                "Load credentials from environment variables or a dedicated secrets manager rather than hardcoding them in source control.",
                "Hardcode secrets directly in public client-side repository files for easy deployment.",
                "Store sensitive access keys in unencrypted client browser cookies.",
                "Commit `.env` configuration files containing live production API keys into Git."
            ]
        ),
        (
            f"What is the most effective strategy for diagnosing unexpected performance bottlenecks when working with {skill}?",
            [
                "Use profiling and APM telemetry tools to measure execution times and resource consumption before refactoring.",
                "Guess the slowest function and immediately rewrite it in assembly without measuring.",
                "Restart the server periodically to mask memory leaks without investigation.",
                "Disable all logging and error monitoring to reduce CPU cycles."
            ]
        ),
        (
            f"In the context of {skill}, how should asynchronous operations and concurrent tasks be handled safely?",
            [
                "Use structured concurrency, promise/future handling, and proper thread/coroutine synchronization to prevent race conditions.",
                "Run all concurrent tasks on a single unmonitored global thread without error handling.",
                "Avoid using timeouts and let stuck requests hang indefinitely.",
                "Disable data locks and mutexes to maximize raw write speed regardless of race conditions."
            ]
        ),
        (
            f"When implementing automated testing for a module built with {skill}, what approach ensures resilient test coverage?",
            [
                "Write isolated unit tests for core business logic and mock external network and database dependencies.",
                "Test only the happy path and skip edge cases, invalid inputs, and error boundaries.",
                "Rely solely on manual ad-hoc browser clicking without automated test suites.",
                "Delete failing tests whenever code changes cause test assertions to break."
            ]
        ),
        (
            f"What design principle in {skill} minimizes tight coupling and enhances code maintainability across large codebases?",
            [
                "Dependency Inversion and Separation of Concerns using clear modular interfaces.",
                "Placing all business logic, database queries, and UI rendering inside a single monolithic file.",
                "Using global mutable state across all functions without parameter passing.",
                "Duplicating code across modules instead of creating reusable abstractions."
            ]
        ),
        (
            f"When scaling an application utilizing {skill} under high user traffic, which caching strategy is most beneficial?",
            [
                "Implement a distributed in-memory cache (such as Redis) for frequent read-heavy operations with appropriate TTL expiration.",
                "Disable all caching layers so every request hits the primary transactional database directly.",
                "Cache dynamic user authentication tokens permanently without expiration.",
                "Store unbounded cache entries in memory without any LRU eviction policy."
            ]
        ),
        (
            f"How should unhandled errors and exceptions be managed in a robust {skill} service?",
            [
                "Catch errors at appropriate boundaries, log structured error context, and return meaningful sanitized messages to clients.",
                "Catch all exceptions with empty `except` / `catch` blocks to silently swallow runtime crashes.",
                "Expose internal database stack traces directly to end users in HTTP 500 responses.",
                "Terminate the entire operating system process on every validation error."
            ]
        ),
        (
            f"What is the primary benefit of enforcing strict linting and type checking in a {skill} project?",
            [
                "Catches type mismatches, syntax bugs, and code smells early during development before runtime deployment.",
                "Compresses the compiled binary to fit into smaller disk sectors.",
                "Automatically bypasses unit testing requirements for pull requests.",
                "Prevents other developers from contributing to the repository."
            ]
        ),
        (
            f"When integrating third-party dependencies and libraries in {skill}, what security practice is essential?",
            [
                "Pin dependency versions and run automated vulnerability scanning (e.g., audit/Dependabot) in CI pipelines.",
                "Install unknown packages from untrusted registries without verification.",
                "Never update dependencies even when critical zero-day vulnerabilities are disclosed.",
                "Grant all installed third-party libraries unrestricted root operating system permissions."
            ]
        ),
        (
            f"What is the standard procedure for handling database schema migrations in a {skill} backend service?",
            [
                "Use version-controlled migration scripts applied sequentially with rollback support.",
                "Manually modify production database tables using raw SQL queries during peak hours without backups.",
                "Drop and recreate all database tables whenever a column name changes.",
                "Avoid using foreign key constraints and indexes to simplify table structures."
            ]
        ),
    ]

    template_q, template_opts = angles[angle_index % len(angles)]
    
    # Shuffle options while tracking correct answer
    opts = list(template_opts)
    correct_val = opts[0]
    random.shuffle(opts)
    correct_idx = opts.index(correct_val)
    
    return {
        "skill": skill,
        "question": template_q,
        "options": opts,
        "correct_index": correct_idx,
    }


def _validate_question(q):
    """Validates that a question dictionary has proper keys, distinct options, and valid index."""
    if not isinstance(q, dict):
        return False
    question_text = str(q.get("question", "")).strip()
    skill_text = str(q.get("skill", "")).strip()
    options = q.get("options")
    correct_index = q.get("correct_index")

    if not question_text or len(question_text) < 10 or not skill_text:
        return False
    if not isinstance(options, list) or len(options) != 4:
        return False
    if len(set(str(opt).strip().lower() for opt in options if str(opt).strip())) != 4:
        return False
    if not isinstance(correct_index, int) or correct_index < 0 or correct_index > 3:
        return False
    return True


def _deduplicate_and_clean_questions(questions):
    """Removes duplicate or near-duplicate questions by question text."""
    seen_stems = set()
    cleaned = []
    for q in questions:
        if not _validate_question(q):
            continue
        # Normalize stem for duplicate detection
        stem = re.sub(r"[^a-z0-9]", "", q["question"].lower())
        if stem in seen_stems or len(stem) < 10:
            continue
        seen_stems.add(stem)
        cleaned.append({
            "skill": str(q["skill"]).strip(),
            "question": str(q["question"]).strip(),
            "options": [str(opt).strip() for opt in q["options"]],
            "correct_index": int(q["correct_index"]),
        })
    return cleaned


def _supplement_questions(existing_questions, skills, target_count=20):
    """
    Fills missing question slots up to target_count with curated bank and parametric questions.
    Guarantees no duplicate questions and wide skill coverage.
    """
    results = list(existing_questions)
    seen_stems = set(re.sub(r"[^a-z0-9]", "", q["question"].lower()) for q in results)
    
    skills = _sanitize_skills(skills)
    skill_cycle_index = 0

    # 1. First pass: Pull from curated question bank for candidate skills
    for skill in skills:
        if len(results) >= target_count:
            break
        cat = _match_question_bank_category(skill)
        bank_items = _QUESTION_BANK.get(cat, [])
        for item in bank_items:
            if len(results) >= target_count:
                break
            stem = re.sub(r"[^a-z0-9]", "", item["question"].lower())
            if stem in seen_stems:
                continue
            
            # Shuffle options to avoid predictable answers
            opts = list(item["options"])
            correct_val = opts[item["correct_index"]]
            random.shuffle(opts)
            correct_idx = opts.index(correct_val)
            
            q_obj = {
                "skill": skill,
                "question": item["question"],
                "options": opts,
                "correct_index": correct_idx,
            }
            seen_stems.add(stem)
            results.append(q_obj)

    # 2. Second pass: Pull from general and data structure banks if still needed
    general_pool = _QUESTION_BANK.get("general", []) + _QUESTION_BANK.get("data structures", [])
    for item in general_pool:
        if len(results) >= target_count:
            break
        stem = re.sub(r"[^a-z0-9]", "", item["question"].lower())
        if stem in seen_stems:
            continue
        opts = list(item["options"])
        correct_val = opts[item["correct_index"]]
        random.shuffle(opts)
        correct_idx = opts.index(correct_val)
        
        assigned_skill = skills[skill_cycle_index % len(skills)]
        skill_cycle_index += 1
        
        q_obj = {
            "skill": assigned_skill,
            "question": item["question"],
            "options": opts,
            "correct_index": correct_idx,
        }
        seen_stems.add(stem)
        results.append(q_obj)

    # 3. Third pass: Generate distinct parametric questions across varied angles
    angle_offset = 0
    while len(results) < target_count:
        assigned_skill = skills[skill_cycle_index % len(skills)]
        skill_cycle_index += 1
        q_obj = _build_parametric_question(assigned_skill, angle_offset)
        angle_offset += 1
        
        stem = re.sub(r"[^a-z0-9]", "", q_obj["question"].lower())
        if stem in seen_stems:
            continue
        seen_stems.add(stem)
        results.append(q_obj)

    return results[:target_count]


def generate_assessment(skills, context="", owner_id=None, supabase=None):
    """
    Generates a 20-question multiple choice skill assessment based on candidate skills.
    Ensures 0 duplicate questions, balanced skill distribution, and robust fallback handling.
    """
    clean_skills = _sanitize_skills(skills)

    if owner_id and supabase and context:
        try:
            context = retrieve_context(
                owner_id,
                "Generate technical assessment questions from this candidate resume",
                context,
                supabase,
            )
        except Exception as exc:
            logger.debug("Assessment RAG retrieval notice: %s", exc)

    extracted_questions = []

    try:
        llm = get_llm(timeout=60)
        chain = _GEN_PROMPT | llm
        response = chain.invoke({
            "skills": ", ".join(clean_skills),
            "context": context or "Candidate skills: " + ", ".join(clean_skills),
        })
        raw = _strip_code_fences(extract_llm_text(response.content))
        data = json.loads(raw)
        raw_list = data.get("questions", []) if isinstance(data, dict) else []
        extracted_questions = _deduplicate_and_clean_questions(raw_list)
    except Exception as exc:
        logger.warning("LLM assessment generation error, using curated question pool: %s", exc)
        extracted_questions = []

    # If LLM returned fewer than 20 distinct questions or failed, supplement intelligently
    final_questions = _supplement_questions(extracted_questions, clean_skills, target_count=20)
    return final_questions


def grade_assessment(questions, answers):
    breakdown = {}
    responses = []

    for q in questions:
        qid = q["id"]
        skill = q["skill_tag"]
        selected = answers.get(str(qid))
        is_correct = selected is not None and int(selected) == q["correct_option"]

        breakdown.setdefault(skill, {"correct": 0, "total": 0})
        breakdown[skill]["total"] += 1
        if is_correct:
            breakdown[skill]["correct"] += 1

        responses.append({"question_id": qid, "selected_option": selected, "is_correct": is_correct})

    total_correct = sum(r["is_correct"] for r in responses)
    score_pct = round((total_correct / len(questions)) * 100, 1) if questions else 0.0

    return {"score_pct": score_pct, "breakdown": breakdown, "responses": responses}


def generate_strengths_weaknesses(breakdown):
    formatted = "\n".join(f"- {skill}: {v['correct']}/{v['total']}" for skill, v in breakdown.items())
    try:
        llm = get_llm(timeout=30)
        chain = _SUMMARY_PROMPT | llm
        response = chain.invoke({"breakdown": formatted})
        raw = _strip_code_fences(extract_llm_text(response.content))
        result = json.loads(raw)
    except Exception:
        return _fallback_summary(breakdown)

    return result if isinstance(result, dict) and (result.get("validated_skills") or result.get("strengths")) else _fallback_summary(breakdown)


def _fallback_summary(breakdown):
    validated = [skill for skill, values in breakdown.items() if values["correct"] / max(values["total"], 1) >= 0.7]
    weaknesses = [skill for skill, values in breakdown.items() if skill not in validated]
    technical = round(sum(value["correct"] for value in breakdown.values()) / max(sum(value["total"] for value in breakdown.values()), 1) * 100, 1) if breakdown else 0.0
    return {
        "strengths": validated,
        "weaknesses": weaknesses,
        "validated_skills": validated,
        "technical_pct": technical,
        "communication_pct": technical,
        "problem_solving_pct": technical,
    }
