"""Ajusta sys.path para localizar o pacote Python airllm (incl. instalações editáveis via .pth)."""
from __future__ import annotations

import glob
import importlib
import platform
import sys
from pathlib import Path
from typing import List, Optional, Tuple

_last_inserted_paths: List[str] = []
_configured_packages_path: Optional[str] = None


def _has_airllm_package(site_or_parent: Path) -> bool:
    """Verifica se existe um pacote importável airllm diretamente sob este diretório."""
    try:
        for child in site_or_parent.iterdir():
            if not child.is_dir():
                continue
            if child.name.lower() != "airllm":
                continue
            init_py = child / "__init__.py"
            if init_py.is_file():
                return True
            # namespace ou só .pyd — aceita pasta airllm com algum .py
            if any(child.glob("*.py")):
                return True
    except OSError:
        pass
    return False


def _find_airllm_parent_walk_up(start: Path, max_up: int = 8) -> Optional[Path]:
    cur = start
    for _ in range(max_up):
        if _has_airllm_package(cur):
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _find_airllm_shallow_search(root: Path, max_depth: int = 4, max_visits: int = 400) -> Optional[Path]:
    """Busca limitada por site-packages com airllm (ancestral do venv ou pasta ampla)."""
    budget = [max_visits]

    def scan(d: Path, depth: int) -> Optional[Path]:
        if depth > max_depth:
            return None
        budget[0] -= 1
        if budget[0] < 0:
            return None
        try:
            if _has_airllm_package(d):
                return d
            for sub in d.iterdir():
                if not sub.is_dir() or sub.name.startswith("."):
                    continue
                found = scan(sub, depth + 1)
                if found is not None:
                    return found
        except OSError:
            pass
        return None

    try:
        # Evita varrer pastas enormes (ex.: raiz do usuário com milhares de itens)
        try:
            n = sum(1 for _ in root.iterdir())
            if n > 64:
                return None
        except OSError:
            return None
        return scan(root.resolve(), 0)
    except OSError:
        return None


def resolve_airllm_site_packages(user_path: str) -> Optional[Path]:
    """
    Resolve até o diretório que deve entrar no sys.path (pai do pacote airllm).

    Aceita site-packages, pasta airllm, raiz do venv, ou pastas acima (sobe diretórios).
    """
    raw = (user_path or "").strip()
    if not raw:
        return None

    try:
        p = Path(raw).expanduser().resolve()
    except OSError:
        return None
    if not p.is_dir():
        return None

    # Usuário selecionou .../site-packages/airllm
    if p.name.lower() == "airllm" and p.parent.is_dir():
        if _has_airllm_package(p.parent):
            return p.parent

    if _has_airllm_package(p):
        return p

    if platform.system() == "Windows":
        cand = p / "Lib" / "site-packages"
        if cand.is_dir() and _has_airllm_package(cand):
            return cand
    else:
        for lib in p.glob("lib/python*/site-packages"):
            if lib.is_dir() and _has_airllm_package(lib):
                return lib
        for lib in glob.glob(str(p / "lib" / "python*" / "site-packages")):
            lp = Path(lib)
            if lp.is_dir() and _has_airllm_package(lp):
                return lp

    up = _find_airllm_parent_walk_up(p)
    if up is not None:
        return up

    shallow = _find_airllm_shallow_search(p, max_depth=4)
    if shallow is not None:
        return shallow

    return None


def _parse_pth_file(pth_file: Path, site_packages: Path, out: List[Path]) -> None:
    try:
        text = pth_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("import "):
            continue
        path = Path(line)
        if not path.is_absolute():
            path = (site_packages / path).resolve()
        else:
            path = path.resolve()
        if path.is_dir():
            out.append(path)


def _parse_egg_link(egg_link: Path, out: List[Path]) -> None:
    try:
        first = egg_link.read_text(encoding="utf-8", errors="ignore").splitlines()[0].strip()
    except (OSError, IndexError):
        return
    path = Path(first)
    if path.is_dir():
        out.append(path.resolve())


def collect_editable_and_pth_paths(site_packages: Path) -> List[Path]:
    """
    Caminhos extras declarados em .pth e .egg-link dentro de site-packages.
    Necessário porque o Python só aplica .pth na inicialização — não após mudar sys.path em runtime.
    """
    extra: List[Path] = []
    if not site_packages.is_dir():
        return extra
    try:
        for item in site_packages.iterdir():
            if item.name.startswith("."):
                continue
            if item.suffix == ".pth" and item.is_file():
                _parse_pth_file(item, site_packages, extra)
            elif item.suffix == ".egg-link" and item.is_file():
                _parse_egg_link(item, extra)
    except OSError:
        pass
    seen: set[str] = set()
    unique: List[Path] = []
    for ep in extra:
        s = str(ep)
        if s not in seen:
            seen.add(s)
            unique.append(ep)
    return unique


def _remove_tracked_from_syspath() -> None:
    for sp in _last_inserted_paths:
        while sp in sys.path:
            try:
                sys.path.remove(sp)
            except ValueError:
                break


def apply_airllm_packages_path(user_path: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Insere no sys.path o site-packages e caminhos de instalação editável (.pth / .egg-link).

    Retorna (ok, caminho site-packages resolvido ou None).
    """
    global _last_inserted_paths

    _remove_tracked_from_syspath()
    _last_inserted_paths = []

    if not user_path or not str(user_path).strip():
        importlib.invalidate_caches()
        return False, None

    resolved = resolve_airllm_site_packages(str(user_path))
    if not resolved:
        importlib.invalidate_caches()
        return False, None

    extras = collect_editable_and_pth_paths(resolved)

    # Ordem: primeiro os caminhos do .pth (código font do editable), depois site-packages.
    for ep in reversed(extras):
        s = str(ep)
        if s not in sys.path:
            sys.path.insert(0, s)
        _last_inserted_paths.append(s)

    sp = str(resolved)
    if sp not in sys.path:
        sys.path.insert(0, sp)
    _last_inserted_paths.append(sp)

    importlib.invalidate_caches()
    return True, sp


def set_airllm_packages_path(user_path: Optional[str]) -> Tuple[bool, Optional[str]]:
    global _configured_packages_path
    _configured_packages_path = (user_path or "").strip() or None
    return apply_airllm_packages_path(_configured_packages_path)


def ensure_airllm_path() -> None:
    apply_airllm_packages_path(_configured_packages_path)


def try_import_airllm() -> Tuple[bool, Optional[str]]:
    """
    Tenta importar airllm após aplicar o path configurado.
    Retorna (sucesso, mensagem de erro ou None).
    """
    ensure_airllm_path()
    try:
        import airllm  # noqa: F401
        return True, None
    except ImportError as e:
        return False, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
