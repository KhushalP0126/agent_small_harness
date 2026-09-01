from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable
from importlib import metadata
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agents.library_doc_search import DocumentationSearchResult


class KernelLibraryDocumentationSearchAgent:
    """Search and verify library documentation in a Kernel cloud browser."""

    name = "agent-kernel-library-documentation-search"

    def __init__(
        self,
        kernel_client: Any | None = None,
        metadata_candidates: Any | None = None,
    ) -> None:
        if kernel_client is None:
            try:
                Kernel = _load_kernel_sdk().Kernel
            except ImportError as exc:
                raise RuntimeError(
                    "Kernel documentation search requires the optional `kernel` package. "
                    "Run `make setup-browser` and set KERNEL_API_KEY."
                ) from exc
            api_key = _configured_kernel_api_key()
            if not api_key:
                raise RuntimeError("Set KERNEL_API_KEY in .env before using Kernel documentation search.")
            kernel_client = Kernel(api_key=api_key)
        self.kernel = kernel_client
        self.metadata_candidates = metadata_candidates or _metadata_candidates
        self.provider = "kernel"
        self.model = "kernel-browser"

    def search(
        self,
        library: str,
        public_symbols: list[str] | None = None,
        *,
        language: str = "python",
    ) -> DocumentationSearchResult:
        symbols = [symbol for symbol in (public_symbols or [])[:20] if symbol]
        query = f"{library} official documentation"
        browser = None
        try:
            candidates = self.metadata_candidates(library)
            if not candidates:
                raise ValueError(f"No PyPI documentation candidates found for {library}")
            browser = self.kernel.browsers.create()
            response = self.kernel.browsers.playwright.execute(
                id=browser.session_id,
                code=_browser_search_code(library, symbols, candidates),
            )
            payload = _response_result(response)
            documentation = _verified_documentation(payload, library, symbols)
            if not documentation:
                raise ValueError("Kernel browser found no verified documentation pages")
            return DocumentationSearchResult(
                provider=self.provider,
                model=self.model,
                searched_by_model=False,
                query=query,
                documentation=documentation,
                documented_api=[],
                raw_response=json.dumps(payload, sort_keys=True),
            )
        except Exception as exc:  # noqa: BLE001 - persisted as reviewable discovery metadata
            return DocumentationSearchResult(
                provider=self.provider,
                model=self.model,
                searched_by_model=False,
                query=query,
                documentation=[],
                documented_api=[],
                error=f"{exc.__class__.__name__}: {exc}",
            )
        finally:
            if browser is not None:
                self.kernel.browsers.delete_by_id(browser.session_id)

    def syntax_notes(
        self,
        library: str,
        public_symbols: list[str] | None = None,
        documentation: list[dict] | None = None,
        *,
        language: str = "python",
    ) -> str:
        symbols = ", ".join((public_symbols or [])[:40]) or "none"
        lines = [
            f"# {library} ({language}) Syntax and Documentation Notes",
            "",
            "Documentation was discovered and verified with a Kernel browser.",
            "",
            "## Public Symbols",
            "",
            symbols,
            "",
            "## Verified Documentation",
            "",
        ]
        for item in documentation or []:
            title = item.get("title", "Untitled documentation")
            url = item.get("url", "")
            note = item.get("note", "Verified page text mentions the requested library.")
            lines.append(f"- [{title}]({url}) - {note}")
        if not documentation:
            lines.append("- No verified documentation pages were returned.")
        return "\n".join(lines).rstrip() + "\n"


def _browser_search_code(
    library: str,
    public_symbols: list[str],
    candidates: list[dict[str, str]],
) -> str:
    symbols_json = json.dumps(public_symbols)
    library_json = json.dumps(library.lower())
    candidates_json = json.dumps(candidates)
    return f"""
const library = {library_json};
const libraryRoot = library.split('.')[0];
const symbols = {symbols_json};
const candidates = {candidates_json};
const documentation = [];
for (const candidate of candidates) {{
  try {{
    await page.goto(candidate.url, {{ waitUntil: 'domcontentloaded', timeout: 15000 }});
    const text = (await page.locator('body').innerText()).slice(0, 6000);
    const lower = text.toLowerCase();
    const verified = lower.includes(library) || lower.includes(libraryRoot) || symbols.some((symbol) => lower.includes(symbol.toLowerCase()));
    if (verified) {{
      documentation.push({{
        title: candidate.title || await page.title(),
        url: page.url(),
        note: `Verified allowed-source page text for ${{library}}.`,
        text_excerpt: text.slice(0, 500),
      }});
    }}
  }} catch (error) {{
    // Candidate pages can be unavailable or script-heavy.
  }}
  if (documentation.length >= 5) break;
}}
return {{ documentation }};
"""


def _load_kernel_sdk() -> Any:
    """Load the external SDK without colliding with this repo's kernel package."""
    module_name = "_kernel_sdk"
    if module_name in sys.modules:
        return sys.modules[module_name]
    try:
        package_root = Path(metadata.distribution("kernel").locate_file("kernel"))
    except metadata.PackageNotFoundError as exc:
        raise ImportError("The external Kernel SDK distribution is not installed") from exc
    init_path = package_root / "__init__.py"
    spec = spec_from_file_location(
        module_name,
        init_path,
        submodule_search_locations=[str(package_root)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load the external Kernel SDK from {init_path}")
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _configured_kernel_api_key() -> str:
    value = os.environ.get("KERNEL_API_KEY", "").strip()
    if value:
        return value
    env_path = Path(".env")
    if not env_path.exists():
        return ""
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if not line.startswith("KERNEL_API_KEY="):
            continue
        value = line.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value.strip()
    return ""


def _response_result(response: Any) -> dict[str, Any]:
    result = getattr(response, "result", response)
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict):
        raise ValueError("Kernel browser response did not contain an object result")
    return result


def _verified_documentation(
    payload: dict[str, Any],
    library: str,
    public_symbols: list[str],
) -> list[dict]:
    entries = payload.get("documentation", [])
    if not isinstance(entries, list):
        raise ValueError("Kernel browser response did not contain a documentation list")
    normalized: list[dict] = []
    library_lower = library.lower()
    library_root = library_lower.split(".", 1)[0]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title", "")).strip()
        url = str(entry.get("url", "")).strip()
        note = str(entry.get("note", "")).strip()
        text = str(entry.get("text_excerpt", "")).lower()
        verified = (
            library_lower in text
            or library_root in text
            or any(symbol.lower() in text for symbol in public_symbols)
        )
        if title and _allowed_documentation_url(url) and verified:
            normalized.append({"title": title, "url": url, "note": note})
    return normalized


ALLOWED_DOCUMENTATION_DOMAINS = (
    "clang.llvm.org",
    "docs.python.org",
    "llvm.org",
    "pypi.org",
    "readthedocs.io",
    "github.com",
)


def _allowed_documentation_url(url: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme in {"http", "https"} and any(
        host == domain or host.endswith(f".{domain}")
        for domain in ALLOWED_DOCUMENTATION_DOMAINS
    )


def _metadata_candidates(library: str) -> list[dict[str, str]]:
    names = list(dict.fromkeys((library, library.split(".", 1)[0], library.rsplit(".", 1)[-1])))
    candidates: list[dict[str, str]] = []
    for package_name in names:
        payload = _pypi_metadata(package_name)
        if not payload:
            continue
        info = payload.get("info", {})
        if not isinstance(info, dict):
            continue
        project_urls = info.get("project_urls", {})
        urls: Iterable[tuple[str, str]] = project_urls.items() if isinstance(project_urls, dict) else []
        metadata_urls: list[tuple[str, str]] = list(urls)
        for field_name in ("home_page", "docs_url", "download_url"):
            field_url = str(info.get(field_name, "")).strip()
            if field_url:
                metadata_urls.append((field_name, field_url))
        for label, url in metadata_urls:
            value = str(url).strip()
            label_text = str(label).lower()
            if value and any(term in label_text for term in ("doc", "home", "source", "repository", "github")):
                if _allowed_documentation_url(value):
                    candidates.append({"title": f"{info.get('name', package_name)} {label}", "url": value})
        pypi_url = f"https://pypi.org/project/{package_name}/"
        candidates.append({"title": f"PyPI {info.get('name', package_name)}", "url": pypi_url})
        if candidates:
            break
    return list({item["url"]: item for item in candidates}.values())[:8]


def _pypi_metadata(package_name: str) -> dict[str, Any] | None:
    request = Request(
        f"https://pypi.org/pypi/{package_name}/json",
        headers={"User-Agent": "agent-small-harness-library-discovery/1.0"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
