"""Translate Python comments and docstrings to English."""

import argparse
import ast
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
import difflib
import io
import json
import os
import re
import shutil
import time
import tokenize
from pathlib import Path

try:
    import requests
except ImportError as exc:  # pragma: no cover
    raise SystemExit("The 'requests' package is required: uv add requests") from exc

IGNORE_DIRS = {
    ".venv", "venv", "env", "__pycache__", ".git", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", "node_modules", ".tox",
}

PT_CORE = {
    "de", "da", "do", "dos", "das", "em", "no", "na", "nos", "nas", "para", "com", "por",
    "que", "uma", "um", "os", "as", "nao", "não", "mais", "mas", "ou", "e", "se", "ao",
    "aos", "num", "numa", "neste", "nesta", "deste", "desta", "desse", "dessa", "isso",
    "isto", "esse", "essa", "este", "esta", "cada", "todo", "toda", "todos", "todas",
    "outro", "outra", "outros", "outras", "mesmo", "mesma", "mesmos", "mesmas", "muito",
    "muita", "muitos", "muitas", "pouco", "pouca", "poucos", "poucas", "abaixo", "acima",
    "ate", "até", "entre", "sobre", "como", "quando", "onde", "qual", "quais", "porque",
    "pois", "tambem", "também", "apenas", "somente", "apos", "após", "antes", "durante",
    "sem", "sob", "contra", "perante", "mediante", "segundo", "conforme", "consoante",
}

PT_ACCENTS = "áéíóúãõàèìòêâôçÁÉÍÓÚÃÕÀÈÌÒÊÂÔÇ"


def is_portuguese(text: str) -> bool:
    if any(ch in PT_ACCENTS for ch in text):
        return True
    tokens = re.findall(r"[A-Za-zÀ-ÿ']+", text.lower())
    return any(tok in PT_CORE for tok in tokens)


def chunk_text(text: str, max_len: int = 450) -> list[str]:
    if len(text) <= max_len:
        return [text]
    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    current = ""
    for line in lines:
        if current and len(current) + len(line) > max_len:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    result: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_len:
            result.append(chunk)
            continue
        words = chunk.split(" ")
        cur = ""
        for word in words:
            if cur and len(cur) + len(word) + 1 > max_len:
                result.append(cur)
                cur = word
            else:
                cur = (cur + " " + word).strip() if cur else word
        if cur:
            result.append(cur)
    return result

class Translator:
    def __init__(self, provider, source, target, email, cache, delay):
        self.provider = provider
        self.source = source
        self.target = target
        self.email = email
        self.cache = cache
        self.delay = delay

    def translate(self, text: str) -> str:
        if text is None or text.strip() == "":
            return text
        if text in self.cache:
            return self.cache[text]
        if self.source == self.target:
            self.cache[text] = text
            return text
        if self.source.startswith("pt") and not is_portuguese(text):
            self.cache[text] = text
            return text

        chunks = chunk_text(text)
        out: list[str] = []
        for chunk in chunks:
            if chunk in self.cache:
                out.append(self.cache[chunk])
                continue
            translated = self._translate_chunk(chunk)
            self.cache[chunk] = translated
            out.append(translated)
            if self.delay:
                time.sleep(self.delay)
        result = "".join(out)
        self.cache[text] = result
        return result

    def _translate_chunk(self, text: str) -> str:
        if self.provider == "mymemory":
            return self._mymemory(text)
        if self.provider == "openai":
            return self._openai(text)
        if self.provider == "anthropic":
            return self._anthropic(text)
        if self.provider == "ollama":
            return self._ollama(text)
        raise ValueError(f"Unknown provider: {self.provider}")

    def _mymemory(self, text: str) -> str:
        url = "https://api.mymemory.translated.net/get"
        params = {"q": text, "langpair": f"{self.source}|{self.target}"}
        if self.email:
            params["de"] = self.email
        for attempt in range(4):
            try:
                resp = requests.get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    time.sleep((2 ** attempt) + 1)
                    continue
                resp.raise_for_status()
                data = resp.json()
                if data.get("quotaFinished"):
                    raise RuntimeError("MyMemory daily quota finished")
                translated = data.get("responseData", {}).get("translatedText")
                if not translated or str(translated).startswith("PLEASE SELECT"):
                    return text
                return translated
            except Exception as exc:
                if attempt == 3:
                    print(f"    [warn] translation failed: {exc}")
                    return text
                time.sleep(1 + attempt)
        return text

    def _openai(self, text: str) -> str:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise SystemExit("openai package not installed") from exc
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise SystemExit("OPENAI_API_KEY is not set")
        client = OpenAI(api_key=api_key)
        prompt = (
            "Translate the following text from Portuguese to English. "
            "Preserve formatting, newlines, indentation, and technical terms. "
            "Return only the translated text.\n\n" + text
        )
        resp = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        return resp.choices[0].message.content or text

    def _anthropic(self, text: str) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise SystemExit("anthropic package not installed") from exc
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise SystemExit("ANTHROPIC_API_KEY is not set")
        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            "Translate the following text from Portuguese to English. "
            "Preserve formatting, newlines, indentation, and technical terms. "
            "Return only the translated text.\n\n" + text
        )
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text if resp.content else text

    def _ollama(self, text: str) -> str:
        import json as _json
        import urllib.request

        url = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
        model = os.environ.get("OLLAMA_MODEL", "llama3.1")
        prompt = (
            "Translate the following text from Portuguese to English. "
            "Preserve formatting, newlines, indentation, and technical terms. "
            "Return only the translated text.\n\n" + text
        )
        data = _json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = _json.loads(resp.read().decode())
        return payload.get("response", text).strip() or text

TRANSLATOR = None


def is_docstring_expr(node) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def pos_to_offset(lines: list[str], row: int, col: int) -> int:
    offset = 0
    for i in range(row - 1):
        offset += len(lines[i])
    return offset + col


def translate_comment(original: str) -> str:
    global TRANSLATOR
    assert TRANSLATOR is not None
    if not original.startswith("#"):
        return original
    after = original[1:]
    leading = after[: len(after) - len(after.lstrip())]
    content = after[len(leading):]
    content_core = content.rstrip()
    trailing = content[len(content_core):]
    if content_core.strip() == "":
        return original
    translated_core = TRANSLATOR.translate(content_core)
    if translated_core == content_core:
        return original
    return "#" + leading + translated_core + trailing


def translate_docstring_snippet(snippet: str) -> str:
    global TRANSLATOR
    assert TRANSLATOR is not None
    match = re.match(r"^([a-zA-Z]*)('''|\"\"\"|'|\"')", snippet)
    if not match:
        return snippet
    prefix = match.group(1)
    quote = match.group(2)
    if len(snippet) < len(prefix) + len(quote) * 2:
        return snippet
    inner = snippet[len(prefix) + len(quote) : len(snippet) - len(quote)]
    translated_inner = TRANSLATOR.translate(inner)
    if translated_inner == inner:
        return snippet
    return prefix + quote + translated_inner + quote


def collect_edits(source: str) -> list[tuple[int, int, str]]:
    global TRANSLATOR
    assert TRANSLATOR is not None
    edits: list[tuple[int, int, str]] = []
    lines = source.splitlines(keepends=True)

    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                start = pos_to_offset(lines, *tok.start)
                end = pos_to_offset(lines, *tok.end)
                new_text = translate_comment(tok.string)
                if new_text != tok.string:
                    edits.append((start, end, new_text))
    except tokenize.TokenError as exc:
        print(f"    [warn] tokenizer stopped early: {exc}")

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        print(f"    [warn] syntax error, skipping docstrings: {exc}")
        return edits

    doc_nodes = []
    if tree.body and is_docstring_expr(tree.body[0]):
        doc_nodes.append(tree.body[0])
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and is_docstring_expr(node.body[0]):
                doc_nodes.append(node.body[0])

    for expr in doc_nodes:
        value_node = expr.value
        start = pos_to_offset(lines, value_node.lineno, value_node.col_offset)
        end = pos_to_offset(lines, value_node.end_lineno, value_node.end_col_offset)
        original_snippet = source[start:end]
        new_snippet = translate_docstring_snippet(original_snippet)
        if new_snippet != original_snippet:
            edits.append((start, end, new_snippet))

    return edits

def apply_edits(source: str, edits: list[tuple[int, int, str]]) -> str:
    result = source
    for start, end, new_text in sorted(edits, key=lambda item: -item[0]):
        result = result[:start] + new_text + result[end:]
    return result


def process_file(path: str, args, cache_path: str) -> str:
    global TRANSLATOR
    with tokenize.open(path) as f:
        source = f.read()

    edits = collect_edits(source)
    if not edits:
        return "no-changes"

    new_source = apply_edits(source, edits)
    if new_source == source:
        return "no-changes"

    rel = os.path.relpath(path, args.root)
    diff = difflib.unified_diff(
        source.splitlines(keepends=True),
        new_source.splitlines(keepends=True),
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
    )
    diff_text = "".join(diff)

    if args.dry_run:
        print(diff_text)
        return "dry-run"

    if args.backup:
        backup_path = path + ".bak"
        if not os.path.exists(backup_path):
            shutil.copy2(path, backup_path)

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new_source)

    return "updated"


def load_cache(cache_path: str) -> dict:
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_cache(cache_path: str, cache: dict) -> None:
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cache_path)


def main() -> None:
    global TRANSLATOR
    parser = argparse.ArgumentParser(description="Translate Python comments and docstrings to English.")
    parser.add_argument("--root", default=".", help="Project root to scan.")
    parser.add_argument("--dry-run", action="store_true", help="Print diffs without writing files.")
    parser.add_argument("--backup", action="store_true", help="Create .bak backups before writing.")
    parser.add_argument(
        "--provider",
        default="mymemory",
        choices=["mymemory", "openai", "anthropic", "ollama"],
        help="Translation backend.",
    )
    parser.add_argument("--source-lang", default="pt", help="Source language code.")
    parser.add_argument("--target-lang", default="en", help="Target language code.")
    parser.add_argument("--email", default="autonomous-vision-engine@example.com", help="Contact email for MyMemory quota.")
    parser.add_argument("--cache", default=".translation_cache.json", help="Cache file path.")
    parser.add_argument("--delay", type=float, default=0.15, help="Delay between API calls (seconds).")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    args.root = root

    cache_path = args.cache
    if not os.path.isabs(cache_path):
        cache_path = os.path.join(root, cache_path)

    cache = load_cache(cache_path)
    TRANSLATOR = Translator(
        provider=args.provider,
        source=args.source_lang,
        target=args.target_lang,
        email=args.email,
        cache=cache,
        delay=args.delay,
    )

    files: list[Path] = []
    for path in sorted(Path(root).rglob("*.py")):
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.name == "translate_comments.py":
            continue
        files.append(path)

    print(f"Found {len(files)} Python files under {root}")

    updated = 0
    skipped = 0
    for path in files:
        print(f"Processing: {path}")
        try:
            status = process_file(str(path), args, cache_path)
        except Exception as exc:
            print(f"    [error] {exc}")
            status = "error"
        if status == "updated":
            updated += 1
        elif status == "dry-run":
            pass
        else:
            skipped += 1
        save_cache(cache_path, cache)

    print(f"\nDone. Updated: {updated}, skipped/no-changes: {skipped}")


if __name__ == "__main__":
    main()